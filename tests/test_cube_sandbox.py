import asyncio
import json
from pathlib import Path
import threading

import pytest

from backend.app.runtime.docker_sandbox import (
    MAX_OUTPUT_BYTES,
    DockerSandboxExecutor,
    DockerSandboxSettings,
    DockerSandboxUnavailable,
)


class FakeStdin:
    def __init__(self):
        self.data = bytearray()
        self.closed = False

    def write(self, data):
        self.data.extend(data)

    async def drain(self):
        return None

    def close(self):
        self.closed = True


class FakeStream:
    def __init__(self, data=b"", *, wait_event=None):
        self.data = data
        self.wait_event = wait_event

    async def read(self, _size=-1):
        if self.wait_event is not None:
            await self.wait_event.wait()
        data, self.data = self.data, b""
        return data


class FakeProcess:
    def __init__(self, stdout=b"", stderr=b"", returncode=0, *, block=False):
        self.stdin = FakeStdin()
        self.stdout = FakeStream(stdout, wait_event=asyncio.Event() if block else None)
        self.stderr = FakeStream(stderr, wait_event=asyncio.Event() if block else None)
        self.returncode = None
        self.final_returncode = returncode
        self.terminated = 0
        self.killed = 0

    async def wait(self):
        self.returncode = self.final_returncode
        return self.returncode

    def terminate(self):
        self.terminated += 1
        self.returncode = self.final_returncode

    def kill(self):
        self.killed += 1
        self.returncode = self.final_returncode


class ProcessFactory:
    def __init__(self, process):
        self.process = process
        self.calls = []

    async def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.process


class CidfileProcessFactory:
    def __init__(self, run_process, cleanup_process, container_id="abc123-container"):
        self.run_process = run_process
        self.cleanup_process = cleanup_process
        self.container_id = container_id
        self.calls = []

    async def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if "--cidfile" in args:
            cidfile = Path(args[args.index("--cidfile") + 1])
            cidfile.write_text(self.container_id, encoding="ascii")
            return self.run_process
        assert list(args)[1:5] == ["--context", "default", "rm", "-f"]
        return self.cleanup_process


class HangingInspectProcess:
    def __init__(self):
        self.stdin = FakeStdin()
        self.stdout = None
        self.stderr = None
        self.returncode = None
        self.terminated = 0
        self.killed = 0

    async def wait(self):
        if self.returncode is not None:
            return self.returncode
        await asyncio.Event().wait()

    def terminate(self):
        self.terminated += 1
        self.returncode = 1

    def kill(self):
        self.killed += 1
        self.returncode = 1


class StubbornProcess:
    def __init__(self):
        self.stdin = FakeStdin()
        self.returncode = None

    async def wait(self):
        raise RuntimeError("wait failed")

    def terminate(self):
        raise RuntimeError("terminate failed")

    def kill(self):
        raise RuntimeError("kill failed")


def values(**overrides):
    result = {
        "enabled": True,
        "image": "python:3.11-slim",
        "timeout_seconds": 30,
        "memory_mb": 512,
        "cpu_count": 2,
        "network_enabled": False,
    }
    result.update(overrides)
    return result


def test_docker_settings_validate_safe_image_and_resource_limits():
    settings = DockerSandboxSettings.from_value(values())
    assert settings.image == "python:3.11-slim"
    assert settings.network_enabled is False

    for key, value in {
        "image": "https://registry.example/image",
        "timeout_seconds": 0,
        "memory_mb": 127,
        "cpu_count": 9,
    }.items():
        with pytest.raises(DockerSandboxUnavailable):
            DockerSandboxSettings.from_value(values(**{key: value}))

    with pytest.raises(DockerSandboxUnavailable):
        DockerSandboxSettings.from_value(values(image="image name"))
    with pytest.raises(DockerSandboxUnavailable):
        DockerSandboxSettings.from_value(values(timeout_seconds=True))


def test_disabled_or_missing_docker_fails_closed_without_process():
    process_factory = ProcessFactory(FakeProcess())
    executor = DockerSandboxExecutor(lambda: None, docker_path=None, process_factory=process_factory)

    with pytest.raises(DockerSandboxUnavailable, match="Docker"):
        asyncio.run(executor.execute_python_async("def run(arguments): return arguments", {}))

    assert process_factory.calls == []
    assert executor.is_configured() is False


