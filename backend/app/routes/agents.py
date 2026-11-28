from uuid import uuid4
import json
from time import perf_counter

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from backend.app.db import database_connection
from backend.app.models import AgentCreate, AgentPatch, AgentResponse
from backend.app.prompts import render_prompt
from backend.app.runtime.chat_options import chat_options, default_chat_capabilities
from backend.app.provider.openai_compatible import ProviderError, complete_chat

router = APIRouter(prefix="/agents", tags=["agents"])

_AGENT_SELECT = (
    "SELECT a.id,a.name,a.provider,a.model_config_id,m.model,m.name AS model_config_name,"
    "a.system_prompt,a.mode,a.context_window,a.skill_directory,a.selected_skills_json,"
    "a.selected_agents_json,a.sandbox_code_enabled,a.max_rounds,a.selected_mcp_tools_json,a.selected_local_tools_json,a.selected_knowledge_bases_json,a.selected_database_connections_json,a.prompt_variables_json,"
    "a.input_rate_per_million,a.output_rate_per_million,a.created_at,a.updated_at "
    "FROM agents a LEFT JOIN model_configs m ON m.id=a.model_config_id "
)


def _validate_model_assignment(connection, model_config_id: str, request: Request) -> None:
    model = connection.execute(
        "SELECT id,last_tested_at FROM model_configs WHERE id=?", (model_config_id,)
    ).fetchone()
    if model is None:
        raise HTTPException(status_code=422, detail="所选 Model 不存在")
    if not request.app.state.secret_store.get(f"model:{model_config_id}:api_key"):
        raise HTTPException(status_code=409, detail="所选 Model 尚未配置 API 密钥")
    if not model["last_tested_at"]:
        raise HTTPException(status_code=409, detail="请先测试所选 Model 的连接")



def _validate_delegation_targets(connection, agent_id: str, target_ids: list[str]) -> list[str]:
    unique_ids = list(dict.fromkeys(target_ids))
    if len(unique_ids) > 20 or any(not isinstance(item, str) or len(item) != 32 for item in unique_ids):
        raise HTTPException(status_code=422, detail="Agent 委派授权列表无效")
    if agent_id in unique_ids:
        raise HTTPException(status_code=422, detail="Agent 不能委派给自身")
    if unique_ids:
        placeholders = ",".join("?" for _ in unique_ids)
        rows = connection.execute(f"SELECT id FROM agents WHERE id IN ({placeholders})", unique_ids).fetchall()
        found = {row["id"] for row in rows}
        if found != set(unique_ids):
            raise HTTPException(status_code=422, detail="委派目标包含不存在的 Agent")
    return unique_ids


def _validate_knowledge_bases(connection, knowledge_base_ids: list[str]) -> list[str]:
    unique_ids = list(dict.fromkeys(knowledge_base_ids))
    if len(unique_ids) > 100 or any(not isinstance(item, str) for item in unique_ids):
        raise HTTPException(status_code=422, detail="知识库授权列表无效")
    for knowledge_base_id in unique_ids:
        exists = connection.execute(
            "SELECT 1 FROM knowledge_bases WHERE id=?", (knowledge_base_id,)
        ).fetchone()
        if exists is None:
            raise HTTPException(status_code=422, detail="Agent 引用了不存在的知识库")
    return unique_ids


def _validate_database_connections(connection, database_connection_ids: list[str]) -> list[str]:
    unique_ids = list(dict.fromkeys(database_connection_ids))
    if len(unique_ids) > 100 or any(not isinstance(item, str) for item in unique_ids):
        raise HTTPException(status_code=422, detail="数据库连接授权列表无效")
    for connection_id in unique_ids:
        exists = connection.execute("SELECT 1 FROM database_connections WHERE id=?", (connection_id,)).fetchone()
        if exists is None:
            raise HTTPException(status_code=422, detail="Agent 引用了不存在的数据库连接")
    return unique_ids


