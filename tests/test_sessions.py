import json

import httpx
from fastapi.testclient import TestClient

from backend.app.main import create_app
from model_test_utils import create_test_agent


class MemorySecrets:
    def __init__(self):
        self.values = {}

    def get(self, name):
        return self.values.get(name)

    def set(self, name, value):
        self.values[name] = value

    def delete(self, name):
        self.values.pop(name, None)


def test_chat_stream_persists_messages_and_never_returns_secret(tmp_path):
    async def provider_handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer private-key"
        assert request.url.path == "/v1/chat/completions"
        body = (
            'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(200, text=body)

    app = create_app(
        tmp_path / "chat.sqlite3",
        http_transport=httpx.MockTransport(provider_handler),
        secret_store=MemorySecrets(),
    )
    with TestClient(app) as client:
        agent = create_test_agent(client, {"name": "Chat", "base_url": "https://example.test/v1", "model": "test-model"}).json()
        saved_secret = client.put(
            f"/api/agents/{agent['id']}/credentials", json={"api_key": "private-key"}
        )
        assert saved_secret.status_code == 204
        assert "private-key" not in saved_secret.text

        session = client.post(
            "/api/sessions", json={"agent_id": agent["id"]}
        ).json()
        response = client.post(
            f"/api/sessions/{session['id']}/messages",
            json={"content": "Hi"},
        )
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert '"text":"Hello"' in response.text

        messages = client.get(f"/api/sessions/{session['id']}/messages").json()
        assert [message["role"] for message in messages] == ["user", "assistant"]
        assert messages[-1]["content"] == "Hello"
        assert "private-key" not in json.dumps(agent)


def test_chat_rejects_missing_agent_credentials(tmp_path):
    app = create_app(tmp_path / "chat.sqlite3", secret_store=MemorySecrets())
    with TestClient(app) as client:
        agent = create_test_agent(client, {"name": "Chat"}).json()
        client.app.state.secret_store.delete(f"model:{agent['model_config_id']}:api_key")
        session = client.post(
            "/api/sessions", json={"agent_id": agent["id"]}
        ).json()

        response = client.post(
            f"/api/sessions/{session['id']}/messages", json={"content": "Hi"}
        )

    assert response.status_code == 409
    assert "api key" not in response.text.lower()
