import httpx
from fastapi.testclient import TestClient

from backend.app.main import create_app
from model_test_utils import create_test_agent


class Secrets:
    def get(self, name): return "key" if name.endswith("api_key") else None
    def set(self, name, value): pass
    def delete(self, name): pass


def test_plan_execute_creates_plan_then_streams_execution(tmp_path):
    calls = []
    async def provider(request: httpx.Request):
        calls.append(request)
        body = await request.aread()
        import json
        payload = json.loads(body)
        if payload["stream"] is False:
            return httpx.Response(200, json={"choices": [{"message": {"content": "1. 检查问题\n2. 给出建议"}}]})
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"已完成"}}]}\n\ndata: [DONE]\n\n')

    app = create_app(tmp_path / "plan.sqlite3", http_transport=httpx.MockTransport(provider), secret_store=Secrets())
    with TestClient(app) as client:
        agent = create_test_agent(client, {"name": "Planner", "base_url": "https://example.test/v1", "model": "model", "mode": "plan_execute"}).json()
        session = client.post("/api/sessions", json={"agent_id": agent["id"]}).json()
        response = client.post(f"/api/sessions/{session['id']}/messages", json={"content": "帮我解决问题"})
        assert "event: plan" in response.text
        assert "检查问题" in response.text
        assert '"text":"已完成"' in response.text
        assert response.text.count('"event_type":"plan_step_started"') == 2
        assert response.text.count('"event_type":"plan_step_completed"') == 2

    assert len(calls) == 3
    execution_payloads = []
    for request in calls[1:]:
        execution_payloads.append(__import__('json').loads(request.content))
    assert all(any("执行计划：" in (message.get("content") or "") for message in payload["messages"]) for payload in execution_payloads)
