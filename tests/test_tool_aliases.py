import re

import httpx
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.tools import ToolDefinition, ToolRegistry, model_tool_names
from model_test_utils import create_test_agent


class Secrets:
    def get(self, name): return "key"
    def set(self, name, value): pass
    def delete(self, name): pass


def test_mcp_tool_names_are_valid_and_reversible_for_provider():
    names = ["sample/lookup", "a-b/lookup", "local_foo"]
    aliases = model_tool_names(names)
    assert len(set(aliases.values())) == len(names)
    assert all(re.fullmatch(r"[A-Za-z0-9_-]{1,64}", alias) for alias in aliases.values())
    assert aliases["local_foo"] == "local_foo"


def test_runtime_maps_provider_alias_back_to_real_mcp_tool(tmp_path):
    seen = []
    calls = []
    async def provider(request: httpx.Request):
        import json
        body = json.loads(await request.aread())
        seen.append(body)
        if len(seen) == 1:
            alias = body["tools"][0]["function"]["name"]
            assert re.fullmatch(r"[A-Za-z0-9_-]{1,64}", alias)
            return httpx.Response(200, text=(
                'data: ' + json.dumps({"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_1", "function": {"name": alias, "arguments": "{}"}}]}}]}) + '\n\n'
                'data: [DONE]\n\n'
            ))
        assert "read-ok" in body["messages"][-1]["content"]
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"done"}}]}\n\ndata: [DONE]\n\n')

    app = create_app(tmp_path / "tool-alias.sqlite3", http_transport=httpx.MockTransport(provider), secret_store=Secrets())
    app.state.tool_registry.register(ToolDefinition(
        name="sample/lookup", description="Read", risk="read_only",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        handler=lambda args: calls.append(args) or "read-ok",
    ))
    with TestClient(app) as client:
        agent = create_test_agent(client, {"name": "Helper", "base_url": "https://provider.test/v1", "model": "m"}).json()
        session = client.post("/api/sessions", json={"agent_id": agent["id"]}).json()
        result = client.post(f"/api/sessions/{session['id']}/messages", json={"content": "lookup"})
    assert result.status_code == 200
    assert "done" in result.text
    assert calls == [{}]
