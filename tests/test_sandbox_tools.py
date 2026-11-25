import asyncio
import json

import httpx

from backend.app.main import create_app
from model_test_utils import create_test_agent, create_test_agent_async


class Secrets:
    def get(self, name): return "key"
    def set(self, name, value): pass
    def delete(self, name): pass


def test_generated_python_is_only_offered_when_saved_and_requires_each_run_approval(tmp_path):
    calls = []
    executions = []
    async def provider(request):
        body = json.loads(await request.aread())
        calls.append(body)
        if len(calls) == 1:
            code_tool = next(tool for tool in body["tools"] if "CubeSandbox" in tool["function"]["description"])
            call = {"index": 0, "id": "sandbox-code-1", "type": "function", "function": {
                "name": code_tool["function"]["name"],
                "arguments": json.dumps({"code": "def run(arguments): return arguments['value']", "arguments": {"value": "safe-result"}}),
            }}
            frame = {"choices": [{"delta": {"tool_calls": [call]}}]}
            return httpx.Response(200, text="data: " + json.dumps(frame) + "\n\ndata: [DONE]\n\n")
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"done"}}]}\n\ndata: [DONE]\n\n')

    async def run():
        app = create_app(tmp_path / "sandbox-approval.sqlite3", data_dir=tmp_path / "data", secret_store=Secrets(), http_transport=httpx.MockTransport(provider))
        async def fake_execute(source, arguments):
            executions.append((source, arguments))
            return "safe-result"
        app.state.sandbox_executor.execute_python_async = fake_execute
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
                agent = (await create_test_agent_async(client, app, {
                    "name": "Code Agent", "model": "test", "sandbox_code_enabled": True,
                })).json()
                session = (await client.post("/api/sessions", json={"agent_id": agent["id"]})).json()
                task = asyncio.create_task(client.post(f"/api/sessions/{session['id']}/messages", json={"content": "calculate"}))
                for _ in range(200):
                    if app.state.pending_approvals:
                        break
                    await asyncio.sleep(0.01)
                assert app.state.pending_approvals
                assert executions == []
                approval_id = next(iter(app.state.pending_approvals))
                await client.post(f"/api/approvals/{approval_id}", json={"approved": True})
                response = await task
                assert response.status_code == 200
                assert "done" in response.text
        return calls

    requests = asyncio.run(run())
    assert executions == [("def run(arguments): return arguments['value']", {"value": "safe-result"})]
    assert any("CubeSandbox" in tool["function"]["description"] for tool in requests[0]["tools"])


def test_generated_code_tool_is_not_exposed_when_agent_setting_is_off(tmp_path):
    requests = []

    async def provider(request):
        requests.append(json.loads(await request.aread()))
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"no code"}}]}\n\ndata: [DONE]\n\n')

    app = create_app(tmp_path / "sandbox-disabled.sqlite3", data_dir=tmp_path / "data", secret_store=Secrets(), http_transport=httpx.MockTransport(provider))
    from fastapi.testclient import TestClient
    with TestClient(app) as client:
        agent = create_test_agent(client, {"name": "No Code", "model": "m"}).json()
        session = client.post("/api/sessions", json={"agent_id": agent["id"]}).json()
        client.post(f"/api/sessions/{session['id']}/messages", json={"content": "hello"})

    assert all(not any("CubeSandbox" in tool["function"]["description"] for tool in request.get("tools", [])) for request in requests)
