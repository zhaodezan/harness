from __future__ import annotations

import asyncio
import hashlib
import inspect
import re
from dataclasses import dataclass
from typing import Any, Callable, Literal


RiskLevel = Literal["read_only", "side_effect"]
ToolHandler = Callable[[dict[str, Any]], Any]


def model_tool_names(names: list[str], *, max_length: int = 64) -> dict[str, str]:
    """Create unique provider-compatible names and retain a reversible map."""
    result: dict[str, str] = {}
    used: set[str] = set()
    for original in names:
        sanitized = re.sub(r"[^A-Za-z0-9_-]", "_", original)
        if not sanitized or not sanitized[0].isalpha():
            sanitized = f"tool_{sanitized}"
        if len(sanitized) > max_length or sanitized in used or sanitized != original:
            digest = hashlib.sha256(original.encode("utf-8")).hexdigest()[:10]
            sanitized = f"{sanitized[:max_length - len(digest) - 1]}_{digest}"
        if sanitized in used:
            raise ValueError("无法为工具生成唯一的模型名称")
        used.add(sanitized)
        result[original] = sanitized
    return result


class ApprovalRequired(PermissionError):
    """Raised before a side-effecting handler is invoked without approval."""


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]
    risk: RiskLevel
    handler: ToolHandler
    plugin_id: str | None = None


def filter_tools_for_agent(
    tools: list[dict[str, Any]], selected_mcp_tools: list[str], selected_local_tools: list[str],
    selected_database_connections: list[str] | None = None,
) -> list[dict[str, Any]]:
    selected = set(selected_mcp_tools) | set(selected_local_tools)
    selected_database = set(selected_database_connections or [])
    def allowed(tool):
        plugin_id = tool.get("plugin_id")
        if plugin_id is None:
            return True
        if plugin_id.startswith("database:"):
            parts = plugin_id.split(":")
            return len(parts) == 3 and parts[1] in selected_database
        return plugin_id in selected or (
            plugin_id.startswith("mcp:") and ":".join(plugin_id.split(":")[:2]) + ":*" in selected
        )

    return [tool for tool in tools if allowed(tool)]


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> None:
        if not tool.name or tool.name in self._tools:
            raise ValueError("Tool 名称不能为空且不能重复")
        if tool.risk not in ("read_only", "side_effect"):
            raise ValueError("Tool 风险级别无效")
        if tool.input_schema.get("type") != "object":
            raise ValueError("Tool 参数 Schema 必须是 object")
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    def unregister_prefix(self, prefix: str) -> None:
        if not prefix:
            return
        for name in [name for name in self._tools if name.startswith(prefix)]:
            self._tools.pop(name, None)

    def list(self) -> list[dict[str, Any]]:
        return [
            {"name": tool.name, "description": tool.description,
             "input_schema": tool.input_schema, "risk": tool.risk,
             "plugin_id": tool.plugin_id}
            for tool in self._tools.values()
        ]

    def get_definition(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    async def execute(
        self, name: str, arguments: dict[str, Any], *, approved: bool, expected: ToolDefinition | None = None,
    ) -> Any:
        tool = self._tools.get(name)
        if tool is None or (expected is not None and expected is not tool):
            raise KeyError(f"工具不存在或已变更: {name}")
        self._validate(tool.input_schema, arguments)
        if tool.risk == "side_effect" and not approved:
            raise ApprovalRequired(f"工具 {name} 需要用户确认")
        if inspect.iscoroutinefunction(tool.handler):
            result = await tool.handler(arguments)
        else:
            result = await asyncio.to_thread(tool.handler, arguments)
            if inspect.isawaitable(result):
                result = await result
        return result

    @classmethod
    def _validate(cls, schema: dict[str, Any], value: Any) -> None:
        cls._validate_value(schema, value, path="参数")

    @classmethod
    def _validate_value(cls, schema: dict[str, Any], value: Any, *, path: str) -> None:
        if "enum" in schema and value not in schema["enum"]:
            raise ValueError(f"{path}只能是允许值: {', '.join(map(str, schema['enum']))}")
        if schema.get("type") == "object":
            if not isinstance(value, dict):
                raise ValueError(f"{path}必须是 JSON 对象")
            required = schema.get("required", [])
            missing = [name for name in required if name not in value]
            if missing:
                raise ValueError(f"{path}缺少必填参数: {', '.join(missing)}")
            properties = schema.get("properties", {})
            if schema.get("additionalProperties") is False:
                unexpected = set(value) - set(properties)
                if unexpected:
                    raise ValueError(f"{path}包含未定义参数: {', '.join(sorted(unexpected))}")
            for name, item in value.items():
                if name in properties:
                    cls._validate_value(properties[name], item, path=f"参数 {name}")
            return
        expected_types = {
            "string": str, "integer": int, "number": (int, float),
            "boolean": bool, "object": dict, "array": list,
        }
        expected = expected_types.get(schema.get("type"))
        if expected is not None and (
            not isinstance(value, expected)
            or (schema.get("type") == "integer" and isinstance(value, bool))
            or (schema.get("type") == "number" and isinstance(value, bool))
        ):
            raise ValueError(f"{path}类型不符合要求")
        if isinstance(value, str) and len(value) > schema.get("maxLength", 100_000):
            raise ValueError(f"{path}超出长度限制")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if "minimum" in schema and value < schema["minimum"] or "maximum" in schema and value > schema["maximum"]:
                raise ValueError(f"{path}超出允许范围")
        if isinstance(value, list):
            if "minItems" in schema and len(value) < schema["minItems"] or "maxItems" in schema and len(value) > schema["maxItems"]:
                raise ValueError(f"{path}项目数量超出范围")
            if "items" in schema:
                for index, item in enumerate(value):
                    cls._validate_value(schema["items"], item, path=f"参数 {path} 的第 {index + 1} 项")
