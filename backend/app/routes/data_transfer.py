import json
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.background import BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from backend.app.db import database_connection
from backend.app.scheduler import MAX_SCRIPT_CHARS, MAX_TIMEOUT_SECONDS, CronSchedule, SchedulerError, validate_script

router = APIRouter(prefix="/api/data", tags=["data transfer"])


class ImportPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format_version: int = Field(ge=1, le=2)
    secrets_included: bool = False
    models: list[dict] = Field(default_factory=list, max_length=500)
    agents: list[dict] = Field(max_length=500)
    sessions: list[dict] = Field(max_length=10000)
    messages: list[dict] = Field(max_length=100000)
    memories: list[dict] = Field(default_factory=list, max_length=10000)
    prompt_versions: list[dict] = Field(default_factory=list, max_length=10000)
    mcp_servers: list[dict] = Field(default_factory=list, max_length=1000)
    knowledge_bases: list[dict] = Field(default_factory=list, max_length=1000)
    knowledge_documents: list[dict] = Field(default_factory=list, max_length=100000)
    database_connections: list[dict] = Field(default_factory=list, max_length=1000)
    scheduled_tasks: list[dict] = Field(default_factory=list, max_length=10000)

def _portable_message_metadata(raw) -> str:
    # Attachment files live in the local app store, not in portable JSON backups.
    try:
        metadata = json.loads(raw)
        if isinstance(metadata, dict):
            metadata.pop('images', None)
            return json.dumps(metadata, ensure_ascii=True)
    except (TypeError, ValueError, RecursionError):
        pass
    return '{}'


@router.get("/export")
def export_data(request: Request):
    with database_connection(request.app.state.database_path) as connection:
        models = [dict(row) for row in connection.execute(
            "SELECT id,name,base_url,model,created_at,updated_at FROM model_configs ORDER BY rowid"
        ).fetchall()]
        agents = [dict(row) for row in connection.execute(
            "SELECT id,name,provider,model_config_id,system_prompt,mode,context_window,skill_directory,"
            "selected_skills_json,selected_agents_json,sandbox_code_enabled,max_rounds,selected_mcp_tools_json,selected_local_tools_json,selected_knowledge_bases_json,selected_database_connections_json,created_at,updated_at "
            "FROM agents ORDER BY rowid"
        ).fetchall()]
        for agent in agents:
            agent["selected_skills"] = json.loads(agent.pop("selected_skills_json") or "[]")
            agent["selected_agents"] = json.loads(agent.pop("selected_agents_json") or "[]")
            agent["sandbox_code_enabled"] = bool(agent["sandbox_code_enabled"])
            agent["selected_mcp_tools"] = json.loads(agent.pop("selected_mcp_tools_json") or "[]")
            agent["selected_local_tools"] = json.loads(agent.pop("selected_local_tools_json") or "[]")
            agent["selected_knowledge_bases"] = json.loads(agent.pop("selected_knowledge_bases_json") or "[]")
            agent["selected_database_connections"] = json.loads(agent.pop("selected_database_connections_json") or "[]")
        sessions = [dict(row) for row in connection.execute(
            "SELECT id,agent_id,title,parent_session_id,branched_from_message_id,created_at,updated_at FROM sessions ORDER BY rowid"
        ).fetchall()]
        messages = [dict(row) for row in connection.execute(
            "SELECT id,session_id,role,content,metadata_json,created_at FROM messages ORDER BY rowid"
        ).fetchall()]
        for message in messages:
            message['metadata_json'] = _portable_message_metadata(message['metadata_json'])
        memories = [dict(row) for row in connection.execute(
            "SELECT id,content,scope,agent_id,source,created_at,updated_at FROM memories ORDER BY rowid"
        ).fetchall()]
        prompts = [dict(row) for row in connection.execute(
            "SELECT id,agent_id,content,created_at FROM prompt_versions ORDER BY rowid"
        ).fetchall()]
        mcp_servers = [dict(row) for row in connection.execute(
            "SELECT id,name,transport,command,args_json,cwd,url,enabled,env_names_json,created_at FROM mcp_servers ORDER BY rowid"
        ).fetchall()]
        knowledge_bases = [dict(row) for row in connection.execute(
            "SELECT id,name,created_at,updated_at FROM knowledge_bases ORDER BY rowid"
        ).fetchall()]
        knowledge_documents = [dict(row) for row in connection.execute(
            "SELECT id,knowledge_base_id,filename,extension,extracted_text,char_count,status,error,created_at,updated_at "
            "FROM knowledge_documents ORDER BY rowid"
        ).fetchall()]
        database_configs = [dict(row) for row in connection.execute(
            "SELECT id,name,db_type,sqlite_path,host,port,database_name,username,ssl,readonly,created_at,updated_at "
            "FROM database_connections ORDER BY rowid"
        ).fetchall()]
        for config in database_configs:
            config["ssl"] = bool(config["ssl"])
            config["readonly"] = bool(config["readonly"])
        scheduled_tasks = [dict(row) for row in connection.execute(
            "SELECT id,name,script,cron_expression,timezone,timeout_seconds FROM scheduled_tasks ORDER BY rowid"
        ).fetchall()]

        for server in mcp_servers:
            server["args"] = json.loads(server.pop("args_json") or "[]")
            server["env_names"] = json.loads(server.pop("env_names_json") or "[]")
    return JSONResponse(
        {"format_version": 2, "models": models, "agents": agents, "sessions": sessions,
         "messages": messages, "memories": memories, "prompt_versions": prompts,
         "mcp_servers": mcp_servers, "knowledge_bases": knowledge_bases,
         "knowledge_documents": knowledge_documents, "database_connections": database_configs,
         "scheduled_tasks": scheduled_tasks,
         "secrets_included": False},
        headers={"Content-Disposition": "attachment; filename=agent-harness-backup.json"},
    )


