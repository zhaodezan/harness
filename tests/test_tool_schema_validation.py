import pytest

from backend.app.tool_plugins import LocalToolManager
from backend.app.tools import ToolRegistry


def test_tool_schema_rejects_required_parameter_missing_from_properties(tmp_path):
    source = '''TOOL = {
        "name": "bad_schema",
        "description": "Invalid schema",
        "input_schema": {"type": "object", "properties": {}, "required": ["missing"]},
        "risk": "read_only",
    }
def run(arguments):
    return "ok"
'''
    manager = LocalToolManager(tmp_path / "tools", ToolRegistry())
    with pytest.raises(ValueError, match="required"):
        manager.install_source(source)
