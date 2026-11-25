from backend.app.db import database_connection, initialize_database
from backend.app.main import create_app
from fastapi.testclient import TestClient


def test_restart_marks_only_incomplete_runs_interrupted(tmp_path):
    database_path = tmp_path / "restart.sqlite3"
    initialize_database(database_path)
    with database_connection(database_path) as connection:
        connection.execute("INSERT INTO run_events(run_id,event_type,status,level,subsystem,summary) VALUES('unfinished','run_started','running','info','runtime','started')")
        connection.execute("INSERT INTO run_events(run_id,event_type,status,level,subsystem,summary) VALUES('finished','run_started','running','info','runtime','started')")
        connection.execute("INSERT INTO run_events(run_id,event_type,status,level,subsystem,summary) VALUES('finished','run_completed','completed','info','runtime','finished')")

    with TestClient(create_app(database_path)) as client:
        events = client.get("/api/logs").json()
    starts = {event["run_id"]: event for event in events if event["event_type"] == "run_started"}
    assert starts["unfinished"]["status"] == "interrupted"
    assert starts["finished"]["status"] == "running"
