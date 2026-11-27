import asyncio
import json

import pytest
import httpx
from fastapi.testclient import TestClient

from backend.app.main import create_app


class MemorySecrets:
    def __init__(self): self.values = {}
    def get(self, name): return self.values.get(name)
    def set(self, name, value): self.values[name] = value
    def delete(self, name): self.values.pop(name, None)


def test_mcp_http_server_can_be_configured_connected_and_tools_discovered(tmp_path):
    seen_authorization = []
    async def transport(request: httpx.Request):
        seen_authorization.append(request.headers.get("authorization"))
        body = __import__('json').loads(await request.aread())
        if body["method"] == "initialize":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": {"protocolVersion": "2025-03-26", "capabilities": {"tools": {}}, "serverInfo": {"name": "sample", "version": "1"}}}, headers={"Mcp-Session-Id": "session-1"})
        if body["method"] == "notifications/initialized":
            return httpx.Response(202)
        if body["method"] == "tools/list":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": {"tools": [{"name": "lookup", "description": "Find data", "inputSchema": {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]}, "annotations": {"readOnlyHint": True}}]}})
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": {"content": [{"type": "text", "text": "ok"}]}})

    app = create_app(tmp_path / "mcp.sqlite3", http_transport=httpx.MockTransport(transport), secret_store=MemorySecrets())
    with TestClient(app) as client:
        created = client.post("/api/mcp/servers", json={"name": "sample", "transport": "http", "url": "https://example.test/mcp", "bearer_token": "hidden-token"})
        assert created.status_code == 201
        assert "hidden-token" not in created.text
        server_id = created.json()["id"]
        connected = client.post(f"/api/mcp/servers/{server_id}/connect", json={"confirmed": True})
        assert connected.status_code == 200
        tools = client.get(f"/api/mcp/servers/{server_id}/tools").json()
        registered = {tool["name"]: tool["risk"] for tool in app.state.tool_registry.list()}

    assert tools[0]["name"] == "sample/lookup"
    assert tools[0]["id"] == f"mcp:{server_id}:lookup"
    assert tools[0]["risk"] == "read_only"
    assert registered["sample/lookup"] == "read_only"
    assert all(value == "Bearer hidden-token" for value in seen_authorization)


@pytest.mark.parametrize("transport", ["stdio", "sse", "streamable_http"])
def test_mcp_accepts_supported_transports(tmp_path, transport):
    app = create_app(tmp_path / f"{transport}.sqlite3", secret_store=MemorySecrets())
    payload = {"name": transport, "transport": transport}
    if transport == "stdio":
        payload["command"] = "python"
    else:
        payload["url"] = "https://example.test/mcp"
    with TestClient(app) as client:
        response = client.post("/api/mcp/servers", json=payload)
    assert response.status_code == 201, response.text


def test_legacy_http_transport_is_returned_as_streamable_http(tmp_path):
    app = create_app(tmp_path / "legacy-http.sqlite3", secret_store=MemorySecrets())
    with TestClient(app) as client:
        created = client.post("/api/mcp/servers", json={
            "name": "Legacy HTTP", "transport": "http", "url": "https://example.test/mcp",
        })
        assert created.status_code == 201
        listed = client.get("/api/mcp/servers")
    assert listed.json()[0]["transport"] == "streamable_http"


def test_sse_mcp_server_uses_endpoint_for_json_rpc(tmp_path):
    seen = []
    async def transport(request):
        seen.append(request)
        if request.method == "GET":
            return httpx.Response(200, text="event: endpoint\ndata: /messages\n\n", headers={"content-type": "text/event-stream"})
        body = __import__('json').loads(await request.aread())
        if body["method"] == "notifications/initialized":
            return httpx.Response(202)
        return httpx.Response(200, json={
            "jsonrpc": "2.0", "id": body["id"],
            "result": {"tools": [{"name": "lookup", "description": "Find", "inputSchema": {"type": "object", "properties": {}}}]}
            if body["method"] == "tools/list" else {"protocolVersion": "2025-03-26"},
        })

    app = create_app(tmp_path / "sse.sqlite3", http_transport=httpx.MockTransport(transport), secret_store=MemorySecrets())
    with TestClient(app) as client:
        created = client.post("/api/mcp/servers", json={
            "name": "SSE", "transport": "sse", "url": "https://example.test/sse", "bearer_token": "secret-token",
        }).json()
        connected = client.post(f"/api/mcp/servers/{created['id']}/connect", json={"confirmed": True})
        tools = client.get(f"/api/mcp/servers/{created['id']}/tools")
        client.post(f"/api/mcp/servers/{created['id']}/disconnect")

    assert connected.status_code == 200
    assert tools.json()[0]["name"] == "SSE/lookup"
    assert all(request.headers.get("authorization") == "Bearer secret-token" for request in seen)
    assert [str(request.url) for request in seen if request.method == "POST"] == [
        "https://example.test/messages", "https://example.test/messages", "https://example.test/messages",
    ]


