import json

import httpx
from fastapi.testclient import TestClient

from backend.app.db import database_connection
from backend.app.main import create_app
from model_test_utils import create_test_agent


class MemorySecrets:
    def get(self, name): return "key"
    def set(self, name, value): pass
    def delete(self, name): pass


def test_branch_send_reuses_edited_user_message_instead_of_duplicating(tmp_path):
    sent = []
    async def provider(request: httpx.Request):
        payload = json.loads(await request.aread())
        sent.append(payload["messages"])
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"answer"}}]}\n\ndata: [DONE]\n\n')

    database_path = tmp_path / "branch-send.sqlite3"
    app = create_app(database_path, http_transport=httpx.MockTransport(provider), secret_store=MemorySecrets())
    with TestClient(app) as client:
        agent = create_test_agent(client, {"name": "Helper", "base_url": "https://example.test/v1", "model": "m"}).json()
        session = client.post("/api/sessions", json={"agent_id": agent["id"]}).json()
        with database_connection(database_path) as connection:
            connection.execute("INSERT INTO messages(id,session_id,role,content) VALUES('source-user',?,'user','old')", (session["id"],))
        branch = client.post(f"/api/sessions/{session['id']}/branch", json={"message_id": "source-user", "content": "edited question"}).json()
        response = client.post(f"/api/sessions/{branch['id']}/messages", json={"content": "edited question", "reuse_last_user": True})
        assert response.status_code == 200
        messages = client.get(f"/api/sessions/{branch['id']}/messages").json()

    assert [item["content"] for item in messages if item["role"] == "user"] == ["edited question"]
    assert [item["content"] for item in sent[0] if item["role"] == "user"] == ["edited question"]