def _agent_response(row: object) -> AgentResponse:
    item = dict(row)
    item["selected_skills"] = json.loads(item.pop("selected_skills_json", "[]"))
    item["selected_mcp_tools"] = json.loads(item.pop("selected_mcp_tools_json", "[]"))
    item["selected_local_tools"] = json.loads(item.pop("selected_local_tools_json", "[]"))
    item["selected_knowledge_bases"] = json.loads(item.pop("selected_knowledge_bases_json", "[]"))
    item["selected_database_connections"] = json.loads(item.pop("selected_database_connections_json", "[]"))
    item["selected_agents"] = json.loads(item.pop("selected_agents_json", "[]"))
    item["sandbox_code_enabled"] = bool(item.get("sandbox_code_enabled", 0))
    item["prompt_variables"] = json.loads(item.pop("prompt_variables_json", "{}"))
    return AgentResponse(**item)


def _save_prompt_version(connection, agent_id: str, prompt: str) -> None:
    latest = connection.execute(
        "SELECT content FROM prompt_versions WHERE agent_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (agent_id,),
    ).fetchone()
    if latest is None or latest["content"] != prompt:
        connection.execute(
            "INSERT INTO prompt_versions(id, agent_id, content) VALUES (?, ?, ?)",
            (uuid4().hex, agent_id, prompt),
        )


@router.get("", response_model=list[AgentResponse])
def list_agents(request: Request) -> list[AgentResponse]:
    with database_connection(request.app.state.database_path) as connection:
        rows = connection.execute(
            _AGENT_SELECT + "ORDER BY a.updated_at DESC,a.name"
        ).fetchall()
    return [_agent_response(row) for row in rows]


@router.post("", response_model=AgentResponse, status_code=status.HTTP_201_CREATED)
def create_agent(payload: AgentCreate, request: Request) -> AgentResponse:
    agent_id = uuid4().hex
    selected_knowledge_bases = list(dict.fromkeys(payload.selected_knowledge_bases))
    selected_database_connections = list(dict.fromkeys(payload.selected_database_connections))
    with database_connection(request.app.state.database_path) as connection:
        _validate_model_assignment(connection, payload.model_config_id, request)
        selected_knowledge_bases = _validate_knowledge_bases(connection, selected_knowledge_bases)
        selected_database_connections = _validate_database_connections(connection, selected_database_connections)
        selected_agents = _validate_delegation_targets(connection, agent_id, payload.selected_agents)
        connection.execute(
            "INSERT INTO agents(id,name,provider,base_url,model,system_prompt,mode,context_window,skill_directory,selected_skills_json,selected_agents_json,sandbox_code_enabled,max_rounds,selected_mcp_tools_json,selected_local_tools_json,selected_knowledge_bases_json,selected_database_connections_json,prompt_variables_json,input_rate_per_million,output_rate_per_million,model_config_id) "
            "VALUES(?,?,?, '', '', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                agent_id, payload.name, payload.provider, payload.system_prompt, payload.mode,
                payload.context_window, payload.skill_directory, json.dumps(payload.selected_skills),
                json.dumps(selected_agents), int(payload.sandbox_code_enabled), payload.max_rounds,
                json.dumps(payload.selected_mcp_tools), json.dumps(payload.selected_local_tools),
                json.dumps(selected_knowledge_bases), json.dumps(selected_database_connections),
                json.dumps(payload.prompt_variables), payload.input_rate_per_million,
                payload.output_rate_per_million, payload.model_config_id,
            ),
        )
        _save_prompt_version(connection, agent_id, payload.system_prompt)
        row = connection.execute(_AGENT_SELECT + "WHERE a.id=?", (agent_id,)).fetchone()
    return _agent_response(row)


@router.get("/{agent_id}/chat-options")
async def list_chat_options(agent_id: str, request: Request):
    with database_connection(request.app.state.database_path) as connection:
        agent = connection.execute(
            "SELECT id AS agent_id, skill_directory, selected_skills_json, selected_mcp_tools_json, "
            "selected_local_tools_json, selected_agents_json FROM agents WHERE id=?", (agent_id,),
        ).fetchone()
        if agent is None:
            raise HTTPException(status_code=404, detail="Agent 不存在")
        options, _ = chat_options(connection, request.app.state, agent)
        return {"options": options, "defaults": default_chat_capabilities(agent, options)}


