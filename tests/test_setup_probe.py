import httpx
from fastapi.testclient import TestClient

from backend.app.main import create_app


class Secrets:
    def __init__(self): self.values = {}
    def get(self, name): return self.values.get(name)
    def set(self, name, value): self.values[name] = value
    def delete(self, name): self.values.pop(name, None)


def test_model_connection_test_does_not_create_agent_and_keeps_secret_in_keyring(tmp_path):
    async def provider(request: httpx.Request):
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
    secrets = Secrets()
    app = create_app(tmp_path / "setup-probe.sqlite3", http_transport=httpx.MockTransport(provider), secret_store=secrets)
    with TestClient(app) as client:
        model = client.post("/api/models", json={
            "name": "Probe", "base_url": "https://example.test/v1", "model": "m", "api_key": "probe-only",
        }).json()
        response = client.post(f"/api/models/{model['id']}/test-connection")
        agents = client.get("/api/agents").json()
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert agents == []
    assert secrets.values == {f"model:{model['id']}:api_key": "probe-only"}

