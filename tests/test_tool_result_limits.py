import json

from backend.app.routes.sessions import _bounded_tool_result


def test_large_tool_result_is_valid_json_with_explicit_truncation_notice():
    encoded = _bounded_tool_result({"result": "x" * 50000}, limit=1000)
    parsed = json.loads(encoded)
    assert parsed["truncated"] is True
    assert "工具结果过大" in parsed["message"]
    assert len(encoded) < 1000
