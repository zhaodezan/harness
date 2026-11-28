import json

import httpx
from fastapi.testclient import TestClient

from backend.app.db import database_connection
from backend.app.main import create_app
from model_test_utils import create_test_model


class MemorySecrets:
    def __init__(self): self.values = {}
    def get(self, name): return self.values.get(name)
    def set(self, name, value): self.values[name] = value
    def delete(self, name): self.values.pop(name, None)


def successful_transport():
    async def provider(_request):
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
    return httpx.MockTransport(provider)


def test_model_create_and_list_never_return_credentials(tmp_path):
    secrets = MemorySecrets()
    async def provider(_request):
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
    app = create_app(
        tmp_path / "models.sqlite3", data_dir=tmp_path / "data", secret_store=secrets,
        http_transport=httpx.MockTransport(provider),
    )
    with TestClient(app) as client:
        created = client.post("/api/models", json={
            "name": "Primary", "base_url": "https://example.test/v1",
            "model": "model-a", "api_key": "never-return-this",
        })
        listed = client.get("/api/models")

    assert created.status_code == 201, created.text
    model = created.json()
    assert model["name"] == "Primary"
    assert model["credential_configured"] is True
    assert "api_key" not in model
    assert "never-return-this" not in created.text
    assert "never-return-this" not in listed.text
    assert secrets.get(f"model:{model['id']}:api_key") == "never-return-this"


def test_model_update_test_connection_and_credential_rotation(tmp_path):
    requests = []

    async def provider(request):
        body = json.loads(await request.aread())
        requests.append((body, request.headers.get("authorization")))
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    secrets = MemorySecrets()
    app = create_app(tmp_path / "model-test.sqlite3", data_dir=tmp_path / "data",
                     secret_store=secrets, http_transport=httpx.MockTransport(provider))
    with TestClient(app) as client:
        created = client.post("/api/models", json={
            "name": "Primary", "base_url": "https://example.test/v1", "model": "model-a",
            "api_key": "old-key",
        }).json()
        tested = client.post(f"/api/models/{created['id']}/test-connection")
        updated = client.patch(f"/api/models/{created['id']}", json={"base_url": "https://next.test/v1"})
        assert updated.json()["last_tested_at"] is not None
        client.put(f"/api/models/{created['id']}/credentials", json={"api_key": "new-key"})
        retested = client.post(f"/api/models/{created['id']}/test-connection")
        listed = client.get("/api/models").json()

    assert tested.status_code == 200
    assert retested.status_code == 200
    assert requests[0][1] == "Bearer old-key"
    assert requests[1][1] == "Bearer old-key"
    assert requests[2][1] == "Bearer old-key"
    assert requests[3][1] == "Bearer new-key"
    assert requests[4][1] == "Bearer new-key"
    assert listed[0]["last_tested_at"] is not None
    assert secrets.get(f"model:{created['id']}:api_key") == "new-key"


def test_model_delete_unbinds_agents_keeps_history_and_allows_rebinding(tmp_path):
    secrets = MemorySecrets()
    app = create_app(tmp_path / "model-ref.sqlite3", data_dir=tmp_path / "data", secret_store=secrets)
    with TestClient(app) as client:
        model = create_test_model(client)
        other = create_test_model(client)
        first, second, unrelated = [client.post("/api/agents", json={
            "name": name, "model_config_id": model_id,
        }).json()["id"] for name, model_id in [("First", model), ("Second", model), ("Unrelated", other)]]
        session = client.post("/api/sessions", json={"agent_id": first}).json()["id"]
        with database_connection(app.state.database_path) as connection:
            connection.execute("INSERT INTO messages(id,session_id,role,content) VALUES('before',?,'user','Prior message')", (session,))
        deleted = client.delete(f"/api/models/{model}")
        assert deleted.status_code == 204, deleted.text
        assert secrets.get(f"model:{model}:api_key") is None
        agents = {agent["id"]: agent["model_config_id"] for agent in client.get("/api/agents").json()}
        assert agents == {first: None, second: None, unrelated: other}
        assert [message["content"] for message in client.get(f"/api/sessions/{session}/messages").json()] == ["Prior message"]
        blocked = client.post(f"/api/sessions/{session}/messages", json={"content": "Continue"})
        assert blocked.status_code == 409
        assert "配置有效的 Model" in blocked.json()["detail"]
        renamed = client.patch(f"/api/agents/{second}", json={"name": "Renamed without model"})
        assert renamed.status_code == 200 and renamed.json()["model_config_id"] is None
        rebound = client.patch(f"/api/agents/{first}", json={"model_config_id": other})
        assert rebound.status_code == 200 and rebound.json()["model_config_id"] == other

    with TestClient(app) as restarted:
        agent = restarted.get(f"/api/agents/{second}").json()
        assert agent["model_config_id"] is None and agent["model"] is None and agent["model_config_name"] is None
        assert [item["id"] for item in restarted.get("/api/models").json()] == [other]


def test_model_delete_clears_unreferenced_credential(tmp_path):
    secrets = MemorySecrets()
    with TestClient(create_app(tmp_path / "model-delete.sqlite3", data_dir=tmp_path / "data", secret_store=secrets, http_transport=successful_transport())) as client:
        model = client.post("/api/models", json={
            "name": "Unused", "base_url": "https://example.test/v1", "model": "m", "api_key": "key",
        }).json()
        response = client.delete(f"/api/models/{model['id']}")
    assert response.status_code == 204
    assert secrets.get(f"model:{model['id']}:api_key") is None


