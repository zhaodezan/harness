import json

import httpx
from fastapi.testclient import TestClient

from backend.app.db import database_connection
from backend.app.main import create_app
from model_test_utils import create_test_agent


class Secrets:
    def get(self, name): return "key"
    def set(self, name, value): pass
    def delete(self, name): pass


def test_long_history_is_summarized_for_request_but_preserved_in_database(tmp_path):
    payloads = []
    async def provider(request: httpx.Request):
        payload = json.loads(await request.aread())
        payloads.append(payload)
        if payload["stream"] is False:
            return httpx.Response(200, json={"choices": [{"message": {"content": "EARLIER_CONTEXT_SUMMARY"}}]})
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"answer"}}]}\n\ndata: [DONE]\n\n')

    database_path = tmp_path / "compact.sqlite3"
    app = create_app(database_path, http_transport=httpx.MockTransport(provider), secret_store=Secrets())
    with TestClient(app) as client:
        agent = create_test_agent(client, {"name": "Helper", "base_url": "https://example.test/v1", "model": "model", "context_window": 1024}).json()
        session = client.post("/api/sessions", json={"agent_id": agent["id"]}).json()
        with database_connection(database_path) as connection:
            for index in range(15):
                connection.execute(
                    "INSERT INTO messages(id, session_id, role, content) VALUES (?, ?, 'user', ?)",
                    (f"old-{index}", session["id"], f"historic detail {index} " + "x" * 600),
                )
        response = client.post(f"/api/sessions/{session['id']}/messages", json={"content": "current question"})
        assert response.status_code == 200
        history = client.get(f"/api/sessions/{session['id']}/messages").json()
        events = client.get("/api/logs").json()

    assert len(history) == 17
    assert len(payloads) == 2
    assert any("EARLIER_CONTEXT_SUMMARY" in message["content"] for message in payloads[-1]["messages"])
    assert any(event["event_type"] == "context_compacted" for event in events)
