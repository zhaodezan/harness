import asyncio
import json
import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from fastapi.responses import StreamingResponse

from backend.app.db import database_connection
from backend.app.knowledge_base import format_knowledge_context, retrieve_authorized_documents
from backend.app.prompts import render_prompt
from backend.app.provider.openai_compatible import ProviderError
from backend.app.provider.streaming import stream_chat_events
from backend.app.runtime.planning import parse_plan_steps
from backend.app.skills import discover_skills
from backend.app.runtime.skill_context import build_skill_context, read_skill_resource, read_skill_script, skill_resource_tool, skill_script_tool
from backend.app.runtime.a2a import DelegationBudget
from backend.app.runtime.sandbox_tools import sandbox_code_tool
from backend.app.runtime.cube_sandbox import SandboxUnavailable
from backend.app.runtime.windows_tool import WindowsToolError
from backend.app.tools import ApprovalRequired, chat_tool_requires_approval, filter_tools_for_agent, model_tool_names

router = APIRouter(prefix="/api/agents", tags=["agent debug"])


class DebugMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=100_000)


class DebugConfig(BaseModel):
    model_config_id: str = Field(min_length=32, max_length=32)
    mode: Literal["react", "plan_execute"] = "react"
    context_window: int = Field(default=32768, ge=1024, le=2_000_000)
    system_prompt: str = Field(default="", max_length=20000)
    prompt_variables: dict[str, str] = Field(default_factory=dict, max_length=50)
    selected_skills: list[str] = Field(default_factory=list, max_length=50)
    selected_mcp_tools: list[str] = Field(default_factory=list, max_length=100)
    selected_local_tools: list[str] = Field(default_factory=list, max_length=100)
    selected_knowledge_bases: list[str] = Field(default_factory=list, max_length=100)
    selected_database_connections: list[str] = Field(default_factory=list, max_length=100)
    selected_agents: list[str] = Field(default_factory=list, max_length=20)
    sandbox_code_enabled: bool = False


class DebugRequest(BaseModel):
    config: DebugConfig
    messages: list[DebugMessage] = Field(min_length=1, max_length=40)


