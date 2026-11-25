import json

import httpx
from fastapi.testclient import TestClient

from backend.app.main import create_app
from model_test_utils import create_test_agent
from backend.app.tools import ToolDefinition


class Secrets:
    def get(self, name): return "key"
    def set(self, name, value): pass
    def delete(self, name): pass


def test_plan_execute_replans_after_failed_step_without_repeating_failed_tool(tmp_path):
    calls = []
    async def provider(request: httpx.Request):
        payload = json.loads(await request.aread())
        calls.append(payload)
        if payload["stream"] is False:
            if len(calls) == 1:
                text = "1. 查找资料\n2. 总结结果"
            else:
                text = "1. 使用备用来源"
            return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})
        if len(calls) == 2:
            return httpx.Response(200, text='data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","type":"function","function":{"name":"lookup","arguments":"{}"}}]}}]}\n\ndata: [DONE]\n\n')
        if len(calls) == 3:
            return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"查找失败"}}]}\n\ndata: [DONE]\n\n')
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"备用来源完成"}}]}\n\ndata: [DONE]\n\n')

    app = create_app(tmp_path / "replan.sqlite3", http_transport=httpx.MockTransport(provider), secret_store=Secrets())
    app.state.tool_registry.register(ToolDefinition(
        name="lookup", description="Lookup", input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        risk="read_only", handler=lambda _: (_ for _ in ()).throw(RuntimeError("network")),
    ))
    with TestClient(app) as client:
        agent = create_test_agent(client, {"name": "Planner", "base_url": "https://example.test/v1", "model": "m", "mode": "plan_execute"}).json()
        session = client.post("/api/sessions", json={"agent_id": agent["id"]}).json()
        response = client.post(f"/api/sessions/{session['id']}/messages", json={"content": "完成调查"})

    assert response.status_code == 200
    assert "plan_replanning" in response.text
    assert '"revised":true' in response.text
    assert "备用来源完成" in response.text
    assert sum(1 for payload in calls if payload["stream"] is False) == 2
