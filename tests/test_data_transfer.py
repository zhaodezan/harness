import json

from fastapi.testclient import TestClient

from backend.app.db import database_connection
from backend.app.main import create_app
from model_test_utils import create_test_agent


def test_backup_round_trip_remaps_agent_delegation_targets(tmp_path):
    with TestClient(create_app(tmp_path / "delegation-source.sqlite3")) as client:
        source = create_test_agent(client, {"name": "Source"}).json()
        target = create_test_agent(client, {"name": "Target"}).json()
        client.patch(f"/api/agents/{source['id']}", json={"selected_agents": [target["id"]], "sandbox_code_enabled": True})
        payload = client.get("/api/data/export").json()

    target_app = create_app(tmp_path / "delegation-target.sqlite3")
    with TestClient(target_app) as client:
        imported = client.post("/api/data/import", json=payload)
        agents = {agent["name"]: agent for agent in client.get("/api/agents").json()}

    assert imported.status_code == 200, imported.text
    assert agents["Source"]["selected_agents"] == [agents["Target"]["id"]]
    assert agents["Source"]["sandbox_code_enabled"] is True
    assert agents["Source"]["id"] != source["id"]
    assert agents["Target"]["id"] != target["id"]


def test_backup_round_trip_preserves_agent_plugin_selections(tmp_path):
    source = create_app(tmp_path / "source.sqlite3")
    with TestClient(source) as client:
        agent = create_test_agent(client, {"name": "Plugin agent"}).json()
        client.patch(f"/api/agents/{agent['id']}", json={
            "selected_skills": ["summarize"],
            "selected_mcp_tools": ["mcp:server-1:search"],
            "selected_local_tools": ["tool-1"],
        })
        payload = client.get("/api/data/export").json()

    assert payload["agents"][0]["selected_mcp_tools"] == ["mcp:server-1:search"]
    target = create_app(tmp_path / "target.sqlite3")
    with TestClient(target) as client:
        imported = client.post("/api/data/import", json=payload)
        restored = client.get("/api/agents").json()[0]

    assert imported.status_code == 200
    assert restored["selected_skills"] == ["summarize"]
    assert restored["selected_mcp_tools"] == ["mcp:server-1:search"]
    assert restored["selected_local_tools"] == ["tool-1"]


def test_backup_round_trip_remaps_mcp_tool_authorizations(tmp_path):
    source = create_app(tmp_path / "mcp-source.sqlite3")
    with TestClient(source) as client:
        server = client.post("/api/mcp/servers", json={
            "name": "files", "transport": "stdio", "command": "python",
        }).json()
        create_test_agent(client, {
            "name": "Uses MCP", "selected_mcp_tools": [f"mcp:{server['id']}:lookup"],
        })
        payload = client.get("/api/data/export").json()

    payload['mcp_servers'][0]['auto_connect'] = True  # Imported data cannot authorize automatic connections.
    target = create_app(tmp_path / "mcp-target.sqlite3")
    with TestClient(target) as client:
        response = client.post("/api/data/import", json=payload)
        restored_agent = client.get("/api/agents").json()[0]
        restored_server = client.get("/api/mcp/servers").json()[0]

    assert response.status_code == 200
    assert restored_server["id"] != server["id"]
    assert not restored_server["auto_connect"]
    assert restored_agent["selected_mcp_tools"] == [f"mcp:{restored_server['id']}:lookup"]


def test_export_and_import_round_trip_agent_sessions_and_messages_without_secrets(tmp_path):
    source_db = tmp_path / "session-source.sqlite3"
    source = create_app(source_db)
    with TestClient(source) as client:
        agent = create_test_agent(client, {"name": "Helper", "system_prompt": "Useful"}).json()
        session = client.post("/api/sessions", json={"agent_id": agent["id"], "title": "Notes"}).json()
        client.post("/api/mcp/servers", json={"name": "local-files", "transport": "stdio", "command": "python", "args": ["server.py"]})
        with database_connection(source_db) as connection:
            connection.execute("INSERT INTO messages(id,session_id,role,content) VALUES('msg1',?,'user','question')", (session["id"],))
        exported = client.get("/api/data/export")
        assert exported.status_code == 200
        payload = exported.json()
        assert "api_key" not in exported.text

    target = create_app(tmp_path / "session-target.sqlite3")
    with TestClient(target) as client:
        imported = client.post("/api/data/import", json=payload)
        assert imported.status_code == 200, imported.text
        assert imported.json()["sessions_imported"] == 1
        assert imported.json()["mcp_servers_imported"] == 1
        assert client.get("/api/mcp/servers").json()[0]["name"] == "local-files"
        new_agent = client.get("/api/agents").json()[0]
        new_session = client.get(f"/api/sessions?agent_id={new_agent['id']}").json()[0]
        messages = client.get(f"/api/sessions/{new_session['id']}/messages").json()

    assert new_agent["id"] != agent["id"]
    assert new_session["title"] == "Notes"
    assert [item["content"] for item in messages] == ["question"]


def test_backup_does_not_transfer_local_image_references(tmp_path):
    db = tmp_path / 'session.sqlite3'
    with TestClient(create_app(db, data_dir=tmp_path / 'data')) as client:
        session = client.post('/api/sessions', json={}).json()['id']
        metadata = {'images': [{'id': 'a' * 32, 'label': 'local photo'}]}
        with database_connection(db) as connection:
            connection.execute("INSERT INTO messages(id,session_id,role,content,metadata_json) VALUES('photo',?,'assistant','picture',?)",
                               (session, json.dumps(metadata)))
        payload = client.get('/api/data/export').json()
        assert 'images' not in json.loads(payload['messages'][0]['metadata_json'])
        payload['messages'][0]['metadata_json'] = json.dumps(metadata)
        assert client.post('/api/data/import', json=payload).status_code == 200
        imported = next(item['id'] for item in client.get('/api/sessions').json() if item['id'] != session)
        (restored,) = client.get(f'/api/sessions/{imported}/messages').json()
        assert restored['content'] == 'picture' and 'images' not in restored
