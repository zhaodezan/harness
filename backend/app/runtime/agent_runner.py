import json

from backend.app.db import database_connection
from backend.app.prompts import render_prompt
from backend.app.provider.openai_compatible import ProviderError
from backend.app.provider.streaming import stream_chat_events
from backend.app.runtime.model_rounds import ModelRoundCounter
from backend.app.runtime.skill_context import build_skill_context
from backend.app.skills import discover_skills
from backend.app.tools import filter_tools_for_agent, model_tool_names


class AgentTaskRunner:
    """Run delegated work using the target Agent's own read-only capabilities."""

    def __init__(self, app, a2a_service):
        self.app = app
        self.a2a_service = a2a_service

    async def __call__(self, agent_id: str, task_text: str, budget) -> str:
        with database_connection(self.app.state.database_path) as connection:
            row = connection.execute(
                "SELECT a.id,a.name,a.system_prompt,a.prompt_variables_json,a.skill_directory,a.selected_skills_json,"
                "a.selected_agents_json,a.selected_mcp_tools_json,a.selected_local_tools_json,a.selected_database_connections_json,a.max_rounds,"
                "m.base_url,m.model,a.model_config_id FROM agents a "
                "LEFT JOIN model_configs m ON m.id=a.model_config_id WHERE a.id=?", (agent_id,),
            ).fetchone()
        if row is None or not row["base_url"] or not row["model"]:
            raise ProviderError("被委派的 Agent 模型配置不可用")
        api_key = self.app.state.secret_store.get(f"model:{row['model_config_id']}:api_key")
        if not api_key:
            raise ProviderError("被委派的 Agent 尚未配置模型凭据")
        try:
            prompt = render_prompt(row["system_prompt"], json.loads(row["prompt_variables_json"] or "{}"))
        except ValueError:
            prompt = row["system_prompt"]
        messages = [{"role": "system", "content": prompt}] if prompt else []
        managed = self.app.state.plugin_store.list_skills()
        legacy = discover_skills(row["skill_directory"]) if row["skill_directory"] else []
        skill_context = build_skill_context(json.loads(row["selected_skills_json"] or "[]"), managed, legacy, task_text)
        messages.extend(skill_context.messages)
        messages.append({"role": "user", "content": task_text})

        selected_mcp = json.loads(row["selected_mcp_tools_json"] or "[]")
        selected_local = json.loads(row["selected_local_tools_json"] or "[]")
        selected_databases = json.loads(row["selected_database_connections_json"] or "[]")
        registered = filter_tools_for_agent(
            self.app.state.tool_registry.list(), selected_mcp, selected_local, selected_databases
        )
        # Local Python tools stay unavailable to delegated tasks until sandbox-backed.
        registered = [tool for tool in registered if tool["risk"] == "read_only" and tool.get("plugin_id") not in selected_local]
        delegate_tool = None
        if len(budget.chain) - 1 < 2 and budget.calls < 4:
            delegate_tool, _ = self.a2a_service.create_delegate_tool(
                agent_id, json.loads(row["selected_agents_json"] or "[]"), registered
            )
            if delegate_tool:
                registered.append(delegate_tool)
        names = model_tool_names([tool["name"] for tool in registered])
        reverse_names = {alias: name for name, alias in names.items()}
        model_tools = [{"type": "function", "function": {
            "name": names[tool["name"]], "description": tool["description"], "parameters": tool["input_schema"],
        }} for tool in registered]

        round_counter = ModelRoundCounter(row['max_rounds'])
        while True:
            round_counter.start_call()
            tool_calls = []
            answer = []
            async for event in stream_chat_events(
                self.app.state.http_client, base_url=row["base_url"], model=row["model"],
                api_key=api_key, messages=messages, tools=model_tools,
            ):
                if event["type"] == "text":
                    answer.append(event["text"])
                elif event["type"] == "tool_calls":
                    tool_calls = event["tool_calls"]
                    for call in tool_calls:
                        call["name"] = reverse_names.get(call["name"], "")
            if not tool_calls:
                return "".join(answer).strip()
            messages.append({"role": "assistant", "content": None, "tool_calls": [{
                "id": call["id"], "type": "function",
                "function": {"name": call["name"], "arguments": json.dumps(call["arguments"], ensure_ascii=False)},
            } for call in tool_calls]})
            for call in tool_calls:
                definition = next((tool for tool in registered if tool["name"] == call["name"]), None)
                try:
                    if definition is None:
                        raise PermissionError("工具未授权或已禁用")
                    if delegate_tool and call["name"] == delegate_tool["name"]:
                        delegated = await self.a2a_service.delegate(
                            agent_id, call["arguments"].get("agent_id"), call["arguments"].get("task"), budget
                        )
                        result = delegated.model_dump(mode="json")
                    else:
                        plugin_id = definition.get("plugin_id") or ""
                        if plugin_id.startswith("database:"):
                            parts = plugin_id.split(":")
                            if len(parts) != 3 or parts[1] not in selected_databases:
                                raise PermissionError("数据库连接未授权")
                            if not self.app.state.database_manager.is_tool_authorized(agent_id, plugin_id):
                                raise PermissionError("数据库连接授权已撤销")
                        result = await self.app.state.tool_registry.execute(call["name"], call["arguments"], approved=True)
                    result = {"result": result}
                except Exception:
                    result = {"error": "被委派 Agent 的只读工具执行失败或未获授权"}
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": json.dumps(result, ensure_ascii=False)[:12000]})