@router.get("/{agent_id}", response_model=AgentResponse)
def get_agent(agent_id: str, request: Request) -> AgentResponse:
    with database_connection(request.app.state.database_path) as connection:
        row = connection.execute(_AGENT_SELECT + "WHERE a.id=?", (agent_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Agent 不存在")
    return _agent_response(row)


@router.patch("/{agent_id}", response_model=AgentResponse)
def update_agent(
    agent_id: str, payload: AgentPatch, request: Request
) -> AgentResponse:
    updates = payload.model_dump(exclude_unset=True)
    delegation_targets = updates.pop("selected_agents", None)
    if "selected_skills" in updates:
        updates["selected_skills_json"] = json.dumps(updates.pop("selected_skills"))
    if "selected_mcp_tools" in updates:
        updates["selected_mcp_tools_json"] = json.dumps(updates.pop("selected_mcp_tools"))
    if "selected_local_tools" in updates:
        updates["selected_local_tools_json"] = json.dumps(updates.pop("selected_local_tools"))
    if "selected_knowledge_bases" in updates:
        with database_connection(request.app.state.database_path) as validation_connection:
            updates["selected_knowledge_bases"] = _validate_knowledge_bases(
                validation_connection, updates["selected_knowledge_bases"]
            )
        updates["selected_knowledge_bases_json"] = json.dumps(updates.pop("selected_knowledge_bases"))
    if "prompt_variables" in updates:
        updates["prompt_variables_json"] = json.dumps(updates.pop("prompt_variables"))
    if "input_rate_per_million" in updates and updates["input_rate_per_million"] is None:
        updates["input_rate_per_million"] = None
    if "selected_database_connections" in updates:
        with database_connection(request.app.state.database_path) as validation_connection:
            updates["selected_database_connections"] = _validate_database_connections(
                validation_connection, updates["selected_database_connections"]
            )
        updates["selected_database_connections_json"] = json.dumps(updates.pop("selected_database_connections"))
    nullable_fields = {"input_rate_per_million", "output_rate_per_million"}
    if (not updates and delegation_targets is None) or any(value is None for field, value in updates.items() if field not in nullable_fields):
        raise HTTPException(status_code=422, detail="请提供有效的更新内容")

    with database_connection(request.app.state.database_path) as connection:
        current = connection.execute(
            "SELECT model_config_id FROM agents WHERE id=?", (agent_id,)
        ).fetchone()
        if current is None:
            raise HTTPException(status_code=404, detail="Agent 不存在")
        if delegation_targets is not None:
            updates["selected_agents_json"] = json.dumps(
                _validate_delegation_targets(connection, agent_id, delegation_targets)
            )
        if not updates:
            raise HTTPException(status_code=422, detail="请提供有效的更新内容")
        if "model_config_id" in updates and updates["model_config_id"] != current["model_config_id"]:
            _validate_model_assignment(connection, updates["model_config_id"], request)
            updates["model_binding_cleared"] = 0
        assignments = ", ".join(f"{field} = ?" for field in updates)
        values = [*updates.values(), agent_id]
        cursor = connection.execute(
            f"UPDATE agents SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            values,
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Agent 不存在")
        if "system_prompt" in updates:
            _save_prompt_version(connection, agent_id, updates["system_prompt"])
        row = connection.execute(_AGENT_SELECT + "WHERE a.id=?", (agent_id,)).fetchone()
    return _agent_response(row)


@router.post("/{agent_id}/test-connection")
async def test_connection(agent_id: str, request: Request):
    with database_connection(request.app.state.database_path) as connection:
        agent = connection.execute(
            "SELECT a.model_config_id,m.base_url,m.model FROM agents a "
            "LEFT JOIN model_configs m ON m.id=a.model_config_id WHERE a.id=?", (agent_id,)
        ).fetchone()
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent 不存在")
    if not agent["model_config_id"]:
        raise HTTPException(status_code=409, detail="请先为此 Agent 选择 Model")
    api_key = request.app.state.secret_store.get(f"model:{agent['model_config_id']}:api_key")
    if not agent["base_url"] or not agent["model"] or not api_key:
        raise HTTPException(status_code=409, detail="请检查关联 Model 的地址、模型标识和 API 密钥")
    started = perf_counter()
    try:
        await complete_chat(
            request.app.state.http_client, base_url=agent["base_url"],
            model=agent["model"], api_key=api_key,
            messages=[{"role": "user", "content": "请仅回复：连接成功"}],
        )
    except ProviderError as error:
        raise HTTPException(status_code=502, detail=str(error)) from None
    with database_connection(request.app.state.database_path) as connection:
        connection.execute(
            "UPDATE model_configs SET last_tested_at=CURRENT_TIMESTAMP WHERE id=?",
            (agent["model_config_id"],),
        )
    return {"status": "ok", "elapsed_ms": round((perf_counter() - started) * 1000)}


class PromptPreviewRequest(BaseModel):
    variables: dict[str, str] = Field(default_factory=dict, max_length=50)


@router.post("/{agent_id}/prompts/preview")
def preview_prompt(agent_id: str, payload: PromptPreviewRequest, request: Request):
    with database_connection(request.app.state.database_path) as connection:
        row = connection.execute("SELECT system_prompt FROM agents WHERE id = ?", (agent_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Agent 不存在")
    try:
        rendered = render_prompt(row["system_prompt"], payload.variables)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    return {"rendered_prompt": rendered, "model_called": False}


@router.get("/{agent_id}/prompts")
def list_prompt_versions(agent_id: str, request: Request):
    with database_connection(request.app.state.database_path) as connection:
        exists = connection.execute("SELECT 1 FROM agents WHERE id = ?", (agent_id,)).fetchone()
        if exists is None:
            raise HTTPException(status_code=404, detail="Agent 不存在")
        rows = connection.execute(
            "SELECT id, content, created_at FROM prompt_versions WHERE agent_id = ? "
            "ORDER BY created_at DESC, rowid DESC",
            (agent_id,),
        ).fetchall()
    return [dict(row) for row in rows]


@router.post("/{agent_id}/prompts/{prompt_id}/restore", response_model=AgentResponse)
def restore_prompt_version(agent_id: str, prompt_id: str, request: Request) -> AgentResponse:
    with database_connection(request.app.state.database_path) as connection:
        version = connection.execute(
            "SELECT content FROM prompt_versions WHERE id = ? AND agent_id = ?",
            (prompt_id, agent_id),
        ).fetchone()
        if version is None:
            raise HTTPException(status_code=404, detail="Prompt 版本不存在")
        connection.execute(
            "UPDATE agents SET system_prompt = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (version["content"], agent_id),
        )
        _save_prompt_version(connection, agent_id, version["content"])
        row = connection.execute(_AGENT_SELECT + "WHERE a.id=?", (agent_id,)).fetchone()
    return _agent_response(row)


class CredentialPayload(BaseModel):
    api_key: str = Field(min_length=1, max_length=4096)


@router.put("/{agent_id}/credentials", status_code=status.HTTP_204_NO_CONTENT)
def set_agent_credential(
    agent_id: str, payload: CredentialPayload, request: Request
) -> Response:
    with database_connection(request.app.state.database_path) as connection:
        agent = connection.execute(
            "SELECT model_config_id FROM agents WHERE id=?", (agent_id,)
        ).fetchone()
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent 不存在")
    if not agent["model_config_id"]:
        raise HTTPException(status_code=409, detail="请先为此 Agent 选择 Model")
    try:
        request.app.state.secret_store.set(f"model:{agent['model_config_id']}:api_key", payload.api_key)
    except Exception:
        raise HTTPException(status_code=503, detail="无法安全保存 Model API 密钥") from None
    with database_connection(request.app.state.database_path) as connection:
        connection.execute("UPDATE model_configs SET last_tested_at=NULL WHERE id=?", (agent["model_config_id"],))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_agent(agent_id: str, request: Request) -> Response:
    with database_connection(request.app.state.database_path) as connection:
        cursor = connection.execute("DELETE FROM agents WHERE id = ?", (agent_id,))
        if cursor.rowcount:
            for row in connection.execute("SELECT id,selected_agents_json FROM agents").fetchall():
                targets = json.loads(row["selected_agents_json"] or "[]")
                if agent_id in targets:
                    targets = [target for target in targets if target != agent_id]
                    connection.execute(
                        "UPDATE agents SET selected_agents_json=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                        (json.dumps(targets), row["id"]),
                    )
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Agent 不存在")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
