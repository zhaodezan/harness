from fastapi.testclient import TestClient
import httpx

from backend.app.main import create_app
from backend.app.tools import ToolDefinition
from model_test_utils import create_test_agent


def test_agent_persists_only_existing_other_agent_delegation_targets(tmp_path):
    with TestClient(create_app(tmp_path / "delegation-config.sqlite3")) as client:
        source = create_test_agent(client, {"name": "Source"}).json()
        target = create_test_agent(client, {"name": "Target"}).json()
        updated = client.patch(f"/api/agents/{source['id']}", json={"selected_agents": [target["id"]]})
        fetched = client.get(f"/api/agents/{source['id']}").json()
        self_target = client.patch(f"/api/agents/{source['id']}", json={"selected_agents": [source["id"]]})
        missing_target = client.patch(f"/api/agents/{source['id']}", json={"selected_agents": ["f" * 32]})

    assert updated.status_code == 200
    assert updated.json()["selected_agents"] == [target["id"]]
    assert fetched["selected_agents"] == [target["id"]]
    assert self_target.status_code == 422
    assert missing_target.status_code == 422


def test_agent_stores_mcp_and_local_tool_selections_independently(tmp_path):
    with TestClient(create_app(tmp_path / "agents.sqlite3")) as client:
        agent = create_test_agent(client, {"name": "A"}).json()
        updated = client.patch(f"/api/agents/{agent['id']}", json={
            "selected_mcp_tools": ["mcp:server-1:search"],
            "selected_local_tools": ["tool-1"],
        }).json()
        fetched = client.get(f"/api/agents/{agent['id']}").json()

    assert updated["selected_mcp_tools"] == ["mcp:server-1:search"]
    assert updated["selected_local_tools"] == ["tool-1"]
    assert fetched["selected_mcp_tools"] == updated["selected_mcp_tools"]
    assert fetched["selected_local_tools"] == updated["selected_local_tools"]


def test_agent_only_exposes_selected_tools_and_rejects_unselected_calls(tmp_path):
    requests = []
    executed = []

    async def provider(request):
        import json
        body = json.loads(await request.aread())
        requests.append(body)
        if len(requests) == 1:
            call = {"index": 0, "id": "call-1", "type": "function", "function": {"name": "denied", "arguments": "{}"}}
            return httpx.Response(200, text=f'data: {{"choices":[{{"delta":{{"tool_calls":[{json.dumps(call)}]}}}}]}}\n\ndata: [DONE]\n\n')
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"done"}}]}\n\ndata: [DONE]\n\n')

    class Secrets:
        def get(self, name): return "key"
        def set(self, name, value): pass
        def delete(self, name): pass

    app = create_app(
        tmp_path / "tools.sqlite3", http_transport=httpx.MockTransport(provider),
        secret_store=Secrets(), data_dir=tmp_path / "data",
    )
    app.state.tool_registry.register(ToolDefinition(
        name="allowed", description="Allowed", input_schema={"type": "object", "properties": {}},
        risk="read_only", handler=lambda args: executed.append("allowed"), plugin_id="tool-allowed",
    ))
    app.state.tool_registry.register(ToolDefinition(
        name="denied", description="Denied", input_schema={"type": "object", "properties": {}},
        risk="read_only", handler=lambda args: executed.append("denied"), plugin_id="tool-denied",
    ))
    with TestClient(app) as client:
        agent = create_test_agent(client, {
            "name": "Restricted", "base_url": "https://example.test/v1", "model": "m",
            "selected_local_tools": ["tool-allowed"],
        }).json()
        session = client.post("/api/sessions", json={"agent_id": agent["id"]}).json()
        response = client.post(f"/api/sessions/{session['id']}/messages", json={"content": "run tool"})

    assert response.status_code == 200
    assert [item["function"]["name"] for item in requests[0]["tools"]] == ["allowed"]
    assert executed == []
    tool_results = [message for message in requests[1]["messages"] if message.get("role") == "tool"]
    assert "未经授权" in tool_results[0]["content"]