def test_restart_restores_confirmed_mcp_and_leaves_unconfirmed_alone(tmp_path):
    seen = []
    secrets = MemorySecrets()

    async def transport(request):
        body = json.loads(await request.aread())
        seen.append((request.url.host, body['method'], request.headers.get('authorization')))
        if body['method'] == 'notifications/initialized':
            return httpx.Response(202)
        results = {'initialize': {}, 'tools/list': {'tools': [{'name': 'lookup'}]},
                   'tools/call': {'content': [{'type': 'text', 'text': 'ok'}]}}
        return httpx.Response(200, json={'jsonrpc': '2.0', 'id': body['id'], 'result': results[body['method']]})

    def app():
        return create_app(tmp_path / 'mcp.sqlite3', data_dir=tmp_path / 'data',
                          http_transport=httpx.MockTransport(transport), secret_store=secrets)

    with TestClient(app()) as client:
        for name in ('confirmed', 'unconfirmed'):
            created = client.post('/api/mcp/servers', json={'name': name, 'transport': 'http',
                                  'url': f'https://{name}.test/mcp', 'bearer_token': 'test-token'})
            assert created.status_code == 201
            if name == 'confirmed':
                server = created.json()['id']
        assert client.post(f'/api/mcp/servers/{server}/connect', json={'confirmed': True}).status_code == 200
    seen.clear()
    restarted = app()
    with TestClient(restarted) as client:
        confirmed, unconfirmed = client.get('/api/mcp/servers').json()
        assert confirmed['connected'] and confirmed['auto_connect']
        assert not unconfirmed['connected'] and not unconfirmed['auto_connect']
        assert all(host == 'confirmed.test' and token == 'Bearer test-token' for host, _, token in seen)
        assert client.get(f'/api/mcp/servers/{server}/tools').json()[0]['id'] == f'mcp:{server}:lookup'
        assert restarted.state.tool_registry.get_definition('confirmed/lookup') is not None
        result = client.portal.call(restarted.state.mcp_manager.call_tool, server, 'lookup', {})
        assert result['content'][0]['text'] == 'ok'
        assert client.post(f'/api/mcp/servers/{server}/disconnect').status_code == 200
    seen.clear()
    with TestClient(app()) as client:
        assert not any(s['connected'] or s['auto_connect'] for s in client.get('/api/mcp/servers').json())
        assert seen == []


def test_failed_restore_does_not_block_startup_or_publish_partial_tools(tmp_path, monkeypatch):
    mode = 'ok'

    async def transport(request):
        body = json.loads(await request.aread())
        if body['method'] == 'notifications/initialized':
            return httpx.Response(202)
        tools = [{'name': 'lookup'}]
        if mode == 'failed' and request.url.host == 'conflict.test':
            tools.append({'name': 'lookup'})
        if mode == 'failed' and request.url.host == 'slow.test':
            await asyncio.Event().wait()
        return httpx.Response(200, json={'jsonrpc': '2.0', 'id': body['id'], 'result': {'tools': tools}})

    def app():
        return create_app(tmp_path / 'mcp.sqlite3', data_dir=tmp_path / 'data',
                          http_transport=httpx.MockTransport(transport), secret_store=MemorySecrets())

    with TestClient(app()) as client:
        for name in ('good', 'conflict', 'slow'):
            server = client.post('/api/mcp/servers', json={'name': name, 'transport': 'http',
                                 'url': f'https://{name}.test/mcp'}).json()['id']
            assert client.post(f'/api/mcp/servers/{server}/connect', json={'confirmed': True}).status_code == 200
    mode = 'failed'
    monkeypatch.setattr('backend.app.mcp_connections.STARTUP_TIMEOUT', 0.6)
    restarted = app()
    with TestClient(restarted) as client:
        assert client.get('/api/health').status_code == 200
        servers = {item['name']: item for item in client.get('/api/mcp/servers').json()}
        assert servers['good']['connected']
        for name in ('conflict', 'slow'):
            item = servers[name]
            assert not item['connected'] and item['auto_connect'] and item['connection_error']
            assert restarted.state.tool_registry.get_definition(f'{name}/lookup') is None
            assert item['id'] not in restarted.state.mcp_manager.servers
            assert client.post(f"/api/mcp/servers/{item['id']}/disconnect").status_code == 200
        assert [item['name'] for item in client.get('/api/mcp/servers').json() if item['auto_connect']] == ['good']
