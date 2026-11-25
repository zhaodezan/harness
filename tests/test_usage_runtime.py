import httpx

from fastapi.testclient import TestClient

from backend.app.main import create_app
from model_test_utils import create_test_agent


class Secrets:
    def get(self, name): return "key"
    def set(self, name, value): pass
    def delete(self, name): pass


def test_provider_token_usage_is_saved_with_run_event(tmp_path):
    async def provider(_: httpx.Request):
        return httpx.Response(200, text=(
            'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'
            'data: {"choices":[],"usage":{"prompt_tokens":21,"completion_tokens":8}}\n\n'
            'data: [DONE]\n\n'
        ))
    app = create_app(tmp_path / "usage-run.sqlite3", http_transport=httpx.MockTransport(provider), secret_store=Secrets())
    with TestClient(app) as client:
        agent = create_test_agent(client, {"name": "Helper", "base_url": "https://example.test/v1", "model": "m"}).json()
        session = client.post("/api/sessions", json={"agent_id": agent["id"]}).json()
        client.post(f"/api/sessions/{session['id']}/messages", json={"content": "hello"})
        run = next(item for item in client.get("/api/logs").json() if item["event_type"] == "run_completed")

    assert run["input_tokens"] == 21
    assert run["output_tokens"] == 8
