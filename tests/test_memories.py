from fastapi.testclient import TestClient

from backend.app.db import database_connection
from backend.app.main import create_app
from model_test_utils import create_test_agent


def test_memory_is_persistent_and_scoped_to_global_or_agent(tmp_path):
    with TestClient(create_app(tmp_path / "memory.sqlite3")) as client:
        agent = create_test_agent(client, {"name": "Writing"}).json()
        other = create_test_agent(client, {"name": "Research"}).json()
        saved = client.post("/api/memories", json={
            "content": "用户偏好简洁、条理清楚的中文回答",
            "scope": "agent", "agent_id": agent["id"], "source": "user_confirmed",
        })
        assert saved.status_code == 201
        assert "items" not in saved.json()
        listed = client.get(f"/api/memories?agent_id={agent['id']}").json()
        assert len(listed) == 1
        assert client.get(f"/api/memories?agent_id={other['id']}").json() == []

        memory_id = saved.json()["id"]
        assert client.patch(f"/api/memories/{memory_id}", json={"content": "偏好简洁中文"}).status_code == 200
        assert client.delete(f"/api/memories/{memory_id}").status_code == 204
        assert client.get("/api/memories").json() == []


def test_scoped_memory_management_never_clears_other_scopes(tmp_path):
    path = tmp_path / "memory.sqlite3"
    with TestClient(create_app(path)) as client:
        with database_connection(path) as connection:
            connection.execute("INSERT INTO agents(id,name) VALUES('agent','Writing'), ('other','Research')")
        global_scope = {"scope": "global"}
        agent_scope = {"scope": "agent", "agent_id": "agent"}
        other_scope = {"scope": "agent", "agent_id": "other"}
        for scope, text in [(global_scope, "global"), (agent_scope, "dedicated"), (other_scope, "other")]:
            assert client.post("/api/memories", json={**scope, "content": text}).status_code == 201
        assert [row["content"] for row in client.get("/api/memories", params=global_scope).json()] == ["global"]
        assert [row["content"] for row in client.get("/api/memories", params=agent_scope).json()] == ["dedicated"]
        assert client.delete("/api/memories", params={"scope": "agent"}).status_code == 422
        assert client.delete("/api/memories", params=global_scope).status_code == 204
        assert client.delete("/api/memories", params=agent_scope).status_code == 204
        assert [row["content"] for row in client.get("/api/memories").json()] == ["other"]


def test_memory_rejects_agent_scope_without_agent_id(tmp_path):
    with TestClient(create_app(tmp_path / "memory.sqlite3")) as client:
        response = client.post("/api/memories", json={
            "content": "preference", "scope": "agent", "source": "user_confirmed"
        })

    assert response.status_code == 422
