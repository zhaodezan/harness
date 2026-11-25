import sqlite3

from fastapi.testclient import TestClient

from backend.app.db import database_connection, initialize_database
from backend.app.main import create_app


def test_startup_prunes_expired_events_and_preserves_sessions(tmp_path):
    database_path = tmp_path / "maintenance.sqlite3"
    initialize_database(database_path)
    with database_connection(database_path) as connection:
        connection.execute("INSERT INTO run_events(occurred_at,run_id,level,subsystem,event_type,status) VALUES('2000-01-01T00:00:00+00:00','old','info','runtime','old','done')")
        connection.execute("INSERT INTO run_events(occurred_at,run_id,level,subsystem,event_type,status) VALUES('2099-01-01T00:00:00+00:00','future','info','runtime','future','done')")
    with TestClient(create_app(database_path)):
        pass
    with sqlite3.connect(database_path) as connection:
        remaining = connection.execute("SELECT run_id FROM run_events").fetchall()

    assert remaining == [("future",)]
