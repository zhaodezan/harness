import asyncio
import json
import os
from types import SimpleNamespace

import httpx
import pytest

from backend.app.runtime.docker_sandbox import DockerSandboxUnavailable
from backend.app.runtime.sandbox_tools import execute_generated_code, execute_skill_script, sandbox_code_tool

from backend.app.main import create_app
from model_test_utils import create_test_agent, create_test_agent_async


class RecordingExecutor:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def execute_python_async(self, source, arguments):
        self.calls.append((source, arguments))
        return self.result


class RecordingLocalExecutor:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def execute(self, source, arguments, *, cwd):
        self.calls.append((source, arguments, cwd))
        return self.result


@pytest.mark.skipif(os.name == 'nt', reason='Non-Windows regression path')
def test_generated_code_local_workspace_keeps_non_windows_sandbox_path(tmp_path):
    sandbox = RecordingExecutor('sandbox-result')
    local = RecordingLocalExecutor('local-result')
    state = SimpleNamespace(sandbox_executor=sandbox, local_code_executor=local)

    result = asyncio.run(execute_generated_code(
        state, {'execution_mode': 'local', 'path': str(tmp_path)},
        {'code': 'return 1', 'arguments': {'x': 1}},
    ))

    assert result == 'sandbox-result'
    assert sandbox.calls == [('return 1', {'x': 1})]
    assert local.calls == []


def test_generated_code_and_skill_script_fail_closed_without_sandbox_executor(tmp_path):
    state = SimpleNamespace(sandbox_executor=None, local_code_executor=RecordingLocalExecutor('local-result'))
    workspace = {'execution_mode': 'sandbox', 'path': str(tmp_path)}

    with pytest.raises(DockerSandboxUnavailable, match='Docker 沙箱'):
        asyncio.run(execute_generated_code(
            state, workspace, {'code': 'return 1', 'arguments': {}},
        ))
    with pytest.raises(DockerSandboxUnavailable, match='Docker 沙箱'):
        asyncio.run(execute_skill_script(state, workspace, 'return 1', {}))



    sandbox = RecordingExecutor('sandbox-result')
    local = RecordingLocalExecutor('local-result')
    state = SimpleNamespace(sandbox_executor=sandbox, local_code_executor=local)

    result = asyncio.run(execute_skill_script(
        state, {'execution_mode': 'sandbox', 'path': str(tmp_path)}, 'return 1', {'x': 1},
    ))

    assert result == 'sandbox-result'
    assert sandbox.calls == [('return 1', {'x': 1})]
    assert local.calls == []


@pytest.mark.skipif(os.name == 'nt', reason='Non-Windows regression path')
def test_skill_script_local_workspace_keeps_non_windows_sandbox_path(tmp_path):
    sandbox = RecordingExecutor('sandbox-result')
    local = RecordingLocalExecutor('local-result')
    state = SimpleNamespace(sandbox_executor=sandbox, local_code_executor=local)

    result = asyncio.run(execute_skill_script(
        state, {'execution_mode': 'local', 'path': str(tmp_path)}, 'return 1', {'x': 1},
    ))

    assert result == 'sandbox-result'
    assert sandbox.calls == [('return 1', {'x': 1})]
    assert local.calls == []


@pytest.mark.skipif(os.name != 'nt', reason='Requires native Windows PowerShell routing')
def test_skill_script_local_workspace_uses_local_executor(tmp_path):
    sandbox = RecordingExecutor('sandbox-result')
    local = RecordingLocalExecutor('local-result')
    state = SimpleNamespace(sandbox_executor=sandbox, local_code_executor=local)

    result = asyncio.run(execute_skill_script(
        state, {'execution_mode': 'local', 'path': str(tmp_path)}, 'return 1', {'x': 1},
    ))

    assert result == 'local-result'
    assert local.calls == [('return 1', {'x': 1}, tmp_path.resolve())]
    assert sandbox.calls == []


def test_sandbox_code_tool_uses_stable_docker_plugin_id():
    tool = sandbox_code_tool([], mode="sandbox")
    assert tool["plugin_id"] == "internal:docker-sandbox-code"


class Secrets:
    def get(self, name): return "key"
    def set(self, name, value): pass
    def delete(self, name): pass


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
            code_tool = next(tool for tool in body["tools"] if "Docker sandbox" in tool["function"]["description"])
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
        app.state.sandbox_executor.is_configured = lambda: True
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
                agent = (await create_test_agent_async(client, app, {
                    "name": "Code Agent", "model": "test", "sandbox_code_enabled": True,
                })).json()
                session = (await client.post("/api/sessions", json={
                    "agent_id": agent["id"],
                    "workspace": {"execution_mode": "sandbox"},
                })).json()
                task = asyncio.create_task(client.post(f"/api/sessions/{session['id']}/messages", json={"content": "calculate", "approval_mode": "user_approval"}))
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
    assert any("Docker sandbox" in tool["function"]["description"] for tool in requests[0]["tools"])


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

    assert all(not any("Docker sandbox" in tool["function"]["description"] for tool in request.get("tools", [])) for request in requests)
