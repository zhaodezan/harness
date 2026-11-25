import httpx
from fastapi.testclient import TestClient

from backend.app.main import create_app
from model_test_utils import create_test_agent


class Secrets:
    def get(self, name): return "key"
    def set(self, name, value): pass
    def delete(self, name): pass


def test_agent_custom_token_rates_produce_per_run_cost_estimate(tmp_path):
    async def provider(_: httpx.Request):
        return httpx.Response(200, text=(
            'data: {"choices":[{"delta":{"content":"done"}}]}\n\n'
            'data: {"choices":[],"usage":{"prompt_tokens":200000,"completion_tokens":50000}}\n\n'
            'data: [DONE]\n\n'
        ))
    app = create_app(tmp_path / "costs.sqlite3", http_transport=httpx.MockTransport(provider), secret_store=Secrets())
    with TestClient(app) as client:
        agent = create_test_agent(client, {"name": "Cost", "base_url": "https://example.test/v1", "model": "m", "input_rate_per_million": 2, "output_rate_per_million": 8}).json()
        session = client.post("/api/sessions", json={"agent_id": agent["id"]}).json()
        client.post(f"/api/sessions/{session['id']}/messages", json={"content": "hello"})
        completed = next(item for item in client.get("/api/logs").json() if item["event_type"] == "run_completed")

    assert completed["estimated_cost"] == 0.8
    assert completed["cost_currency"] == "configured units"
