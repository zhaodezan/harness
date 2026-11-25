import asyncio
import threading

import pytest

from backend.app.runtime.cube_sandbox import SandboxExecutor, SandboxUnavailable


class FakeConfig:
    def __init__(self, **values):
        self.values = values


class FakeSdk:
    Config = FakeConfig

    def __init__(self, *, fail=False):
        self.fail = fail
        self.created = []
        self.killed = 0
        self.writes = []
        self.command = None

    def create(self, **kwargs):
        self.created.append(kwargs)
        if self.fail:
            raise RuntimeError("service failure")
        return self

    @property
    def files(self):
        return self

    def write(self, path, content):
        self.writes.append((path, content))

    @property
    def commands(self):
        return self

    def run(self, command, timeout):
        self.command = (command, timeout)
        return type("Result", (), {"exit_code": 0, "stdout": '{"result":"ok"}', "stderr": ""})()

    def kill(self):
        self.killed += 1


def settings():
    return {
        "api_url": "https://cube.example.test/cubeapi/v1",
        "template_id": "python-template",
        "api_key": "never-return-this",
        "timeout_seconds": 30,
        "allow_internet_access": True,
    }


def test_sandbox_executor_fails_closed_when_not_configured():
    sdk = FakeSdk()
    executor = SandboxExecutor(lambda: None, sdk=type("Sdk", (), {"Config": FakeConfig, "Sandbox": sdk})())

    with pytest.raises(SandboxUnavailable, match="未配置"):
        executor.execute_python("print('unsafe')", {})

    assert sdk.created == []


def test_python_executes_in_ephemeral_sandbox_without_host_mounts_and_cleans_up():
    sdk = FakeSdk()
    executor = SandboxExecutor(settings, sdk=type("Sdk", (), {"Config": FakeConfig, "Sandbox": sdk})())

    result = executor.execute_python("def run(arguments): return 'ok'", {"x": 1})

    assert result == "ok"
    create = sdk.created[0]
    assert create["template"] == "python-template"
    assert create["allow_internet_access"] is True
    assert create["volume_mounts"] is None
    assert "169.254.169.254" in create["network"]["deny_out"]
    assert sdk.killed == 1
    assert len(sdk.writes) == 3
    assert sdk.command[1] == 30


def test_sandbox_creation_failure_is_not_a_host_execution_fallback():
    sdk = FakeSdk(fail=True)
    executor = SandboxExecutor(settings, sdk=type("Sdk", (), {"Config": FakeConfig, "Sandbox": sdk})())

    with pytest.raises(SandboxUnavailable, match="沙箱执行失败"):
        executor.execute_python("print('must not run locally')", {})

    assert sdk.killed == 0


def test_cancelling_running_code_kills_the_active_sandbox():
    class BlockingSdk(FakeSdk):
        def __init__(self):
            super().__init__()
            self.started = threading.Event()
            self.released = threading.Event()

        def run(self, command, timeout):
            self.command = (command, timeout)
            self.started.set()
            self.released.wait(timeout=3)
            return type("Result", (), {"exit_code": 0, "stdout": '{"result":"ok"}', "stderr": ""})()

        def kill(self):
            self.killed += 1
            self.released.set()

    async def run_test():
        sdk = BlockingSdk()
        executor = SandboxExecutor(settings, sdk=type("Sdk", (), {"Config": FakeConfig, "Sandbox": sdk})())
        task = asyncio.create_task(executor.execute_python_async("def run(arguments): return 'ok'", {}))
        assert await asyncio.to_thread(sdk.started.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert sdk.killed >= 1

    asyncio.run(run_test())


def test_local_python_tool_fails_closed_if_sandbox_executor_missing(tmp_path):
    from backend.app.tool_plugins import LocalToolManager
    from backend.app.tools import ToolRegistry

    registry = ToolRegistry()
    manager = LocalToolManager(tmp_path / "tools", registry)
    manager.install_source('''TOOL = {"name": "secure", "description": "Secure", "input_schema": {"type": "object", "properties": {}}, "risk": "read_only"}\ndef run(arguments): return "must not run locally"\n''')

    with pytest.raises(SandboxUnavailable, match="未配置"):
        asyncio.run(registry.execute("secure", {}, approved=True))


def test_local_python_tool_metadata_is_parsed_without_executing_source(tmp_path):
    from backend.app.tool_plugins import LocalToolManager
    from backend.app.tools import ToolRegistry

    marker = tmp_path / "executed.txt"
    source = f'''from pathlib import Path
Path({str(marker)!r}).write_text("executed")
TOOL = {{"name": "safe", "description": "Tool", "input_schema": {{"type": "object", "properties": {{}}}}, "risk": "read_only"}}
def run(arguments): return "ok"
'''
    manager = LocalToolManager(tmp_path / "tools", ToolRegistry(), sandbox_executor=lambda *_: "ok")

    installed = manager.install_source(source)
    LocalToolManager(tmp_path / "tools", ToolRegistry()).load_installed()

    assert installed["name"] == "safe"
    assert not marker.exists()
