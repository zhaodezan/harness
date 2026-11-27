import asyncio

import pytest

from backend.app.tool_plugins import LocalToolManager
from backend.app.tools import ApprovalRequired, ToolRegistry


READ_ONLY_SOURCE = '''TOOL = {
    "name": "lookup",
    "description": "Lookup a value",
    "input_schema": {
        "type": "object",
        "properties": {"key": {"type": "string", "maxLength": 20}},
        "required": ["key"],
        "additionalProperties": False,
    },
    "risk": "read_only",
}
def run(arguments):
    return arguments["key"] + "-result"
'''
LEGACY_READ_ONLY_SOURCE = '''TOOL = {
    "name": "legacy_lookup",
    "description": "Legacy lookup",
    "input_schema": {
        "type": "object",
        "properties": {"key": {"type": "string"}},
        "required": ["key"],
    },
    "risk": "read_only",
}
def run(arguments):
    return arguments["key"] + "-result"
'''


def test_legacy_read_only_claim_requires_approval_before_running(tmp_path):
    calls = []

    def sandbox_executor(source, arguments):
        calls.append((source, arguments))
        return 'executed'

    registry = ToolRegistry()
    manager = LocalToolManager(tmp_path / 'tools', registry, sandbox_executor=sandbox_executor)
    item = manager.install_source(LEGACY_READ_ONLY_SOURCE)

    assert item['risk'] == 'side_effect'
    with pytest.raises(ApprovalRequired):
        asyncio.run(registry.execute('legacy_lookup', {'key': 'x'}, approved=False))
    assert calls == []
    assert asyncio.run(registry.execute('legacy_lookup', {'key': 'x'}, approved=True)) == 'executed'
    assert calls == [(LEGACY_READ_ONLY_SOURCE, {'key': 'x'})]


def test_install_source_registers_declared_local_tool(tmp_path):
    registry = ToolRegistry()
    manager = LocalToolManager(tmp_path / "tools", registry, sandbox_executor=lambda source, args: args["key"] + "-result")

    item = manager.install_source(READ_ONLY_SOURCE)

    assert item["name"] == "lookup"
    assert item["description"] == "Lookup a value"
    assert item["risk"] == "side_effect"
    assert asyncio.run(registry.execute("lookup", {"key": "x"}, approved=True)) == "x-result"


def test_install_rejects_invalid_tool_metadata_without_saving_source(tmp_path):
    root = tmp_path / "tools"
    manager = LocalToolManager(root, ToolRegistry())

    with pytest.raises(ValueError, match="TOOL"):
        manager.install_source("def run(arguments):\n    return None\n")

    assert list(root.glob("*.py")) == []


def test_install_rejects_duplicate_tool_names(tmp_path):
    manager = LocalToolManager(tmp_path / "tools", ToolRegistry())
    manager.install_source(READ_ONLY_SOURCE)

    with pytest.raises(ValueError, match="重复"):
        manager.install_source(READ_ONLY_SOURCE)


def test_side_effect_tool_requires_approval_before_handler_runs(tmp_path):
    calls = tmp_path / "calls.txt"
    source = f'''TOOL = {{
    "name": "write_value",
    "description": "Write a value",
    "input_schema": {{"type": "object", "properties": {{}}, "additionalProperties": False}},
    "risk": "side_effect",
}}
def run(arguments):
    with open({str(calls)!r}, "a", encoding="utf-8") as output:
        output.write("called")
    return "done"
'''
    registry = ToolRegistry()
    manager = LocalToolManager(tmp_path / "tools", registry)
    manager.install_source(source)

    with pytest.raises(ApprovalRequired):
        asyncio.run(registry.execute("write_value", {}, approved=False))
    assert not calls.exists()
