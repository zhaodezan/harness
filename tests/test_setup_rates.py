import httpx
from fastapi.testclient import TestClient

from backend.app.main import create_app


def test_setup_does_not_persist_token_rates(tmp_path):
    async def provider(_: httpx.Request):
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    class Secrets:
        def __init__(self): self.values = {}
        def get(self, name): return self.values.get(name)
        def set(self, name, value): self.values[name] = value
        def delete(self, name): self.values.pop(name, None)

    app = create_app(tmp_path / "setup-rates.sqlite3", http_transport=httpx.MockTransport(provider), secret_store=Secrets())
    with TestClient(app) as client:
        model = client.post("/api/models", json={
            "name": "Rate model", "base_url": "https://example.test/v1", "model": "m", "api_key": "key",
        }).json()
        client.post(f"/api/models/{model['id']}/test-connection")
        response = client.post("/api/setup/model", json={
            "name": "Rate agent", "model_config_id": model["id"],
            "input_rate_per_million": 1.5, "output_rate_per_million": 6.0,
        })
        agent = client.get(f"/api/agents/{response.json()['id']}").json()
    assert response.status_code == 201
    assert agent["input_rate_per_million"] is None
    assert agent["output_rate_per_million"] is None
