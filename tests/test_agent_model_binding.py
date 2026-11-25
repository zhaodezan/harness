import json

import httpx
from fastapi.testclient import TestClient

from backend.app.db import database_connection
from backend.app.main import create_app


class MemorySecrets:
    def __init__(self, values=None): self.values = dict(values or {})
    def get(self, name): return self.values.get(name)
    def set(self, name, value): self.values[name] = value
    def delete(self, name): self.values.pop(name, None)


def successful_transport():
    async def provider(request):
        body = json.loads(await request.aread())
        if body.get("stream") is False:
            return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n')
    return httpx.MockTransport(provider)


def add_model(client, *, name, model, key):
    response = client.post("/api/models", json={
        "name": name, "base_url": "https://example.test/v1", "model": model, "api_key": key,
    })
    assert response.status_code == 201, response.text
    model = response.json()
    with database_connection(client.app.state.database_path) as connection:
        connection.execute("UPDATE model_configs SET last_tested_at=CURRENT_TIMESTAMP WHERE id=?", (model["id"],))
    return model


def test_agent_model_reference_is_required_and_must_exist(tmp_path):
    with TestClient(create_app(tmp_path / "agents.sqlite3", data_dir=tmp_path / "data", http_transport=successful_transport())) as client:
        missing = client.post("/api/agents", json={"name": "No model"})
        nonexistent = client.post("/api/agents", json={"name": "Bad ref", "model_config_id": "missing"})
        model = add_model(client, name="Main", model="m", key="secret")
        created = client.post("/api/agents", json={"name": "With model", "model_config_id": model["id"]})
    assert missing.status_code == 422
    assert nonexistent.status_code == 422
    assert created.status_code == 201
    assert created.json()["model_config_id"] == model["id"]


def test_agents_share_models_but_can_switch_independently(tmp_path):
    with TestClient(create_app(tmp_path / "sharing.sqlite3", data_dir=tmp_path / "data", http_transport=successful_transport())) as client:
        shared = add_model(client, name="Shared", model="m1", key="k1")
        other = add_model(client, name="Other", model="m2", key="k2")
        first = client.post("/api/agents", json={"name": "First", "model_config_id": shared["id"]}).json()
        second = client.post("/api/agents", json={"name": "Second", "model_config_id": shared["id"]}).json()
        changed = client.patch(f"/api/agents/{first['id']}", json={"model_config_id": other["id"]})
        first_after = client.get(f"/api/agents/{first['id']}").json()
        second_after = client.get(f"/api/agents/{second['id']}").json()
        edited_model = client.patch(f"/api/models/{shared['id']}", json={"model": "m1-updated"}).json()

    assert changed.status_code == 200
    assert first_after["model_config_id"] == other["id"]
    assert second_after["model_config_id"] == shared["id"]
    assert edited_model["model"] == "m1-updated"


def test_agent_connection_and_chat_use_selected_model_credentials(tmp_path):
    sent = []

    async def provider(request):
        if request.url.host == "example.test":
            return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
        body = json.loads(await request.aread())
        sent.append((body, request.headers.get("authorization")))
        if request.headers.get("authorization") != "Bearer shared-secret":
            return httpx.Response(401, json={"error": {"message": "unauthorized"}})
        if body.get("stream") is False:
            return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n')

    secrets = MemorySecrets()
    app = create_app(tmp_path / "runtime.sqlite3", data_dir=tmp_path / "data", secret_store=secrets,
                     http_transport=httpx.MockTransport(provider))
    with TestClient(app) as client:
        model = add_model(client, name="Shared", model="model-shared", key="shared-secret")
        agent = client.post("/api/agents", json={
            "name": "Runtime", "model_config_id": model["id"],
        }).json()
        connection = client.post(f"/api/agents/{agent['id']}/test-connection")
        session = client.post("/api/sessions", json={"agent_id": agent["id"]}).json()
        chat = client.post(f"/api/sessions/{session['id']}/messages", json={"content": "hello"})

    assert connection.status_code == 200
    assert chat.status_code == 200
    assert all(auth == "Bearer shared-secret" for _, auth in sent)
    assert all(body["model"] == "model-shared" for body, _ in sent)


def test_model_can_only_be_assigned_to_new_agent_after_successful_test(tmp_path):
    async def provider(request):
        body = json.loads(await request.aread())
        if body.get("stream") is False:
            return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n')

    with TestClient(create_app(tmp_path / "tested.sqlite3", data_dir=tmp_path / "data",
                               secret_store=MemorySecrets(), http_transport=httpx.MockTransport(provider))) as client:
        model_response = client.post("/api/models", json={
            "name": "Untested", "base_url": "https://example.test/v1", "model": "m", "api_key": "key",
        })
        model = model_response.json()
        with database_connection(client.app.state.database_path) as connection:
            connection.execute("UPDATE model_configs SET last_tested_at=NULL WHERE id=?", (model["id"],))
        rejected = client.post("/api/agents", json={"name": "Before test", "model_config_id": model["id"]})
        assert rejected.status_code == 409
        tested = client.post(f"/api/models/{model['id']}/test-connection")
        accepted = client.post("/api/agents", json={"name": "After test", "model_config_id": model["id"]})
    assert tested.status_code == 200
    assert accepted.status_code == 201


def test_legacy_migrated_agent_can_keep_using_untested_model(tmp_path):
    database = tmp_path / "legacy-bound.sqlite3"
    from backend.app.db import initialize_database
    initialize_database(database)
    with database_connection(database) as connection:
        connection.execute(
            "INSERT INTO agents(id,name,base_url,model) VALUES(?,?,?,?)",
            ("old", "Old", "https://old.test/v1", "old-model"),
        )
    secrets = MemorySecrets({"agent:old:api_key": "old-key"})
    with TestClient(create_app(database, data_dir=tmp_path / "data", secret_store=secrets)) as client:
        agent = client.get("/api/agents/old").json()
        changed = client.patch(f"/api/agents/{agent['id']}", json={"name": "Old renamed"})
    assert changed.status_code == 200
    assert changed.json()["name"] == "Old renamed"
