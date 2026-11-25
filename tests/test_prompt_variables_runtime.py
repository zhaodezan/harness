import json

import httpx
from fastapi.testclient import TestClient

from backend.app.main import create_app
from model_test_utils import create_test_agent


class Secrets:
    def get(self, name): return "key"
    def set(self, name, value): pass
    def delete(self, name): pass


def test_saved_prompt_variables_are_rendered_before_model_request(tmp_path):
    captured = []
    async def provider(request: httpx.Request):
        captured.append(json.loads(await request.aread()))
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"hello"}}]}\n\ndata: [DONE]\n\n')

    app = create_app(tmp_path / "prompt-vars.sqlite3", http_transport=httpx.MockTransport(provider), secret_store=Secrets())
    with TestClient(app) as client:
        agent = create_test_agent(client, {
            "name": "Greeting", "base_url": "https://example.test/v1", "model": "m",
            "system_prompt": "Greet {{user_name}} in {{language}}",
            "prompt_variables": {"user_name": "Ari", "language": "Chinese"},
        }).json()
        session = client.post("/api/sessions", json={"agent_id": agent["id"]}).json()
        response = client.post(f"/api/sessions/{session['id']}/messages", json={"content": "hello"})

    assert response.status_code == 200
    system = next(message["content"] for message in captured[0]["messages"] if message["role"] == "system")
    assert system == "Greet Ari in Chinese"
