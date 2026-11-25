from fastapi.testclient import TestClient

from backend.app.main import create_app
from model_test_utils import create_test_agent


def test_agent_lifecycle_persists_configuration_without_secrets(tmp_path):
    app = create_app(tmp_path / "agents.sqlite3")
    with TestClient(app) as client:
        created = create_test_agent(client, {
            "name": "Research Helper", "base_url": "https://example.test/v1", "model": "model-x",
            "system_prompt": "Be useful.", "mode": "react",
        })
        assert created.status_code == 201
        agent = created.json()
        assert agent["name"] == "Research Helper"
        assert "api_key" not in agent

        listed = client.get("/api/agents")
        assert [item["id"] for item in listed.json()] == [agent["id"]]

        updated = client.patch(
            f"/api/agents/{agent['id']}", json={"name": "Research Assistant"}
        )
        assert updated.status_code == 200
        assert updated.json()["name"] == "Research Assistant"

        deleted = client.delete(f"/api/agents/{agent['id']}")
        assert deleted.status_code == 204
        assert client.get(f"/api/agents/{agent['id']}").status_code == 404


def test_agent_requires_nonblank_name(tmp_path):
    app = create_app(tmp_path / "agents.sqlite3")
    with TestClient(app) as client:
        response = client.post("/api/agents", json={"name": "   "})

    assert response.status_code == 422
