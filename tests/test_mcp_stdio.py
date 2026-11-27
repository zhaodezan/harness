import sys

from fastapi.testclient import TestClient

from backend.app.main import create_app


def test_stdio_server_requires_confirmation_and_connects(tmp_path):
    server_script = tmp_path / "fake_mcp.py"
    server_script.write_text(
        "import json, sys\n"
        "for line in sys.stdin:\n"
        " msg=json.loads(line); method=msg.get('method'); rid=msg.get('id')\n"
        " if method == 'initialize': result={'protocolVersion':'2025-03-26','capabilities':{'tools':{}},'serverInfo':{'name':'fake','version':'1'}}\n"
        " elif method == 'tools/list': result={'tools':[{'name':'read','description':'Read only','inputSchema':{'type':'object','properties':{}},'annotations':{'readOnlyHint':True}}]}\n"
        " else: continue\n"
        " print(json.dumps({'jsonrpc':'2.0','id':rid,'result':result}), flush=True)\n",
        encoding="utf-8",
    )
    app = create_app(tmp_path / "stdio.sqlite3")
    with TestClient(app) as client:
        server = client.post("/api/mcp/servers", json={
            "name": "local-tool", "transport": "stdio", "command": sys.executable,
            "args": ["-u", str(server_script)], "cwd": str(tmp_path),
        }).json()
        not_confirmed = client.post(f"/api/mcp/servers/{server['id']}/connect", json={"confirmed": False})
        assert not_confirmed.status_code == 409
        connected = client.post(f"/api/mcp/servers/{server['id']}/connect", json={"confirmed": True})
        assert connected.status_code == 200
        assert connected.json()["tools"] == ["read"]
        listed = client.get("/api/mcp/servers").json()
        assert listed[0]["connected"] is True
    with TestClient(create_app(tmp_path / "stdio.sqlite3")) as restarted:
        assert restarted.get("/api/mcp/servers").json()[0]["connected"] is True
        assert restarted.get(f"/api/mcp/servers/{server['id']}/tools").json()[0]["name"] == "local-tool/read"
        disconnected = restarted.post(f"/api/mcp/servers/{server['id']}/disconnect")
        assert disconnected.status_code == 200
