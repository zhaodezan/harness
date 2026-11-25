import httpx
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.tools import ToolDefinition
from model_test_utils import create_test_agent


class Secrets:
    def get(self, name): return "key"
    def set(self, name, value): pass
    def delete(self, name): pass


def test_readonly_python_tool_runs_and_returns_result_to_model(tmp_path):
    requests = []
    async def provider(request: httpx.Request):
        import json
        body = json.loads(await request.aread())
        requests.append(body)
        if len(requests) == 1:
            return httpx.Response(200, text='data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","type":"function","function":{"name":"lookup","arguments":"{\\"key\\":\\"alpha\\"}"}}]}}]}\n\ndata: [DONE]\n\n')
        assert requests[-1]["messages"][-1]["role"] == "tool"
        assert "alpha-result" in requests[-1]["messages"][-1]["content"]
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"found"}}]}\n\ndata: [DONE]\n\n')

    app = create_app(tmp_path / "tool-run.sqlite3", http_transport=httpx.MockTransport(provider), secret_store=Secrets())
    app.state.tool_registry.register(ToolDefinition(
        name="lookup", description="Look up a value",
        input_schema={"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"], "additionalProperties": False},
        risk="read_only", handler=lambda args: args["key"] + "-result",
    ))
    with TestClient(app) as client:
        agent = create_test_agent(client, {"name": "Helper", "base_url": "https://example.test/v1", "model": "m"}).json()
        session = client.post("/api/sessions", json={"agent_id": agent["id"]}).json()
        response = client.post(f"/api/sessions/{session['id']}/messages", json={"content": "lookup alpha"})
        assert response.status_code == 200
        assert '"text":"found"' in response.text

    assert len(requests) == 2