@router.post("/preview")
def preview_import(payload: ImportPayload, request: Request):
    incoming_names = [str(server.get("name", "")).strip() for server in payload.mcp_servers]
    with database_connection(request.app.state.database_path) as connection:
        existing_names = {row[0] for row in connection.execute("SELECT name FROM mcp_servers")}
    conflicts = sorted({name for name in incoming_names if name in existing_names})
    warnings = []
    if payload.secrets_included:
        warnings.append("文件声明包含凭据，但出于安全原因不会导入任何密钥。")
    if payload.models or (payload.format_version == 1 and payload.agents):
        warnings.append("Model API 密钥不会包含在备份中；导入后需在 Model 页重新配置凭据并测试连接。")
    if payload.database_connections:
        warnings.append("备份中的数据库连接不包含密码；恢复后请重新设置凭据并测试。SQLite 文件路径需确认仍有效。")
    if payload.scheduled_tasks:
        warnings.append("恢复的定时任务会保持停用，需重新确认脚本版本后再启用或运行；运行历史不会恢复。")
    return {
        "models": len(payload.models),
        "agents": len(payload.agents),
        "sessions": len(payload.sessions),
        "messages": len(payload.messages),
        "memories": len(payload.memories),
        "mcp_servers": len(payload.mcp_servers),
        "knowledge_bases": len(payload.knowledge_bases),
        "knowledge_documents": len(payload.knowledge_documents),
        "database_connections": len(payload.database_connections),
        "scheduled_tasks": len(payload.scheduled_tasks),
        "mcp_name_conflicts": conflicts,
        "warnings": warnings,
        "secrets_imported": 0,
    }


