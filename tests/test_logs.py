import json

from fastapi.testclient import TestClient

from backend.app.events import record_event
from backend.app.main import create_app


def test_run_events_can_be_searched_and_exported_as_jsonl(tmp_path):
    database_path = tmp_path / "logs.sqlite3"
    with TestClient(create_app(database_path)) as client:
        record_event(
            database_path, run_id="run-1", session_id="session-1", level="info",
            subsystem="runtime", event_type="run_started", status="running",
            summary="Agent run started",
        )
        result = client.get("/api/logs?q=run_started&run_id=run-1").json()
        assert len(result) == 1
        assert result[0]["summary"] == "Agent run started"
        exported = client.get("/api/logs/export?format=jsonl").text

    assert json.loads(exported.splitlines()[0])["run_id"] == "run-1"


def test_expired_events_are_removed_without_touching_messages(tmp_path):
    database_path = tmp_path / "logs.sqlite3"
    with TestClient(create_app(database_path)) as client:
        record_event(
            database_path, run_id="old-run", level="info", subsystem="runtime",
            event_type="old", status="done", summary="expired",
            occurred_at="2000-01-01T00:00:00+00:00",
        )
        record_event(
            database_path, run_id="new-run", level="info", subsystem="runtime",
            event_type="new", status="done", summary="current",
        )
        logs = client.get("/api/logs").json()

    assert [event["run_id"] for event in logs] == ["new-run"]
