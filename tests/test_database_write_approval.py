import asyncio
import json
import sqlite3

import httpx

from backend.app.main import create_app
from model_test_utils import create_test_model


class Secrets:
    def get(self, name): return "key"
    def set(self, name, value): pass
    def delete(self, name): pass


def test_database_write_tool_waits_for_user_approval_before_mutating_source(tmp_path):
    path = tmp_path / "external.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE records(id INTEGER PRIMARY KEY, value TEXT)")
        connection.execute("INSERT INTO records(value) VALUES('before')")

    calls = []
    async def provider(request):
        body = json.loads(await request.aread())
        calls.append(body)
        if len(calls) == 1:
            tool = next(item["function"]["name"] for item in body["tools"] if item["function"]["name"].endswith("_write"))
            frame = {"choices": [{"delta": {"tool_calls": [{
                "index": 0, "id": "db-write-call", "type": "function",
                "function": {"name": tool, "arguments": json.dumps({"sql": "UPDATE records SET value='after' WHERE id=1"})},
            }]}}]}
            return httpx.Response(200, text="data: " + json.dumps(frame) + "\n\ndata: [DONE]\n\n")
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"updated"}}]}\n\ndata: [DONE]\n\n')

    async def run():
        app = create_app(
            tmp_path / "app.sqlite3", data_dir=tmp_path / "data", secret_store=Secrets(),
            http_transport=httpx.MockTransport(provider),
        )
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
                db = (await client.post("/api/databases", json={
                    "name": "Writable", "db_type": "sqlite", "sqlite_path": str(path), "readonly": False,
                })).json()
                model_id = create_test_model(app)
                if not model_id:
                    raise AssertionError("test model creation failed")
                agent_response = await client.post("/api/agents", json={
                    "name": "Writer", "model_config_id": model_id,
                    "selected_database_connections": [db["id"]],
                })
                assert agent_response.status_code == 201, agent_response.text
                session = (await client.post("/api/sessions", json={"agent_id": agent_response.json()["id"]})).json()
                task = asyncio.create_task(client.post(
                    f"/api/sessions/{session['id']}/messages", json={"content": "change the value", "approval_mode": "user_approval"}
                ))
                for _ in range(200):
                    if app.state.pending_approvals:
                        break
                    await asyncio.sleep(0.01)
                assert app.state.pending_approvals
                with sqlite3.connect(path) as connection:
                    assert connection.execute("SELECT value FROM records WHERE id=1").fetchone()[0] == "before"
                approval_id = next(iter(app.state.pending_approvals))
                approved = await client.post(f"/api/approvals/{approval_id}", json={"approved": True})
                assert approved.status_code == 200
                response = await task
                assert response.status_code == 200
        return len(calls)

    assert asyncio.run(run()) == 2
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT value FROM records WHERE id=1").fetchone()[0] == "after"