@router.post("/import")
def import_data(payload: ImportPayload, request: Request, background_tasks: BackgroundTasks):
    agent_map: dict[str, str] = {}
    session_map: dict[str, str] = {}
    selected_mcp_by_agent: dict[str, list[str]] = {}
    selected_agents_by_agent: dict[str, list[str]] = {}
    mcp_server_map: dict[str, str] = {}
    model_map: dict[str, str] = {}
    database_map: dict[str, str] = {}
    imported_database_connections = 0
    imported_scheduled_tasks: list[dict] = []
    scheduled_task_map: dict[str, str] = {}
    knowledge_base_map: dict[str, str] = {}
    imported_knowledge_bases: list[dict] = []
    imported_database_configs: list[dict] = []
    imported_knowledge_documents = 0
    imported_model_count = 0
    import_warnings = ["导入的 Model 不包含 API 密钥；请在 Model 页重新配置凭据并测试连接。"] if payload.models or (payload.format_version == 1 and payload.agents) else []
    for agent in payload.agents:
        if not isinstance(agent.get("name"), str) or not agent["name"].strip():
            raise HTTPException(status_code=422, detail="导入文件中的智能体名称无效")
    try:
        with database_connection(request.app.state.database_path) as connection:
            for knowledge_base in payload.knowledge_bases:
                old_id = knowledge_base.get("id")
                name = knowledge_base.get("name")
                if (not isinstance(old_id, str) or not old_id or old_id in knowledge_base_map
                    or not isinstance(name, str) or not name.strip() or len(name) > 100):
                    raise HTTPException(status_code=422, detail="导入文件包含无效或重复的知识库")
                new_id = uuid4().hex
                knowledge_base_map[old_id] = new_id
                connection.execute(
                    "INSERT INTO knowledge_bases(id,name) VALUES(?,?)",
                    (new_id, name.strip()),
                )
                imported_knowledge_bases.append({"id": new_id, "name": name.strip()})

            document_ids: set[str] = set()
            for document in payload.knowledge_documents:
                old_id = document.get("id")
                old_knowledge_base_id = document.get("knowledge_base_id")
                filename = document.get("filename")
                extension = document.get("extension")
                extracted_text = document.get("extracted_text", "")
                status_value = document.get("status")
                error = document.get("error")
                if (not isinstance(old_id, str) or not old_id or old_id in document_ids
                    or old_knowledge_base_id not in knowledge_base_map
                    or not isinstance(filename, str) or not filename or len(filename) > 255
                    or extension not in {".md", ".markdown", ".txt", ".json", ".csv", ".pdf", ".docx"}
                    or not isinstance(extracted_text, str) or len(extracted_text) > 2_000_000
                    or status_value not in {"ready", "failed"}
                    or (status_value == "failed" and extracted_text)
                    or (error is not None and (not isinstance(error, str) or len(error) > 500))):
                    raise HTTPException(status_code=422, detail="导入文件包含无效的知识库文档")
                document_ids.add(old_id)
                connection.execute(
                    "INSERT INTO knowledge_documents(id,knowledge_base_id,filename,extension,stored_path,"
                    "extracted_text,char_count,status,error) VALUES(?,?,?,?,?,?,?,?,?)",
                    (uuid4().hex, knowledge_base_map[old_knowledge_base_id], filename,
                     extension, "", extracted_text, len(extracted_text), status_value, error),
                )
                imported_knowledge_documents += 1

            for database_config in payload.database_connections:
                old_id = database_config.get("id")
                name = database_config.get("name")
                db_type = database_config.get("db_type")
                sqlite_path = database_config.get("sqlite_path", "")
                host = database_config.get("host", "")
                port = database_config.get("port", 3306)
                database_name = database_config.get("database_name", "")
                username = database_config.get("username", "")
                ssl = bool(database_config.get("ssl", True))
                readonly = bool(database_config.get("readonly", True))
                if (not isinstance(old_id, str) or not old_id or old_id in database_map
                    or not isinstance(name, str) or not name.strip() or len(name) > 100
                    or db_type not in {"sqlite", "mysql"}
                    or not isinstance(sqlite_path, str) or len(sqlite_path) > 4096
                    or not isinstance(host, str) or len(host) > 255
                    or not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535
                    or not isinstance(database_name, str) or len(database_name) > 255
                    or not isinstance(username, str) or len(username) > 255):
                    raise HTTPException(status_code=422, detail="导入文件包含无效数据库连接")
                if db_type == "mysql" and (not host.strip() or not database_name.strip() or not username.strip()):
                    raise HTTPException(status_code=422, detail="导入的 MySQL 配置缺少连接字段")
                new_id = uuid4().hex
                database_map[old_id] = new_id
                imported_database_configs.append({"id": new_id, "name": name.strip()})
                connection.execute(
                    "INSERT INTO database_connections(id,name,db_type,sqlite_path,host,port,database_name,username,ssl,readonly) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (new_id, name.strip(), db_type, "" if db_type == "sqlite" else "", host, port, database_name,
                     username, int(ssl), int(readonly)),
                )
                imported_database_connections += 1

            for scheduled_task in payload.scheduled_tasks:
                old_id = scheduled_task.get("id")
                name = scheduled_task.get("name")
                script = scheduled_task.get("script")
                cron_expression = scheduled_task.get("cron_expression")
                timezone = scheduled_task.get("timezone")
                timeout_seconds = scheduled_task.get("timeout_seconds", 300)
                if (not isinstance(old_id, str) or not old_id or old_id in scheduled_task_map
                    or not isinstance(name, str) or not name.strip() or len(name) > 100
                    or not isinstance(script, str) or not script.strip() or len(script) > MAX_SCRIPT_CHARS
                    or not isinstance(cron_expression, str) or len(cron_expression) > 100
                    or not isinstance(timezone, str) or len(timezone) > 100
                    or not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool)
                    or not 1 <= timeout_seconds <= MAX_TIMEOUT_SECONDS):
                    raise HTTPException(status_code=422, detail="导入文件包含无效的定时任务")
                try:
                    validate_script(script)
                    CronSchedule(cron_expression, timezone)
                except SchedulerError as error:
                    raise HTTPException(status_code=422, detail=f"导入文件包含无效的定时任务：{error}") from None
                new_id = uuid4().hex
                scheduled_task_map[old_id] = new_id
                connection.execute(
                    "INSERT INTO scheduled_tasks(id,name,script,cron_expression,timezone,timeout_seconds,enabled,confirmed_hash,confirmed_at,next_run_at,last_run_at) "
                    "VALUES(?,?,?,?,?,?,0,NULL,NULL,NULL,NULL)",
                    (new_id, name.strip(), script, cron_expression, timezone, timeout_seconds),
                )
                imported_scheduled_tasks.append({"id": new_id, "name": name.strip()})

            for model in payload.models:
                old_id = model.get("id")
                name = model.get("name")
                base_url = model.get("base_url")
                model_name = model.get("model")
                if (not isinstance(old_id, str) or not old_id or not isinstance(name, str) or not name.strip()
                    or not isinstance(base_url, str) or len(base_url) > 2048
                    or not isinstance(model_name, str) or len(model_name) > 200):
                    raise HTTPException(status_code=422, detail="导入文件包含无效的 Model 配置")
                if old_id in model_map:
                    raise HTTPException(status_code=422, detail="导入文件包含重复的 Model ID")
                new_model_id = uuid4().hex
                model_map[old_id] = new_model_id
                connection.execute(
                    "INSERT INTO model_configs(id,name,base_url,model,credential_migrated) VALUES(?,?,?,?,1)",
                    (new_model_id, name.strip()[:100], base_url, model_name),
                )
                imported_model_count += 1

            for agent in payload.agents:
                new_agent_id = uuid4().hex
                old_agent_id = str(agent.get("id", ""))
                if not old_agent_id or old_agent_id in agent_map:
                    raise HTTPException(status_code=422, detail="导入文件中的 Agent ID 无效或重复")
                agent_map[old_agent_id] = new_agent_id
                selected = agent.get("selected_skills", [])
                selected_mcp = agent.get("selected_mcp_tools", [])
                selected_local = agent.get("selected_local_tools", [])
                selected_knowledge_bases = agent.get("selected_knowledge_bases", [])
                selected_database_connections = agent.get("selected_database_connections", [])
                selected_agents = agent.get("selected_agents", [])
                if not isinstance(selected_agents, list) or len(selected_agents) > 20 or not all(isinstance(item, str) for item in selected_agents):
                    import_warnings.append(f"Agent「{agent['name']}」的委派目标无效，已清空授权。")
                    selected_agents = []
                selected_agents_by_agent[new_agent_id] = selected_agents
                selected_mcp_by_agent[new_agent_id] = selected_mcp
                if (not isinstance(selected_database_connections, list)
                    or len(selected_database_connections) > 100
                    or not all(isinstance(item, str) and item in database_map for item in selected_database_connections)):
                    raise HTTPException(status_code=422, detail="Agent 引用了不存在的数据库连接")
                if (not isinstance(selected_knowledge_bases, list)
                    or len(selected_knowledge_bases) > 100
                    or not all(isinstance(item, str) and item in knowledge_base_map for item in selected_knowledge_bases)):
                    raise HTTPException(status_code=422, detail="Agent 引用了不存在的知识库")
                if any(
                    not isinstance(items, list) or len(items) > 100
                    or not all(isinstance(item, str) and len(item) <= 200 for item in items)
                    for items in (selected_mcp, selected_local)
                ):
                    raise HTTPException(status_code=422, detail="导入文件中的插件授权设置无效")
                if not isinstance(selected, list) or not all(isinstance(item, str) for item in selected):
                    raise HTTPException(status_code=422, detail="导入文件中的 Skills 设置无效")
                if payload.format_version == 2:
                    old_model_id = agent.get("model_config_id")
                    if "model_config_id" in agent and old_model_id is None:
                        new_model_id = None
                    elif isinstance(old_model_id, str) and old_model_id in model_map:
                        new_model_id = model_map[old_model_id]
                    else:
                        raise HTTPException(status_code=422, detail="Agent 引用了不存在的 Model 配置")
                else:
                    old_model_id = agent.get("model_config_id")
                    if isinstance(old_model_id, str) and old_model_id in model_map:
                        new_model_id = model_map[old_model_id]
                    else:
                        base_url = agent.get("base_url", "")
                        model_name = agent.get("model", "")
                        if not isinstance(base_url, str) or not isinstance(model_name, str):
                            raise HTTPException(status_code=422, detail="旧备份中的 Agent 模型配置无效")
                        new_model_id = uuid4().hex
                        connection.execute(
                            "INSERT INTO model_configs(id,name,base_url,model,credential_migrated) VALUES(?,?,?,?,1)",
                            (new_model_id, f"{agent['name']} · {model_name or 'Model'}"[:100], base_url, model_name),
                        )
                        imported_model_count += 1
                connection.execute(
                    "INSERT INTO agents(id,name,provider,base_url,model,system_prompt,mode,context_window,skill_directory,selected_skills_json,selected_agents_json,sandbox_code_enabled,max_rounds,selected_mcp_tools_json,selected_local_tools_json,selected_knowledge_bases_json,selected_database_connections_json,prompt_variables_json,model_config_id,model_binding_cleared) "
                    "VALUES(?,?,?,'','',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (new_agent_id, agent["name"].strip(), agent.get("provider", "openai_compatible"),
                     agent.get("system_prompt", ""), agent.get("mode", "react"),
                     agent.get("context_window", 32768), agent.get("skill_directory", ""),
                     json.dumps(selected), "[]", int(bool(agent.get("sandbox_code_enabled", False))),
                     agent.get("max_rounds", 500), json.dumps(selected_mcp), json.dumps(selected_local),
                     json.dumps([knowledge_base_map[item] for item in selected_knowledge_bases]),
                     json.dumps([database_map[item] for item in selected_database_connections]),
                     json.dumps(agent.get("prompt_variables", {})), new_model_id, int(new_model_id is None)),
                )

            for new_agent_id, old_targets in selected_agents_by_agent.items():
                mapped_targets = []
                for old_target in old_targets:
                    new_target = agent_map.get(old_target)
                    if new_target is None or new_target == new_agent_id:
                        import_warnings.append("备份中的委派目标不存在或指向自身，已跳过该授权。")
                        continue
                    if new_target not in mapped_targets:
                        mapped_targets.append(new_target)
                connection.execute(
                    "UPDATE agents SET selected_agents_json=? WHERE id=?",
                    (json.dumps(mapped_targets), new_agent_id),
                )

            for session in payload.sessions:
                old_agent = session.get("agent_id", "")
                if old_agent is not None and str(old_agent) not in agent_map:
                    raise HTTPException(status_code=422, detail="导入会话引用了不存在的智能体")
                new_id = uuid4().hex
                session_map[str(session["id"])] = new_id
                connection.execute(
                    "INSERT INTO sessions(id,agent_id,title) VALUES(?,?,?)",
                    (new_id, agent_map[str(old_agent)] if old_agent is not None else None,
                     str(session.get("title", "新对话"))[:120]),
                )
            for session in payload.sessions:
                parent = session.get("parent_session_id")
                if parent and str(parent) in session_map:
                    connection.execute(
                        "UPDATE sessions SET parent_session_id = ?, branched_from_message_id = ? WHERE id = ?",
                        (session_map[str(parent)], session.get("branched_from_message_id"), session_map[str(session["id"])]),
                    )
            for message in payload.messages:
                session_id = session_map.get(str(message.get("session_id", "")))
                role = message.get("role")
                content = message.get("content")
                if session_id is None or role not in {"system", "user", "assistant", "tool"} or not isinstance(content, str):
                    raise HTTPException(status_code=422, detail="导入文件包含无效的对话消息")
                connection.execute(
                    "INSERT INTO messages(id,session_id,role,content,metadata_json) VALUES(?,?,?,?,?)",
                    (uuid4().hex, session_id, role, content[:1000000], _portable_message_metadata(message.get('metadata_json'))),
                )
            for prompt in payload.prompt_versions:
                agent_id = agent_map.get(str(prompt.get("agent_id", "")))
                if agent_id and isinstance(prompt.get("content"), str):
                    connection.execute(
                        "INSERT INTO prompt_versions(id,agent_id,content) VALUES(?,?,?)",
                        (uuid4().hex, agent_id, prompt["content"][:20000]),
                    )
            for memory in payload.memories:
                scope = memory.get("scope", "global")
                agent_id = agent_map.get(str(memory.get("agent_id", ""))) if scope == "agent" else None
                content = memory.get("content")
                if not isinstance(content, str) or scope not in {"global", "agent"} or (scope == "agent" and agent_id is None):
                    continue
                connection.execute(
                    "INSERT INTO memories(id,content,scope,agent_id,source) VALUES(?,?,?,?,?)",
                    (uuid4().hex, content[:2000], scope, agent_id, "user_confirmed"),
                )
            mcp_imported = 0
            for server in payload.mcp_servers:
                name = str(server.get("name", "")).strip()
                transport = server.get("transport")
                args = server.get("args", [])
                env_names = server.get("env_names", [])
                transport = "streamable_http" if transport == "http" else transport
                if (not name or transport not in {"stdio", "sse", "streamable_http"} or
                    not isinstance(args, list) or not all(isinstance(arg, str) for arg in args) or
                    not isinstance(env_names, list) or not all(isinstance(key, str) for key in env_names)):
                    raise HTTPException(status_code=422, detail="导入文件包含无效的 MCP Server 配置")
                existing = {row[0] for row in connection.execute("SELECT name FROM mcp_servers")}
                base_name = name
                suffix = 2
                while name in existing:
                    name = f"{base_name} (导入 {suffix})"
                    suffix += 1
                original_server_id = str(server.get("id", ""))
                if not original_server_id:
                    raise HTTPException(status_code=422, detail="导入文件中的 MCP Server ID 无效")
                imported_server_id = uuid4().hex
                mcp_server_map[original_server_id] = imported_server_id
                connection.execute(
                    "INSERT INTO mcp_servers(id,name,transport,command,args_json,cwd,url,enabled,env_names_json) VALUES(?,?,?,?,?,?,?,?,?)",
                    (imported_server_id, name, transport, str(server.get("command", "")),
                     __import__("json").dumps(args), str(server.get("cwd", "")),
                     str(server.get("url", "")), int(bool(server.get("enabled", True))),
                     __import__("json").dumps(env_names)),
                )
                mcp_imported += 1
            for agent_id, selected in selected_mcp_by_agent.items():
                remapped = []
                for permission in selected:
                    parts = permission.split(":", 2)
                    if len(parts) == 3 and parts[0] == "mcp" and parts[1] in mcp_server_map:
                        remapped.append(f"mcp:{mcp_server_map[parts[1]]}:{parts[2]}")
                    else:
                        remapped.append(permission)
                connection.execute(
                    "UPDATE agents SET selected_mcp_tools_json = ? WHERE id = ?",
                    (__import__("json").dumps(remapped), agent_id),
                )
    except HTTPException:
        raise
    except (KeyError, TypeError, ValueError) as error:
        raise HTTPException(status_code=422, detail="导入文件格式无效") from error
    request.app.state.database_manager.register_tools(request.app.state.tool_registry)
    return {"models_imported": imported_model_count,
            "knowledge_bases_imported": len(imported_knowledge_bases),
            "knowledge_documents_imported": imported_knowledge_documents,
            "knowledge_bases": imported_knowledge_bases,
            "database_connections_imported": imported_database_connections,
            "database_connections": imported_database_configs,
            "scheduled_tasks_imported": len(imported_scheduled_tasks),
            "scheduled_tasks": imported_scheduled_tasks,
            "agents_imported": len(agent_map), "sessions_imported": len(session_map),
            "messages_imported": len(payload.messages), "mcp_servers_imported": mcp_imported,
            "secrets_imported": 0, "warnings": import_warnings}
