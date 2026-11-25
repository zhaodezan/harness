from fastapi.testclient import TestClient

from backend.app.events import record_event
from backend.app.main import create_app


def test_logs_filter_by_level_and_subsystem(tmp_path):
    database_path = tmp_path / "filter-logs.sqlite3"
    with TestClient(create_app(database_path)) as client:
        record_event(database_path, run_id="a", event_type="tool", status="done", level="info", subsystem="mcp", summary="connected")
        record_event(database_path, run_id="b", event_type="error", status="failed", level="error", subsystem="runtime", summary="provider timeout")
        result = client.get("/api/logs?level=error&subsystem=runtime").json()
    assert [item["run_id"] for item in result] == ["b"]
