import sys

from fastapi.testclient import TestClient

from backend.app.main import create_app


class MemorySecrets:
    def __init__(self): self.values = {}
    def get(self, name): return self.values.get(name)
    def set(self, name, value): self.values[name] = value
    def delete(self, name): self.values.pop(name, None)


def test_stdio_mcp_environment_values_use_system_secret_store_not_sqlite(tmp_path):
    script = tmp_path / "env_mcp.py"
    script.write_text(
        "import json,os,sys\n"
        "for line in sys.stdin:\n"
        " m=json.loads(line); method=m.get('method'); rid=m.get('id')\n"
        " if method=='initialize': r={'protocolVersion':'2025-03-26','capabilities':{'tools':{}},'serverInfo':{'name':'env','version':'1'}}\n"
        " elif method=='tools/list': r={'tools':[]}\n"
        " else: continue\n"
        " print(json.dumps({'jsonrpc':'2.0','id':rid,'result':r}),flush=True)\n",
        encoding="utf-8",
    )
    secrets = MemorySecrets()
    database_path = tmp_path / "mcp-env.sqlite3"
    with TestClient(create_app(database_path, secret_store=secrets)) as client:
        server = client.post("/api/mcp/servers", json={
            "name": "env", "transport": "stdio", "command": sys.executable,
            "args": ["-u", str(script)], "env": {"SERVICE_TOKEN": "secret-value"},
        }).json()
        assert server["env_names"] == ["SERVICE_TOKEN"]
        assert "secret-value" not in str(server)
        connected = client.post(f"/api/mcp/servers/{server['id']}/connect", json={"confirmed": True})
        assert connected.status_code == 200
    assert secrets.get(f"mcp:{server['id']}:env:SERVICE_TOKEN") == "secret-value"
    contents = database_path.read_bytes()
    assert b"secret-value" not in contents