def test_command_has_container_security_flags_and_source_only_in_stdin(monkeypatch):
    monkeypatch.setattr("backend.app.runtime.docker_sandbox.secrets.token_hex", lambda _size: "test-token")
    output = b'HARNESS_TOOL_RESULT:test-token:{"result":"ok"}\n'
    process = FakeProcess(output)
    process_factory = ProcessFactory(process)
    executor = DockerSandboxExecutor(
        lambda: values(), docker_path="docker", process_factory=process_factory,
        image_checker=lambda image: True,
    )

    assert asyncio.run(executor.execute_python_async("def run(arguments): return 'ok'", {"secret": "value"})) == "ok"
    argv, kwargs = process_factory.calls[0]
    command = list(argv)
    assert command[:4] == ["docker", "--context", "default", "run"]
    for flag in ("--pull=never", "--rm", "-i", "--init", "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges", "--network", "none"):
        assert flag in command
    assert "--tmpfs" in command
    assert "/tmp:rw,noexec,nosuid,size=64m" in command
    assert "--pids-limit" in command
    assert "128" in command
    assert "--memory" in command and "512m" in command
    assert "--cpus" in command and "2" in command
    assert command[-5:-2] == ["python", "-I", "-B"]
    assert "def run" not in " ".join(command)
    assert "secret" not in " ".join(command)
    assert kwargs["stdin"] is not None
    config_path = Path(kwargs["env"]["DOCKER_CONFIG"])
    assert not config_path.exists()
    payload = json.loads(bytes(process.stdin.data))
    assert payload["source"] == "def run(arguments): return 'ok'"
    assert payload["arguments"] == {"secret": "value"}
    assert payload["_result_token"] == "test-token"


def test_network_flag_is_omitted_only_when_network_enabled(monkeypatch):
    monkeypatch.setattr("backend.app.runtime.docker_sandbox.secrets.token_hex", lambda _size: "token")
    process_factory = ProcessFactory(FakeProcess(b'HARNESS_TOOL_RESULT:token:{"result":true}\n'))
    executor = DockerSandboxExecutor(
        lambda: values(network_enabled=True), docker_path="docker", process_factory=process_factory,
        image_checker=lambda image: True,
    )
    asyncio.run(executor.execute_python_async("def run(arguments): return True", {}))
    command = list(process_factory.calls[0][0])
    assert "--network" not in command


def test_image_inspect_uses_local_context_and_does_not_inherit_remote_context(monkeypatch):
    monkeypatch.setenv("DOCKER_CONTEXT", "remote-prod")
    monkeypatch.setenv("DOCKER_HOST", "ssh://remote.example")
    process = FakeProcess(returncode=0)
    process_factory = ProcessFactory(process)
    executor = DockerSandboxExecutor(
        lambda: values(), docker_path="docker", process_factory=process_factory,
    )

    assert executor.is_configured() is True
    argv, kwargs = process_factory.calls[0]
    assert list(argv)[:4] == ["docker", "--context", "default", "image"]
    assert list(argv)[4:] == ["inspect", "python:3.11-slim"]
    assert "DOCKER_CONTEXT" not in kwargs["env"]
    assert "DOCKER_HOST" not in kwargs["env"]
    assert not Path(kwargs["env"]["DOCKER_CONFIG"]).exists()


def test_image_inspect_timeout_terminates_process(monkeypatch):
    monkeypatch.setattr(
        "backend.app.runtime.docker_sandbox.IMAGE_CHECK_TIMEOUT_SECONDS", 0.01,
    )
    process = HangingInspectProcess()
    executor = DockerSandboxExecutor(
        lambda: values(), docker_path="docker", process_factory=ProcessFactory(process),
    )

    assert asyncio.run(executor._default_image_check("python:3.11-slim")) is False
    assert process.terminated + process.killed >= 1
    assert process.stdin.closed


def test_cleanup_failure_is_sanitized_instead_of_swallowed():
    with pytest.raises(DockerSandboxUnavailable, match="无法停止 Docker 沙箱进程"):
        asyncio.run(DockerSandboxExecutor._cleanup_process(StubbornProcess(), []))


    calls = []

    async def image_checker(image):
        calls.append(image)
        return True

    executor = DockerSandboxExecutor(lambda: values(), docker_path="docker", image_checker=image_checker)
    assert executor.is_configured() is True
    assert calls == ["python:3.11-slim"]


def test_nonzero_and_malformed_output_are_sanitized(monkeypatch):
    monkeypatch.setattr("backend.app.runtime.docker_sandbox.secrets.token_hex", lambda _size: "token")
    for process in (
        FakeProcess(b"not-json", stderr=b"password=secret"),
        FakeProcess(b"HARNESS_TOOL_RESULT:token:{not-json}\n"),
        FakeProcess(b"HARNESS_TOOL_RESULT:token:{\"other\": 1}\n"),
        FakeProcess(b"HARNESS_TOOL_RESULT:token:{\"result\": 1}\n", returncode=1),
    ):
        executor = DockerSandboxExecutor(
            lambda: values(), docker_path="docker", process_factory=ProcessFactory(process),
            image_checker=lambda image: True,
        )
        with pytest.raises(DockerSandboxUnavailable) as error:
            asyncio.run(executor.execute_python_async("def run(arguments): return 1", {}))
        assert "secret" not in str(error.value)
        assert "password" not in str(error.value)
        assert "def run" not in str(error.value)


