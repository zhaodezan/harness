import asyncio
import json

import pytest

from backend.app.tools import ApprovalRequired, ToolDefinition, ToolRegistry


def test_registered_readonly_tool_executes_with_validated_input():
    registry = ToolRegistry()
    registry.register(ToolDefinition(
        name="lookup",
        description="Find a value",
        input_schema={"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"], "additionalProperties": False},
        risk="read_only",
        handler=lambda arguments: {"value": arguments["key"]},
    ))

    result = asyncio.run(registry.execute("lookup", {"key": "alpha"}, approved=False))

    assert result == {"value": "alpha"}


def test_side_effect_tool_never_runs_before_approval():
    calls = []
    registry = ToolRegistry()
    registry.register(ToolDefinition(
        name="send_message",
        description="Send a message",
        input_schema={"type": "object", "properties": {"body": {"type": "string"}}, "required": ["body"], "additionalProperties": False},
        risk="side_effect",
        handler=lambda arguments: calls.append(arguments),
    ))

    with pytest.raises(ApprovalRequired):
        asyncio.run(registry.execute("send_message", {"body": "hello"}, approved=False))

    assert calls == []


def test_unknown_tool_and_invalid_input_are_rejected():
    registry = ToolRegistry()
    registry.register(ToolDefinition(
        name="echo", description="Echo", input_schema={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"], "additionalProperties": False},
        risk="read_only", handler=lambda args: args["text"],
    ))
    with pytest.raises(KeyError):
        asyncio.run(registry.execute("missing", {}, approved=False))
    with pytest.raises(ValueError):
        asyncio.run(registry.execute("echo", {"unexpected": True}, approved=False))