def _frame(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n"


@router.post("/{agent_id}/debug")
async def debug_agent(agent_id: str, payload: DebugRequest, request: Request):
    config = payload.config
    knowledge_results: list[dict] = []
    knowledge_error = False
    with database_connection(request.app.state.database_path) as connection:
        agent_exists = connection.execute("SELECT 1 FROM agents WHERE id=?", (agent_id,)).fetchone()
        if agent_exists is None:
            raise HTTPException(status_code=404, detail="Agent 不存在")
        model = connection.execute(
            "SELECT base_url,model FROM model_configs WHERE id=?", (config.model_config_id,)
        ).fetchone()
        if config.selected_knowledge_bases:
            placeholders = ",".join("?" for _ in config.selected_knowledge_bases)
            count = connection.execute(
                f"SELECT count(*) FROM knowledge_bases WHERE id IN ({placeholders})",
                config.selected_knowledge_bases,
            ).fetchone()[0]
            if count != len(set(config.selected_knowledge_bases)):
                raise HTTPException(status_code=422, detail="调试配置包含不存在的知识库")
        try:
            last_user_message = next(
                (item.content for item in reversed(payload.messages) if item.role == "user"), ""
            )
            knowledge_results = retrieve_authorized_documents(
                connection, config.selected_knowledge_bases, last_user_message
            )
        except Exception:
            knowledge_error = True
    if model is None or not model["base_url"] or not model["model"]:
        raise HTTPException(status_code=409, detail="调试配置引用的 Model 不存在或配置不完整")
    api_key = request.app.state.secret_store.get(f"model:{config.model_config_id}:api_key")
    if not api_key:
        raise HTTPException(status_code=409, detail="请先为调试配置所选 Model 保存 API 密钥")

    try:
        messages = []
        if config.system_prompt:
            messages.append({"role": "system", "content": render_prompt(config.system_prompt, config.prompt_variables)})
        skill_context = build_skill_context(
            config.selected_skills, request.app.state.plugin_store.list_skills(), [],
            next((item.content for item in reversed(payload.messages) if item.role == "user"), ""),
        )
        messages.extend(skill_context.messages)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    messages.extend(item.model_dump() for item in payload.messages)
    registry = request.app.state.tool_registry
    registered_tools = filter_tools_for_agent(
        registry.list(), config.selected_mcp_tools, config.selected_local_tools,
        config.selected_database_connections,
    )
    registry_snapshot = {tool["name"]: registry.get_definition(tool["name"]) for tool in registered_tools}
    skill_tool = skill_resource_tool(registered_tools) if skill_context.loaded else None
    if skill_tool:
        registered_tools.append(skill_tool)
    script_tool = skill_script_tool(registered_tools) if skill_context.loaded else None
    if script_tool:
        registered_tools.append(script_tool)
    with database_connection(request.app.state.database_path) as connection:
        agent_row = connection.execute("SELECT selected_agents_json,sandbox_code_enabled FROM agents WHERE id=?", (agent_id,)).fetchone()
    saved_agents = set(json.loads(agent_row["selected_agents_json"] or "[]")) if agent_row else set()
    allowed_agents = [target for target in config.selected_agents if target in saved_agents]
    code_enabled = bool(config.sandbox_code_enabled and agent_row and agent_row["sandbox_code_enabled"])
    delegation_tool, _ = request.app.state.a2a_service.create_delegate_tool(
        agent_id, allowed_agents, registered_tools
    )
    if delegation_tool:
        registered_tools.append(delegation_tool)
    code_tool = sandbox_code_tool(registered_tools) if code_enabled else None
    if code_tool:
        registered_tools.append(code_tool)
    provider_names = model_tool_names([tool["name"] for tool in registered_tools])
    reverse_names = {provider: original for original, provider in provider_names.items()}
    provider_tools = [{"type": "function", "function": {
        "name": provider_names[tool["name"]], "description": tool["description"], "parameters": tool["input_schema"],
    }} for tool in registered_tools]

    async def event_stream():
        request_messages = list(messages)
        if knowledge_results:
            context = format_knowledge_context(knowledge_results)
            last_user_index = next(
                (index for index in range(len(request_messages) - 1, -1, -1)
                 if request_messages[index]["role"] == "user"),
                len(request_messages),
            )
            request_messages.insert(last_user_index, {"role": "system", "content": context})
        execution_messages = list(request_messages)
        delegation_budget = DelegationBudget([agent_id])
        def trace(event_type: str, status: str, summary: str) -> str:
            return _frame("trace", {"event_type": event_type, "status": status, "summary": summary})

        try:
            yield trace("run_started", "running", f"调试运行开始 · 模型：{model['model']}")
            yield _frame("trace", {
                "event_type": "knowledge_retrieval",
                "status": "failed" if knowledge_error else ("completed" if knowledge_results else "empty"),
                "hit_count": len(knowledge_results),
                "sources": [
                    {"knowledge_base_name": item["knowledge_base_name"], "filename": item["filename"]}
                    for item in knowledge_results
                ],
                "summary": "知识库检索完成" if not knowledge_error else "知识库检索失败，已继续调试",
            })
            yield _frame("trace", skill_context.trace())
            steps = [None]
            if config.mode == "plan_execute":
                planning_messages = [
                    {"role": "system", "content": "请仅制定一个简洁、可执行的分步计划；不要执行计划，不要调用工具。以编号列表输出。"},
                    *messages,
                ]
                plan_parts = []
                async for planning_event in stream_chat_events(
                    request.app.state.http_client, base_url=model["base_url"], model=model["model"],
                    api_key=api_key, messages=planning_messages, tools=[],
                ):
                    if planning_event["type"] == "text":
                        plan_parts.append(planning_event["text"])
                plan_text = "".join(plan_parts).strip()
                try:
                    steps = parse_plan_steps(plan_text)
                except ValueError as error:
                    raise ProviderError(str(error)) from None
                yield _frame("plan", {"plan": plan_text, "revised": False})
                execution_messages.append({"role": "assistant", "content": f"执行计划：\n{plan_text}"})
            for step_index, step in enumerate(steps, start=1):
                if step is not None:
                    yield trace("plan_step_started", "running", f"正在执行第 {step_index}/{len(steps)} 步")
                    execution_messages.append({
                        "role": "user",
                        "content": f"现在只执行计划第 {step_index} 步：{step}。完成后简要报告；不要提前执行后续步骤。",
                    })
                for _ in range(8):
                    calls = []
                    async for event in stream_chat_events(
                        request.app.state.http_client, base_url=model["base_url"], model=model["model"],
                        api_key=api_key, messages=execution_messages, tools=provider_tools,
                    ):
                        if event["type"] == "text":
                            yield _frame("delta", {"text": event["text"]})
                        elif event["type"] == "tool_calls":
                            calls = event["tool_calls"]
                            for call in calls:
                                call["name"] = reverse_names.get(call["name"], "")
                    if not calls:
                        break
                    execution_messages.append({
                        "role": "assistant", "content": None,
                        "tool_calls": [{"id": call["id"], "type": "function", "function": {
                            "name": call["name"], "arguments": json.dumps(call["arguments"], ensure_ascii=False),
                        }} for call in calls],
                    })
                    for call in calls:
                        definition = next((tool for tool in registered_tools if tool["name"] == call["name"]), None)
                        tool_name = call['name'] or '未知工具'
                        yield trace("tool_started", "running", f"收到工具调用：{tool_name}")
                        if definition is None:
                            result = {"error": "Tool 未注册、未经授权或已禁用"}
                        else:
                            database_authorized = True
                            if (definition.get("plugin_id") or "").startswith("database:"):
                                database_authorized = request.app.state.database_manager.is_tool_authorized(
                                    agent_id, definition["plugin_id"],
                                    draft_connection_ids=config.selected_database_connections,
                                )
                            approved = not chat_tool_requires_approval(definition)
                            if database_authorized and not approved:
                                approval_id = uuid.uuid4().hex
                                future = asyncio.get_running_loop().create_future()
                                request.app.state.pending_approvals[approval_id] = future
                                try:
                                    yield trace("approval_required", "waiting", f"等待确认工具：{tool_name}")
                                    yield _frame("approval_required", {
                                        "approval_id": approval_id, "tool_name": call["name"],
                                        "description": definition["description"], "arguments": call["arguments"],
                                    })
                                    approved = await asyncio.wait_for(future, timeout=300)
                                except asyncio.TimeoutError:
                                    approved = False
                                finally:
                                    request.app.state.pending_approvals.pop(approval_id, None)
                                yield trace(
                                    "approval_decision", "approved" if approved else "rejected",
                                    f"工具 {tool_name}" + ("已获批准" if approved else "未获批准"),
                                )
                            try:
                                if (definition.get("plugin_id") or "").startswith("internal:"):
                                    request.app.state.tool_registry._validate(definition["input_schema"], call["arguments"])
                                if not database_authorized or (
                                    (definition.get("plugin_id") or "").startswith("database:")
                                    and not request.app.state.database_manager.is_tool_authorized(
                                        agent_id, definition["plugin_id"],
                                        draft_connection_ids=config.selected_database_connections,
                                    )
                                ):
                                    raise PermissionError("数据库连接授权已撤销")
                                if definition["risk"] == "side_effect" and not approved:
                                    raise ApprovalRequired("用户未批准此操作")
                                if delegation_tool and call["name"] == delegation_tool["name"]:
                                    delegated_task = await request.app.state.a2a_service.delegate(
                                        agent_id, call["arguments"].get("agent_id"),
                                        call["arguments"].get("task"), delegation_budget,
                                    )
                                    result = {"result": delegated_task.model_dump(mode="json")}
                                    yield _frame("trace", {
                                        "event_type": "a2a_task", "status": delegated_task.status.state,
                                        "task_id": delegated_task.id,
                                        "summary": f"Agent 委派任务状态：{delegated_task.status.state}",
                                    })
                                elif skill_tool and call["name"] == skill_tool["name"]:
                                    skill_name = call["arguments"].get("skill_name")
                                    skill = skill_context.loaded.get(skill_name) if isinstance(skill_name, str) else None
                                    if skill is None:
                                        raise ValueError("资源不属于当前 Agent 已加载的 Skill")
                                    result = {"result": await asyncio.to_thread(
                                        read_skill_resource, skill, call["arguments"].get("path")
                                    )}
                                elif script_tool and call["name"] == script_tool["name"]:
                                    skill_name = call["arguments"].get("skill_name")
                                    skill = skill_context.loaded.get(skill_name) if isinstance(skill_name, str) else None
                                    if skill is None:
                                        raise ValueError("脚本不属于当前 Agent 已加载的 Skill")
                                    script = await asyncio.to_thread(read_skill_script, skill, call["arguments"].get("path"))
                                    args_json = json.dumps(call["arguments"].get("arguments", {}), ensure_ascii=False, allow_nan=False)
                                    result = {"result": await request.app.state.sandbox_executor.execute_python_async(
                                        script, json.loads(args_json),
                                    )}
                                elif code_tool and call["name"] == code_tool["name"]:
                                    result = {"result": await request.app.state.sandbox_executor.execute_python_async(
                                        call["arguments"].get("code", ""), call["arguments"].get("arguments", {}),
                                    )}
                                else:
                                    expected = registry_snapshot.get(call["name"])
                                    if expected is None:
                                        raise PermissionError("工具已不可用")
                                    result = {"result": await registry.execute(
                                        call["name"], call["arguments"], approved=approved, expected=expected,
                                    )}
                            except ApprovalRequired:
                                result = {"error": "用户未批准此操作；不要再次执行该操作"}
                            except (SandboxUnavailable, WindowsToolError) as error:
                                result = {"error": str(error)}
                            except Exception:
                                result = {"error": "工具执行失败，请检查工具配置"}
                        if "error" in result:
                            yield trace("tool_completed", "failed", f"工具 {tool_name}：{result['error']}")
                        else:
                            yield trace("tool_completed", "completed", f"工具执行完成：{tool_name}")
                        execution_messages.append({"role": "tool", "tool_call_id": call["id"], "content": json.dumps(result, ensure_ascii=False)[:12000]})
                if step is not None:
                    yield trace("plan_step_completed", "completed", f"第 {step_index}/{len(steps)} 步已完成")
            yield trace("run_completed", "completed", "调试运行完成")
            yield _frame("done", {})
        except ProviderError as error:
            yield trace("model_error", "failed", "模型调用或运行失败")
            yield _frame("error", {"message": str(error)})
        except Exception:
            yield trace("run_failed", "failed", "调试运行失败")
            yield _frame("error", {"message": "调试运行失败，请检查工具配置或运行日志"})

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
