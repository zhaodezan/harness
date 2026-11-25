from fastapi.testclient import TestClient

from backend.app.main import create_app
from model_test_utils import create_test_agent


def test_session_can_be_renamed(tmp_path):
    with TestClient(create_app(tmp_path / "rename.sqlite3")) as client:
        agent = create_test_agent(client, {"name": "Helper"}).json()
        session = client.post("/api/sessions", json={"agent_id": agent["id"]}).json()
        changed = client.patch(f"/api/sessions/{session['id']}", json={"title": "Project notes"})
        assert changed.status_code == 200
        assert changed.json()["title"] == "Project notes"
