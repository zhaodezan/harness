from backend.app.tool_plugins import LocalToolManager
from backend.app.tools import ToolRegistry


def source(name):
    return f'''TOOL = {{
    "name": {name!r},
    "description": "Tool",
    "input_schema": {{"type": "object", "properties": {{}}}},
    "risk": "read_only",
}}
def run(arguments):
    return {name!r}
'''


def test_failed_name_collision_update_keeps_other_tool_registered(tmp_path):
    registry = ToolRegistry()
    manager = LocalToolManager(tmp_path / "tools", registry)
    first = manager.install_source(source("first"))
    manager.install_source(source("second"))

    try:
        manager.update_source(first["id"], source("second"))
    except ValueError:
        pass

    assert {tool["name"] for tool in registry.list()} == {"first", "second"}
