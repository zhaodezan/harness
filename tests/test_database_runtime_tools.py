import json

import httpx
from fastapi.testclient import TestClient

from backend.app.db import database_connection
from backend.app.main import create_app
from tests.model_test_utils import create_test_agent


class Secrets:
    def get(self, name): return "key"
    def set(self, name, value): pass
    def delete(self, name): pass


def test_chat_offers_only_agent_authorized_database_tools(tmp_path):
    captured = []
    async def provider(request):
        captured.append(json.loads(await request.aread()))
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n')

    app = create_app(
        tmp_path / "runtime.sqlite3", data_dir=tmp_path / "data", secret_store=Secrets(),
        http_transport=httpx.MockTransport(provider),
    )
    import sqlite3
    dbpaths = []
    with TestClient(app) as client:
        db_ids = []
        for name in ("Allowed", "Not allowed"):
            path = tmp_path / f"{name}.sqlite3"
            with sqlite3.connect(path) as connection:
                connection.execute("CREATE TABLE records(value TEXT)")
            dbpaths.append(path)
            response = client.post("/api/databases", json={
                "name": name, "db_type": "sqlite", "sqlite_path": str(path),
            })
            db_ids.append(response.json()["id"])
        model_id = __import__("tests.model_test_utils", fromlist=["create_test_model"]).create_test_model(client)
        agent = create_test_agent(client, {
            "name": "Reader", "model_config_id": model_id,
            "selected_database_connections": [db_ids[0]],
        }).json()
        session = client.post("/api/sessions", json={"agent_id": agent["id"]}).json()
        response = client.post(f"/api/sessions/{session['id']}/messages", json={"content": "read the database schema"})
        assert response.status_code == 200

    tools = captured[0]["tools"]
    names = [item["function"]["name"] for item in tools]
    assert any(db_ids[0] in name and "schema" in name for name in names)
    assert any(db_ids[0] in name and "query" in name for name in names)
    assert not any(db_ids[1] in name for name in names)
    assert not any("write" in name for name in names)
