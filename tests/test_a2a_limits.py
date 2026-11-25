import asyncio

import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.runtime.a2a import A2AService, DelegationBudget
from backend.app.db import database_connection
from model_test_utils import create_test_agent


async def never_called(*args):
    raise AssertionError("budget/loop rejections must happen before invoking the runner")


def test_a2a_rejects_calls_at_depth_and_call_budget_limits(tmp_path):
    app = create_app(tmp_path / "a2a-limits.sqlite3", data_dir=tmp_path / "data")
    with TestClient(app) as client:
        source = create_test_agent(client, {"name": "Source"}).json()
        target = create_test_agent(client, {"name": "Target"}).json()
        client.patch(f"/api/agents/{source['id']}", json={"selected_agents": [target["id"]]})
        service = A2AService(app.state.database_path, runner=never_called)

        depth_limited = asyncio.run(service.delegate(
            source["id"], target["id"], "work", DelegationBudget([source["id"], "mid-1", "mid-2"])
        ))
        calls_limited_budget = DelegationBudget([source["id"]])
        calls_limited_budget.calls = 4
        call_limited = asyncio.run(service.delegate(source["id"], target["id"], "work", calls_limited_budget))
        cyclic = asyncio.run(service.delegate(
            source["id"], target["id"], "work", DelegationBudget([source["id"], target["id"]])
        ))

    assert depth_limited.status.state == "rejected"
    assert call_limited.status.state == "rejected"
    assert cyclic.status.state == "rejected"


def test_a2a_timeout_restores_chain_and_returns_failed_task(tmp_path):
    app = create_app(tmp_path / "a2a-timeout.sqlite3", data_dir=tmp_path / "data")
    with TestClient(app) as client:
        source = create_test_agent(client, {"name": "Source"}).json()
        target = create_test_agent(client, {"name": "Target"}).json()
        client.patch(f"/api/agents/{source['id']}", json={"selected_agents": [target["id"]]})
        async def slow_runner(*args):
            await asyncio.sleep(0.1)
        service = A2AService(app.state.database_path, runner=slow_runner)
        service_timeout = 0.01
        import backend.app.runtime.a2a as a2a_module
        original_timeout = a2a_module.DELEGATION_TIMEOUT_SECONDS
        a2a_module.DELEGATION_TIMEOUT_SECONDS = service_timeout
        budget = DelegationBudget([source["id"]])
        try:
            task = asyncio.run(service.delegate(source["id"], target["id"], "work", budget))
        finally:
            a2a_module.DELEGATION_TIMEOUT_SECONDS = original_timeout

    assert task.status.state == "failed"
    assert budget.chain == [source["id"]]
    assert budget.calls == 1


def test_a2a_cancellation_propagates_and_restores_chain(tmp_path):
    app = create_app(tmp_path / "a2a-cancel.sqlite3", data_dir=tmp_path / "data")
    with TestClient(app) as client:
        source = create_test_agent(client, {"name": "Source"}).json()
        target = create_test_agent(client, {"name": "Target"}).json()
        client.patch(f"/api/agents/{source['id']}", json={"selected_agents": [target["id"]]})
        started = asyncio.Event()
        async def slow_runner(*args):
            started.set()
            await asyncio.Event().wait()
        service = A2AService(app.state.database_path, runner=slow_runner)
        budget = DelegationBudget([source["id"]])

        async def run():
            delegation = asyncio.create_task(service.delegate(source["id"], target["id"], "work", budget))
            await started.wait()
            delegation.cancel()
            with pytest.raises(asyncio.CancelledError):
                await delegation

        asyncio.run(run())

    assert budget.chain == [source["id"]]
    assert budget.calls == 1
