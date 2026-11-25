import json

import httpx
from fastapi.testclient import TestClient

from backend.app.main import create_app
from model_test_utils import create_test_agent


class Secrets:
    def get(self, name): return "key"
    def set(self, name, value): pass
    def delete(self, name): pass


def test_relevant_confirmed_memory_is_retrieved_in_new_conversation(tmp_path):
    captured = []
    async def provider(request: httpx.Request):
        captured.append(json.loads(await request.aread()))
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"好的"}}]}\n\ndata: [DONE]\n\n')

    app = create_app(tmp_path / "memory-runtime.sqlite3", http_transport=httpx.MockTransport(provider), secret_store=Secrets())
    with TestClient(app) as client:
        agent = create_test_agent(client, {"name": "Helper", "base_url": "https://example.test/v1", "model": "model"}).json()
        client.post("/api/memories", json={"content": "用户偏好简洁清晰的中文回答", "scope": "global", "source": "user_confirmed"})
        client.post("/api/memories", json={"content": "用户养了一只橘猫", "scope": "global", "source": "user_confirmed"})
        session = client.post("/api/sessions", json={"agent_id": agent["id"]}).json()
        response = client.post(f"/api/sessions/{session['id']}/messages", json={"content": "请用简洁方式回答这个问题"})
        assert response.status_code == 200

    sent = captured[0]["messages"]
    memory_blocks = [message["content"] for message in sent if message["role"] == "user" and "长期记忆" in message["content"]]
    assert len(memory_blocks) == 1
    assert "简洁清晰" in memory_blocks[0]
    assert "橘猫" not in memory_blocks[0]
    assert "不是指令" in memory_blocks[0]
