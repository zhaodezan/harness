import json

import httpx
from fastapi.testclient import TestClient

from backend.app.db import database_connection
from backend.app.main import create_app
from tests.model_test_utils import create_test_agent


class Secrets:
    def get(self, name):
        return "key"

    def set(self, name, value):
        pass

    def delete(self, name):
        pass


def _stream_response(text="ok"):
    return httpx.Response(
        200,
        text=f'data: {{"choices":[{{"delta":{{"content":"{text}"}}}}]}}\n\ndata: [DONE]\n\n',
    )


def test_chat_injects_authorized_knowledge_without_persisting_hidden_context(tmp_path):
    captured = []

    async def provider(request):
        captured.append(json.loads(await request.aread()))
        return _stream_response()

    app = create_app(
        tmp_path / "runtime.sqlite3",
        data_dir=tmp_path / "data",
        http_transport=httpx.MockTransport(provider),
        secret_store=Secrets(),
    )
    with TestClient(app) as client:
        knowledge_base = client.post("/api/knowledge-bases", json={"name": "Runbook"}).json()
        client.post(
            f"/api/knowledge-bases/{knowledge_base['id']}/documents",
            files=[("files", ("runbook.md", b"BM25 deployment runbook secret phrase", "text/markdown"))],
        )
        agent = create_test_agent(client, {
            "name": "Helper",
            "selected_knowledge_bases": [knowledge_base["id"]],
        }).json()
        session = client.post("/api/sessions", json={"agent_id": agent["id"]}).json()
        response = client.post(
            f"/api/sessions/{session['id']}/messages",
            json={"content": "What is the BM25 deployment runbook?"},
        )
        assert response.status_code == 200
        assert 'secret phrase' not in response.text

        messages = client.get(f"/api/sessions/{session['id']}/messages").json()
        events = client.get(f"/api/logs?run_id=missing").json()

    request_text = json.dumps(captured[0]["messages"], ensure_ascii=False)
    assert "secret phrase" in request_text
    assert "secret phrase" not in json.dumps(messages, ensure_ascii=False)
    assert "secret phrase" not in json.dumps(events, ensure_ascii=False)
    with database_connection(tmp_path / "runtime.sqlite3") as connection:
        event = connection.execute(
            "SELECT event_type,status,summary FROM run_events WHERE event_type='knowledge_retrieval'"
        ).fetchone()
    assert event is not None
    assert event[1] == "completed"
    assert "secret phrase" not in event[2]


def test_debug_uses_draft_knowledge_selection_without_returning_hidden_context(tmp_path):
    captured = []

    async def provider(request):
        captured.append(json.loads(await request.aread()))
        return _stream_response("debug-ok")

    app = create_app(
        tmp_path / "debug.sqlite3",
        data_dir=tmp_path / "data",
        http_transport=httpx.MockTransport(provider),
        secret_store=Secrets(),
    )
    with TestClient(app) as client:
        knowledge_base = client.post("/api/knowledge-bases", json={"name": "Debug Docs"}).json()
        client.post(
            f"/api/knowledge-bases/{knowledge_base['id']}/documents",
            files=[("files", ("debug.md", b"draft-only hidden retrieval phrase", "text/markdown"))],
        )
        agent = create_test_agent(client, {"name": "Debug"}).json()
        response = client.post(f"/api/agents/{agent['id']}/debug", json={
            "config": {
                "model_config_id": agent["model_config_id"],
                "selected_knowledge_bases": [knowledge_base["id"]],
            },
            "messages": [{"role": "user", "content": "Tell me the draft hidden retrieval phrase"}],
        })

    assert response.status_code == 200
    assert "draft-only hidden retrieval phrase" not in response.text
    assert "draft-only hidden retrieval phrase" in json.dumps(captured[0]["messages"], ensure_ascii=False)
