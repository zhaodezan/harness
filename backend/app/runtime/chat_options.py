"""Per-message capability selections for the local chat composer."""
import json
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from backend.app.skills import discover_skills
from backend.app.tools import filter_tools_for_agent

OptionId = Annotated[str, Field(min_length=1, max_length=1024)]


class ChatCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid")

    local_tools: list[OptionId] = Field(default_factory=list, max_length=100)
    mcp_tools: list[OptionId] = Field(default_factory=list, max_length=100)
    skills: list[OptionId] = Field(default_factory=list, max_length=50)
    agents: list[OptionId] = Field(default_factory=list, max_length=20)


def chat_options(connection, state, agent=None):
    """Return the public catalog and its corresponding private Skill objects."""
    local_ids = {tool["id"] for tool in state.local_tool_manager.list_tools() if tool["status"] == "loaded"}
    mcp_names = {
        row["id"]: row["name"] for row in connection.execute("SELECT id, name FROM mcp_servers WHERE enabled=1")
        if row["id"] in state.mcp_manager.servers
    }
    options = {key: [] for key in ChatCapabilities.model_fields}
    for tool in state.tool_registry.list():
        tool_id = tool.get("plugin_id") or ""
        if tool_id in local_ids:
            options["local_tools"].append({"id": tool_id, "name": tool["name"]})
        elif tool_id.startswith("mcp:"):
            parts = tool_id.split(":", 2)
            if len(parts) == 3 and parts[1] in mcp_names:
                options["mcp_tools"].append({"id": tool_id, "name": parts[2], "group": mcp_names[parts[1]]})

    legacy = discover_skills(agent["skill_directory"]) if agent is not None and agent["skill_directory"] else []
    skills = {skill.name: skill for skill in [*legacy, *state.plugin_store.list_skills()]}
    options["skills"] = [
        {"id": name, "name": name, "description": skill.description[:300]} for name, skill in skills.items()
    ]
    source_id = agent["agent_id"] if agent is not None else None
    options["agents"] = [dict(row) for row in connection.execute(
        "SELECT id, name FROM agents WHERE (? IS NULL OR id != ?) ORDER BY name COLLATE NOCASE, id",
        (source_id, source_id),
    )]
    for items in options.values():
        items.sort(key=lambda item: (item.get("group", "").casefold(), item["name"].casefold()))
    return options, skills


def default_chat_capabilities(agent, options):
    selected = {kind: set(json.loads(agent[f"selected_{kind}_json"] or "[]")) for kind in options}
    selected_mcp = {tool["plugin_id"] for tool in filter_tools_for_agent(
        [{"plugin_id": item["id"]} for item in options["mcp_tools"]], list(selected["mcp_tools"]), [],
    )}
    return {
        kind: [item["id"] for item in items if item["id"] in (selected_mcp if kind == "mcp_tools" else selected[kind])]
        for kind, items in options.items()
    }


def validate_chat_capabilities(capabilities: ChatCapabilities, options) -> dict[str, list[str]]:
    selected = {}
    labels = {"local_tools": "Python Tool", "mcp_tools": "MCP 工具", "skills": "Skill", "agents": "Agent"}
    for kind, ids in capabilities.model_dump().items():
        available = {item["id"] for item in options[kind]}
        if any(item not in available for item in ids):
            raise ValueError(f"所选 {labels[kind]} 已不可用或不允许使用，请重新选择。")
        selected[kind] = list(dict.fromkeys(ids))
    return selected
