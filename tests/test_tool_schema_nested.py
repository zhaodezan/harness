import asyncio

import pytest

from backend.app.tools import ToolDefinition, ToolRegistry


def test_nested_json_schema_validates_required_and_enum_values():
    registry = ToolRegistry()
    registry.register(ToolDefinition(
        name="nested", description="Nested", risk="read_only",
        input_schema={"type": "object", "properties": {
            "profile": {"type": "object", "properties": {"role": {"type": "string", "enum": ["reader", "writer"]}}, "required": ["role"], "additionalProperties": False}
        }, "required": ["profile"], "additionalProperties": False},
        handler=lambda args: args,
    ))
    with pytest.raises(ValueError, match="必填"):
        asyncio.run(registry.execute("nested", {"profile": {}}, approved=False))
    with pytest.raises(ValueError, match="允许值"):
        asyncio.run(registry.execute("nested", {"profile": {"role": "admin"}}, approved=False))


def test_array_items_and_numeric_bounds_are_validated():
    registry = ToolRegistry()
    registry.register(ToolDefinition(
        name="bounded", description="Bounded", risk="read_only",
        input_schema={"type": "object", "properties": {
            "count": {"type": "integer", "minimum": 1, "maximum": 4},
            "tags": {"type": "array", "items": {"type": "string", "maxLength": 5}},
        }, "required": ["count", "tags"], "additionalProperties": False},
        handler=lambda args: args,
    ))
    with pytest.raises(ValueError, match="范围"):
        asyncio.run(registry.execute("bounded", {"count": 5, "tags": []}, approved=False))
    with pytest.raises(ValueError, match="参数 tags"):
        asyncio.run(registry.execute("bounded", {"count": 2, "tags": [1]}, approved=False))
