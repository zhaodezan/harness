import httpx
from fastapi.testclient import TestClient

from backend.app.main import create_app
from model_test_utils import create_test_agent


class Secrets:
    def get(self, name): return "key"
    def set(self, name, value): pass
    def delete(self, name): pass


def test_agent_connection_test_uses_compatible_provider_without_persisting_messages(tmp_path):
    async def provider(request: httpx.Request):
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
    app = create_app(tmp_path / "connection.sqlite3", http_transport=httpx.MockTransport(provider), secret_store=Secrets())
    with TestClient(app) as client:
        agent = create_test_agent(client, {"name": "Helper", "base_url": "https://example.test/v1", "model": "m"}).json()
        response = client.post(f"/api/agents/{agent['id']}/test-connection")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
