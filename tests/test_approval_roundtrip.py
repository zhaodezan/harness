import asyncio
import json

import httpx

from backend.app.main import create_app
from backend.app.tools import ToolDefinition
from model_test_utils import create_test_agent_async


class Secrets:
    def get(self, name): return "key"
    def set(self, name, value): pass
    def delete(self, name): pass


def test_side_effect_tool_waits_for_approval_before_execution(tmp_path):
    calls = []
    executions = []
    async def provider(request: httpx.Request):
        payload = json.loads(await request.aread())
        calls.append(payload)
        if len(calls) == 1:
            content = 'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"mail-1","type":"function","function":{"name":"send_mail","arguments":"{\\"body\\":\\"hello\\"}"}}]}}]}\n\ndata: [DONE]\n\n'
        else:
            content = 'data: {"choices":[{"delta":{"content":"Sent"}}]}\n\ndata: [DONE]\n\n'
        return httpx.Response(200, text=content)

    async def run():
        app = create_app(tmp_path / "approval.sqlite3", http_transport=httpx.MockTransport(provider), secret_store=Secrets())
        app.state.tool_registry.register(ToolDefinition(
            name="send_mail", description="Send an email",
            input_schema={"type": "object", "properties": {"body": {"type": "string"}}, "required": ["body"], "additionalProperties": False},
            risk="side_effect", handler=lambda args: executions.append(args) or {"sent": True},
        ))
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
                agent = (await create_test_agent_async(client, app, {
                    "name": "Helper", "base_url": "https://example.test/v1", "model": "m",
                })).json()
                session = (await client.post("/api/sessions", json={"agent_id": agent["id"]})).json()
                task = asyncio.create_task(client.post(f"/api/sessions/{session['id']}/messages", json={"content": "send mail", "approval_mode": "user_approval"}))
                for _ in range(100):
                    if app.state.pending_approvals:
                        break
                    await asyncio.sleep(0.01)
                assert app.state.pending_approvals
                approval_id = next(iter(app.state.pending_approvals))
                assert executions == []
                decision = await client.post(f"/api/approvals/{approval_id}", json={"approved": True})
                assert decision.status_code == 200
                response = await task
                assert "Sent" in response.text

    asyncio.run(run())
    assert executions == [{"body": "hello"}]
