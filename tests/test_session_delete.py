from fastapi.testclient import TestClient

from backend.app.db import database_connection
from backend.app.main import create_app
from model_test_utils import create_test_agent


def test_deleting_session_cascades_messages_only_for_that_session(tmp_path):
    database_path = tmp_path / "delete-session.sqlite3"
    with TestClient(create_app(database_path)) as client:
        agent = create_test_agent(client, {"name": "Helper"}).json()
        first = client.post("/api/sessions", json={"agent_id": agent["id"]}).json()
        second = client.post("/api/sessions", json={"agent_id": agent["id"]}).json()
        with database_connection(database_path) as connection:
            connection.execute("INSERT INTO messages(id,session_id,role,content) VALUES('m1',?,'user','one')", (first["id"],))
            connection.execute("INSERT INTO messages(id,session_id,role,content) VALUES('m2',?,'user','two')", (second["id"],))
        deleted = client.delete(f"/api/sessions/{first['id']}")
        assert deleted.status_code == 204
        assert client.get(f"/api/sessions/{first['id']}/messages").status_code == 404
        assert [m["content"] for m in client.get(f"/api/sessions/{second['id']}/messages").json()] == ["two"]
