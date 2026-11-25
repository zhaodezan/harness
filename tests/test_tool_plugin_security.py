import pytest

from backend.app.tool_plugins import LocalToolManager
from backend.app.tools import ToolRegistry


def test_tool_manager_rejects_path_traversal_ids(tmp_path):
    manager = LocalToolManager(tmp_path / "tools", ToolRegistry())
    outside = tmp_path / "escape.py"
    outside.write_text("leave unchanged", encoding="utf-8")

    assert manager.delete_tool("..") is False
    with pytest.raises(KeyError):
        manager.update_source("..", "print('overwrite')")
    assert outside.read_text(encoding="utf-8") == "leave unchanged"
