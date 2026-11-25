from fastapi.testclient import TestClient

from backend.app.main import create_app
from tests.model_test_utils import create_test_model


def test_agent_can_select_multiple_existing_knowledge_bases(tmp_path):
    with TestClient(create_app(tmp_path / "agents.sqlite3", data_dir=tmp_path / "data")) as client:
        first = client.post("/api/knowledge-bases", json={"name": "One"}).json()["id"]
        second = client.post("/api/knowledge-bases", json={"name": "Two"}).json()["id"]
        model_id = create_test_model(client)

        created = client.post("/api/agents", json={
            "name": "Research", "model_config_id": model_id,
            "selected_knowledge_bases": [first, second],
        })

        assert created.status_code == 201
        assert created.json()["selected_knowledge_bases"] == [first, second]
        updated = client.patch(
            f"/api/agents/{created.json()['id']}",
            json={"selected_knowledge_bases": [second]},
        )
        assert updated.status_code == 200
        assert updated.json()["selected_knowledge_bases"] == [second]
        assert client.get(f"/api/agents/{created.json()['id']}").json()["selected_knowledge_bases"] == [second]


def test_agent_rejects_unknown_knowledge_base_ids(tmp_path):
    with TestClient(create_app(tmp_path / "agents.sqlite3", data_dir=tmp_path / "data")) as client:
        model_id = create_test_model(client)
        response = client.post("/api/agents", json={
            "name": "Research", "model_config_id": model_id,
            "selected_knowledge_bases": ["unknown"],
        })

    assert response.status_code == 422


def test_setup_agent_saves_selected_knowledge_bases(tmp_path):
    with TestClient(create_app(tmp_path / "agents.sqlite3", data_dir=tmp_path / "data")) as client:
        knowledge_base = client.post("/api/knowledge-bases", json={"name": "Setup Docs"}).json()
        model_id = create_test_model(client)
        response = client.post("/api/setup/model", json={
            "name": "Setup Agent", "model_config_id": model_id,
            "selected_knowledge_bases": [knowledge_base["id"]],
        })
        assert response.status_code == 201
        agent = client.get(f"/api/agents/{response.json()['id']}").json()
    assert agent["selected_knowledge_bases"] == [knowledge_base["id"]]


def test_agent_without_selected_knowledge_bases_defaults_to_empty(tmp_path):
    with TestClient(create_app(tmp_path / "agents.sqlite3", data_dir=tmp_path / "data")) as client:
        model_id = create_test_model(client)
        created = client.post("/api/agents", json={"name": "Default", "model_config_id": model_id})

    assert created.status_code == 201
    assert created.json()["selected_knowledge_bases"] == []