def test_timeout_uses_container_id_cleanup_with_rm_force(monkeypatch):
    monkeypatch.setattr("backend.app.runtime.docker_sandbox.secrets.token_hex", lambda _size: "token")
    run_process = FakeProcess(block=True)
    rm_process = FakeProcess()
    process_factory = CidfileProcessFactory(run_process, rm_process)
    executor = DockerSandboxExecutor(
        lambda: values(timeout_seconds=1), docker_path="docker", process_factory=process_factory,
        image_checker=lambda image: True,
    )

    with pytest.raises(DockerSandboxUnavailable, match="超时"):
        asyncio.run(executor.execute_python_async("def run(arguments): return 1", {}, timeout=1))
    assert len(process_factory.calls) == 2
    cleanup_argv = list(process_factory.calls[1][0])
    assert cleanup_argv[:6] == ["docker", "--context", "default", "rm", "-f", "abc123-container"]
    assert run_process.terminated + run_process.killed >= 1


def test_container_cleanup_failure_replaces_original_failure_with_sanitized_error(monkeypatch):
    monkeypatch.setattr("backend.app.runtime.docker_sandbox.secrets.token_hex", lambda _size: "token")
    run_process = FakeProcess(block=True)
    rm_process = FakeProcess(returncode=1)
    process_factory = CidfileProcessFactory(run_process, rm_process)
    executor = DockerSandboxExecutor(
        lambda: values(timeout_seconds=1), docker_path="docker", process_factory=process_factory,
        image_checker=lambda image: True,
    )

    with pytest.raises(DockerSandboxUnavailable, match="无法清理 Docker 沙箱容器"):
        asyncio.run(executor.execute_python_async("def run(arguments): return 1", {}, timeout=1))


    monkeypatch.setattr("backend.app.runtime.docker_sandbox.secrets.token_hex", lambda _size: "token")
    timed_out = FakeProcess(block=True)
    executor = DockerSandboxExecutor(
        lambda: values(timeout_seconds=1), docker_path="docker", process_factory=ProcessFactory(timed_out),
        image_checker=lambda image: True,
    )
    with pytest.raises(DockerSandboxUnavailable, match="超时"):
        asyncio.run(executor.execute_python_async("def run(arguments): return 1", {}, timeout=1))
    assert timed_out.terminated + timed_out.killed >= 1
    assert timed_out.stdin.closed

    too_large = FakeProcess(b"x" * (MAX_OUTPUT_BYTES + 1))
    executor = DockerSandboxExecutor(
        lambda: values(), docker_path="docker", process_factory=ProcessFactory(too_large),
        image_checker=lambda image: True,
    )
    with pytest.raises(DockerSandboxUnavailable, match="输出"):
        asyncio.run(executor.execute_python_async("def run(arguments): return 1", {}))
    assert too_large.terminated + too_large.killed >= 1


def test_async_cancellation_terminates_process(monkeypatch):
    monkeypatch.setattr("backend.app.runtime.docker_sandbox.secrets.token_hex", lambda _size: "token")
    process = FakeProcess(block=True)
    executor = DockerSandboxExecutor(
        lambda: values(), docker_path="docker", process_factory=ProcessFactory(process),
        image_checker=lambda image: True,
    )

    async def run():
        task = asyncio.create_task(executor.execute_python_async("def run(arguments): return 1", {}))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert process.terminated + process.killed >= 1
    assert process.stdin.closed


def test_legacy_and_named_worker_protocol_is_embedded():
    from backend.app.runtime.docker_sandbox import FIXED_WORKER

    assert "function_name" in FIXED_WORKER
    assert "run(arguments)" in FIXED_WORKER
    assert "isawaitable" in FIXED_WORKER
    assert "HARNESS_TOOL_RESULT:" in FIXED_WORKER


def test_local_python_tool_fails_closed_without_docker_executor(tmp_path):
    from backend.app.tool_plugins import LocalToolManager
    from backend.app.tools import ToolRegistry

    registry = ToolRegistry()
    manager = LocalToolManager(tmp_path / "tools", registry)
    manager.install_source(
        'TOOL = {"name": "secure", "description": "Secure", '
        '"input_schema": {"type": "object", "properties": {}}, "risk": "read_only"}\n'
        'def run(arguments): return "must not run locally"\n'
    )

    with pytest.raises(DockerSandboxUnavailable):
        asyncio.run(registry.execute("secure", {}, approved=True))


def test_local_python_tool_metadata_is_parsed_without_executing_source(tmp_path):
    from backend.app.tool_plugins import LocalToolManager
    from backend.app.tools import ToolRegistry

    marker = tmp_path / "executed.txt"
    source = (
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('executed')\n"
        'TOOL = {"name": "safe", "description": "Tool", '
        '"input_schema": {"type": "object", "properties": {}}, "risk": "read_only"}\n'
        'def run(arguments): return "ok"\n'
    )
    manager = LocalToolManager(tmp_path / "tools", ToolRegistry(), sandbox_executor=lambda *_: "ok")

    installed = manager.install_source(source)
    assert installed["name"] == "safe"
    assert not marker.exists()