def test_create_model_rejects_failed_connection_without_persisting(tmp_path):
    async def provider(_request):
        return httpx.Response(401, json={"error": {"message": "bad key"}})

    secrets = MemorySecrets()
    app = create_app(
        tmp_path / "failed-model.sqlite3", data_dir=tmp_path / "data", secret_store=secrets,
        http_transport=httpx.MockTransport(provider),
    )
    with TestClient(app) as client:
        response = client.post("/api/models", json={
            "name": "Primary", "base_url": "https://example.test/v1",
            "model": "m", "api_key": "never-return-this",
        })
        listed = client.get("/api/models")

    assert response.status_code == 502
    assert "never-return-this" not in response.text
    assert "API" in response.json()["detail"]
    assert listed.json() == []
    assert not any(key.startswith("model:") for key in secrets.values)


def test_model_reports_connected_but_not_saved_if_vault_fails(tmp_path):
    class BrokenStore(MemorySecrets):
        def set(self, name, value):
            raise OSError('unavailable')

    app = create_app(tmp_path / 'db.sqlite', data_dir=tmp_path / 'data', secret_store=BrokenStore(), http_transport=successful_transport())
    payload = {'name': 'Working connection', 'base_url': 'https://example.test/v1', 'model': 'm', 'api_key': 'not-shown'}
    with TestClient(app) as client:
        response = client.post('/api/models', json=payload)
        assert response.status_code == 503
        assert response.headers['X-Harness-Error'] == 'model_credential_save_failed'
        assert '连接成功' in response.json()['detail'] and '未保存' in response.json()['detail']
        assert 'not-shown' not in response.text
        assert client.get('/api/models').json() == []


def test_edit_model_repairs_missing_credential(tmp_path):
    secrets = MemorySecrets()
    app = create_app(tmp_path / 'db.sqlite', data_dir=tmp_path / 'data', secret_store=secrets, http_transport=successful_transport())
    with TestClient(app) as client:
        model = client.post('/api/models', json={
            'name': 'Original', 'base_url': 'https://example.test/v1', 'model': 'm', 'api_key': 'old-key',
        }).json()
        secret_name = f"model:{model['id']}:api_key"
        secrets.delete(secret_name)
        response = client.patch(f"/api/models/{model['id']}", json={'name': 'Repaired', 'api_key': 'new-key'})
        assert response.status_code == 200, response.text
        assert response.json()['credential_configured'] is True
        assert secrets.get(secret_name) == 'new-key'


def test_invalid_model_payload_does_not_reflect_secret(tmp_path):
    app = create_app(tmp_path / 'db.sqlite', data_dir=tmp_path / 'data', secret_store=MemorySecrets(), http_transport=successful_transport())
    secret = 'sensitive-value-that-must-not-be-returned'
    with TestClient(app) as client:
        result = client.post('/api/models/test-connection', json={'base_url': 'https://example.test', 'model': 'm', 'api_key': {'value': secret}})
        assert result.status_code == 422
        assert secret not in result.text


def test_candidate_model_test_has_no_side_effect(tmp_path):
    async def provider(_request):
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    secrets = MemorySecrets()
    app = create_app(
        tmp_path / "candidate-model.sqlite3", data_dir=tmp_path / "data", secret_store=secrets,
        http_transport=httpx.MockTransport(provider),
    )
    with TestClient(app) as client:
        response = client.post("/api/models/test-connection", json={
            "base_url": "https://example.test/v1", "model": "m", "api_key": "candidate-key",
        })
        listed = client.get("/api/models")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert listed.json() == []
    assert secrets.values == {}


def test_combined_model_update_validates_new_url_and_key_atomically(tmp_path):
    async def provider(request):
        body = json.loads(await request.aread())
        authorization = request.headers.get("authorization")
        if request.url.host == "new.example.test" and authorization == "Bearer new-key":
            return httpx.Response(401, json={"error": {"message": "bad credentials"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    secrets = MemorySecrets()
    app = create_app(
        tmp_path / "combined-update.sqlite3", data_dir=tmp_path / "data", secret_store=secrets,
        http_transport=httpx.MockTransport(provider),
    )
    with TestClient(app) as client:
        created = client.post("/api/models", json={
            "name": "Primary", "base_url": "https://example.test/v1", "model": "m", "api_key": "old-key",
        }).json()
        response = client.patch(f"/api/models/{created['id']}", json={
            "base_url": "https://new.example.test/v1", "api_key": "new-key",
        })
        current = client.get("/api/models").json()[0]

    assert response.status_code == 502
    assert current["base_url"] == "https://example.test/v1"
    assert secrets.get(f"model:{created['id']}:api_key") == "old-key"


    async def provider(request):
        if "invalid" in str(request.url):
            return httpx.Response(404, json={"error": {"message": "not found"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    secrets = MemorySecrets()
    app = create_app(
        tmp_path / "update-failure.sqlite3", data_dir=tmp_path / "data", secret_store=secrets,
        http_transport=httpx.MockTransport(provider),
    )
    with TestClient(app) as client:
        created = client.post("/api/models", json={
            "name": "Primary", "base_url": "https://example.test/v1",
            "model": "m", "api_key": "old-key",
        }).json()
        old_tested_at = created["last_tested_at"]
        failed = client.patch(f"/api/models/{created['id']}", json={
            "base_url": "https://invalid.test/v1", "model": "wrong-model",
        })
        current = client.get("/api/models").json()[0]

    assert failed.status_code == 502
    assert "Base URL" in failed.json()["detail"]
    assert current["base_url"] == "https://example.test/v1"
    assert current["model"] == "m"
    assert current["last_tested_at"] == old_tested_at
    assert secrets.get(f"model:{created['id']}:api_key") == "old-key"
