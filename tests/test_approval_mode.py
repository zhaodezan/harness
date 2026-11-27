import asyncio
import json

import httpx

from backend.app.main import create_app
from backend.app.runtime.approval_policy import (
    DEFAULT_APPROVAL_MODE,
    should_wait_for_approval,
)
from backend.app.tools import ToolDefinition
from model_test_utils import create_test_agent_async, create_test_model


class Secrets:
    def __init__(self):
        self.values = {}

    def get(self, name):
        return self.values.get(name)

    def set(self, name, value):
        self.values[name] = value

    def delete(self, name):
        self.values.pop(name, None)


def sse(delta):
    return httpx.Response(
        200,
        text=f'data: {json.dumps({"choices": [{"delta": delta}]})}\n\ndata: [DONE]\n\n',
    )


def invoke(tool, arguments, id="call"):
    return sse({
        "tool_calls": [{
            "index": 0,
            "id": id,
            "function": {"name": tool, "arguments": json.dumps(arguments)},
        }],
    })


def test_default_mode_is_allow_all():
    assert DEFAULT_APPROVAL_MODE == "allow_all"


def test_allow_all_skips_non_mcp_side_effect_approval():
    tool = {"risk": "side_effect", "plugin_id": "local-tool"}
    assert not should_wait_for_approval(tool, "allow_all")


def test_user_approval_waits_for_non_mcp_side_effect():
    tool = {"risk": "side_effect", "plugin_id": "local-tool"}
    assert should_wait_for_approval(tool, "user_approval")


def test_mcp_side_effect_never_waits_in_either_mode():
    tool = {"risk": "side_effect", "plugin_id": "mcp:server:write"}
    assert not should_wait_for_approval(tool, "allow_all")
    assert not should_wait_for_approval(tool, "user_approval")


def test_read_only_never_waits():
    tool = {"risk": "read_only", "plugin_id": "local-tool"}
    assert not should_wait_for_approval(tool, "allow_all")
    assert not should_wait_for_approval(tool, "user_approval")


def build_side_effect_app(tmp_path):
    calls, executions = [], []

    async def provider(request):
        body = json.loads(await request.aread())
        calls.append(body)
        if len(calls) == 1:
            return invoke("write_value", {"value": "ok"})
        return sse({"content": "done"})

    app = create_app(
        tmp_path / "approval.sqlite3",
        data_dir=tmp_path / "data",
        secret_store=Secrets(),
        http_transport=httpx.MockTransport(provider),
    )
    app.state.tool_registry.register(ToolDefinition(
        "write_value",
        "Write a value",
        {"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"]},
        "side_effect",
        lambda arguments: executions.append(arguments) or "ok",
    ))
    return app, calls, executions


def test_message_defaults_to_allow_all_and_does_not_create_pending_approval(tmp_path):
    async def run():
        app, calls, executions = build_side_effect_app(tmp_path)
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
                agent = (await create_test_agent_async(client, app, {"name": "Writer"})).json()
                session = (await client.post("/api/sessions", json={"agent_id": agent["id"]})).json()
                response = await client.post(
                    f"/api/sessions/{session['id']}/messages", json={"content": "write"}
                )
        assert response.status_code == 200
        assert "event: done" in response.text
        assert "event: approval_required" not in response.text
        assert 'approval_decision' in response.text
        assert executions == [{"value": "ok"}]
        assert len(calls) == 2

    asyncio.run(run())


def test_user_approval_pauses_main_message_until_decision(tmp_path):
    async def run():
        app, calls, executions = build_side_effect_app(tmp_path)
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
                agent = (await create_test_agent_async(client, app, {"name": "Writer"})).json()
                session = (await client.post("/api/sessions", json={"agent_id": agent["id"]})).json()
                task = asyncio.create_task(client.post(
                    f"/api/sessions/{session['id']}/messages",
                    json={"content": "write", "approval_mode": "user_approval"},
                ))
                for _ in range(200):
                    if app.state.pending_approvals:
                        break
                    await asyncio.sleep(0.01)
                assert app.state.pending_approvals
                assert executions == []
                approval_id = next(iter(app.state.pending_approvals))
                decision = await client.post(f"/api/approvals/{approval_id}", json={"approved": True})
                response = await task
        assert decision.status_code == 200
        assert response.status_code == 200
        assert "event: done" in response.text
        assert executions == [{"value": "ok"}]

    asyncio.run(run())


def test_debug_allow_all_does_not_create_pending_approval(tmp_path):
    async def run():
        app, calls, executions = build_side_effect_app(tmp_path)
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
                model_id = create_test_model(app)
                agent = (await client.post("/api/agents", json={
                    "name": "Debug Writer", "model_config_id": model_id,
                })).json()
                response = await client.post(f"/api/agents/{agent['id']}/debug", json={
                    "config": {"model_config_id": model_id},
                    "messages": [{"role": "user", "content": "write"}],
                    "approval_mode": "allow_all",
                })
        assert response.status_code == 200
        assert "event: done" in response.text
        assert "event: approval_required" not in response.text
        assert 'approval_decision' in response.text
        assert executions == [{"value": "ok"}]

    asyncio.run(run())
