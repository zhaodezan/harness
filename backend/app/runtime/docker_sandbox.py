"""Execute user-approved Python in a local, disposable Docker container.

The Docker CLI is deliberately the only integration point.  This module never
uses a Docker SDK, remote daemon URL, SSH context, registry login, or ``docker
pull``.  All untrusted request data is sent as one JSON document on stdin to a
fixed worker program.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import os
from dataclasses import dataclass
from pathlib import Path
import re
import secrets
import shutil
import tempfile
import threading
from typing import Any, Awaitable, Callable


MAX_CODE_BYTES = 256 * 1024
MAX_ARGUMENT_BYTES = 64 * 1024
MAX_OUTPUT_BYTES = 256 * 1024
DEFAULT_TIMEOUT_SECONDS = 30
MIN_TIMEOUT_SECONDS = 1
MAX_TIMEOUT_SECONDS = 300
MIN_MEMORY_MB = 128
MAX_MEMORY_MB = 8192
MIN_CPU_COUNT = 1
MAX_CPU_COUNT = 8
DEFAULT_IMAGE = "python:3.11-slim"
TMPFS_SIZE_MB = 64
IMAGE_CHECK_TIMEOUT_SECONDS = 5
CONTAINER_CLEANUP_TIMEOUT_SECONDS = 2
CIDFILE_WAIT_SECONDS = 0.5
DOCKER_LOCAL_CONTEXT_ARGS = ("--context", "default")

# This is intentionally conservative.  It permits normal local and namespaced
# image references, registry ports, tags, and digests, but no whitespace or
# shell metacharacters.  It is still passed as one argv item, never shell text.
_IMAGE_PATTERN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._/@:-]{0,254})\Z")
_CID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
WORKER_RESULT_PREFIX = "HARNESS_TOOL_RESULT:"


class DockerSandboxUnavailable(RuntimeError):
    """A safe-to-display error from the explicitly selected Docker sandbox."""


# A generic alias for callers that only need a sandbox-unavailable exception.
SandboxUnavailable = DockerSandboxUnavailable


@dataclass(frozen=True)
class DockerSandboxSettings:
    enabled: bool = True
    image: str = DEFAULT_IMAGE
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    memory_mb: int = 512
    cpu_count: int = 1
    network_enabled: bool = False

    @classmethod
    def from_value(cls, value: dict[str, Any] | "DockerSandboxSettings" | None) -> "DockerSandboxSettings":
        if isinstance(value, cls):
            return value
        if not isinstance(value, dict):
            raise DockerSandboxUnavailable(
                "本地 Docker 沙箱未配置；请配置本机 Docker 镜像"
            )

        enabled = value.get("enabled", False)
        if not isinstance(enabled, bool):
            raise DockerSandboxUnavailable("Docker 沙箱启用设置无效")
        if not enabled:
            raise DockerSandboxUnavailable("本地 Docker 沙箱未启用")

        image = value.get("image", DEFAULT_IMAGE)
        if not isinstance(image, str):
            raise DockerSandboxUnavailable("Docker 镜像名无效")
        image = image.strip()
        if not image or "://" in image or not _IMAGE_PATTERN.fullmatch(image):
            raise DockerSandboxUnavailable("Docker 镜像名无效；请使用本机已有的安全镜像名")

        timeout = value.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
        memory = value.get("memory_mb", 512)
        cpu = value.get("cpu_count", 1)
        network_enabled = value.get("network_enabled", False)
        if isinstance(timeout, bool) or not isinstance(timeout, int) or not MIN_TIMEOUT_SECONDS <= timeout <= MAX_TIMEOUT_SECONDS:
            raise DockerSandboxUnavailable("Docker 沙箱超时必须在 1 到 300 秒之间")
        if isinstance(memory, bool) or not isinstance(memory, int) or not MIN_MEMORY_MB <= memory <= MAX_MEMORY_MB:
            raise DockerSandboxUnavailable("Docker 沙箱内存必须在 128 到 8192 MB 之间")
        if isinstance(cpu, bool) or not isinstance(cpu, int) or not MIN_CPU_COUNT <= cpu <= MAX_CPU_COUNT:
            raise DockerSandboxUnavailable("Docker 沙箱 CPU 数必须在 1 到 8 之间")
        if not isinstance(network_enabled, bool):
            raise DockerSandboxUnavailable("Docker 沙箱网络设置无效")

        return cls(
            enabled=True,
            image=image,
            timeout_seconds=timeout,
            memory_mb=memory,
            cpu_count=cpu,
            network_enabled=network_enabled,
        )


# This program is a constant.  It receives all user-controlled values from
# stdin, calls either a named function with keyword arguments or legacy
# run(arguments), awaits async results, and emits the token-authenticated
# envelope parsed by the parent.
FIXED_WORKER = r'''import asyncio
import inspect
import json
import sys

WORKER_RESULT_PREFIX = "HARNESS_TOOL_RESULT:"
payload = json.load(sys.stdin)
source = payload.get("source")
arguments = payload.get("arguments")
function_name = payload.get("function_name")
result_token = payload.get("_result_token")
if not isinstance(source, str) or not isinstance(arguments, dict):
    raise ValueError("invalid worker request")
if not isinstance(result_token, str) or not result_token:
    raise ValueError("invalid worker token")
scope = {"__name__": "__main__", "__file__": "<docker-python>"}
exec(compile(source, "<docker-python>", "exec"), scope)
if function_name is not None:
    if not isinstance(function_name, str) or not function_name.isidentifier():
        raise ValueError("invalid function name")
    entry = scope.get(function_name)
    if not callable(entry):
        raise ValueError("function not found")
    result = entry(**arguments)
else:
    entry = scope.get("run")
    if not callable(entry):
        raise ValueError("run(arguments) is required")
    result = entry(arguments)
if inspect.isawaitable(result):
    result = asyncio.run(result)
print(WORKER_RESULT_PREFIX + result_token + ":" + json.dumps({"result": result}, ensure_ascii=True, allow_nan=False))
'''


_UNSET = object()


class _OutputLimitExceeded(Exception):
    pass


class DockerSandboxExecutor:
    """Run one Python request through the local Docker CLI only."""

    def __init__(
        self,
        settings_provider: Callable[[], dict[str, Any] | None] | dict[str, Any] | None,
        *,
        docker_path: str | Path | None | object = _UNSET,
        process_factory: Callable[..., Awaitable[Any]] | None = None,
        image_checker: Callable[[str], Any] | None = None,
        image_check_factory: Callable[[str], Any] | None = None,
        run_process: Callable[..., Awaitable[Any]] | None = None,
        subprocess_factory: Callable[..., Awaitable[Any]] | None = None,
    ) -> None:
        if callable(settings_provider):
            self.settings_provider = settings_provider
        else:
            self.settings_provider = lambda: settings_provider
        if docker_path is _UNSET:
            self.docker_path = shutil.which("docker")
        elif docker_path is None:
            self.docker_path = None
        else:
            self.docker_path = str(docker_path) if str(docker_path) else None
        self.process_factory = process_factory or run_process or subprocess_factory or asyncio.create_subprocess_exec
        self.image_checker = image_checker or image_check_factory

    def _settings(self, value: dict[str, Any] | DockerSandboxSettings | None = None) -> DockerSandboxSettings:
        return DockerSandboxSettings.from_value(
            self.settings_provider() if value is None else value
        )

    async def _default_image_check(self, image: str) -> bool:
        if not self.docker_path:
            return False
        process = None
        config_dir = tempfile.TemporaryDirectory(prefix="harness-docker-config-")
        try:
            async with asyncio.timeout(IMAGE_CHECK_TIMEOUT_SECONDS):
                created = self.process_factory(
                    self.docker_path,
                    *DOCKER_LOCAL_CONTEXT_ARGS,
                    "image",
                    "inspect",
                    image,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                    env=self._safe_environment(config_dir.name),
                )
                process = await created if inspect.isawaitable(created) else created
                waited = await process.wait()
            return getattr(process, "returncode", waited) == 0
        except asyncio.CancelledError:
            raise
        except Exception:
            return False
        finally:
            if process is not None:
                await self._cleanup_process(process, [])
            config_dir.cleanup()

    async def _check_image(self, image: str) -> bool:
        checker = self.image_checker
        if checker is None:
            return await self._default_image_check(image)
        try:
            result = checker(image)
            if inspect.isawaitable(result):
                result = await result
            return bool(result)
        except DockerSandboxUnavailable:
            raise
        except Exception:
            return False

    @staticmethod
    def _run_awaitable_sync(value):
        if not inspect.isawaitable(value):
            return value
        result: list[Any] = []
        error: list[BaseException] = []

        def runner():
            try:
                result.append(asyncio.run(value))
            except BaseException as exc:  # sanitized below
                error.append(exc)

        thread = threading.Thread(target=runner, daemon=True)
        thread.start()
        thread.join()
        if error:
            return False
        return result[0] if result else False

    async def is_configured_async(
        self, value: dict[str, Any] | DockerSandboxSettings | None = None
    ) -> bool:
        """Asynchronously probe local Docker availability without blocking the event loop."""
        if not self.docker_path:
            return False
        try:
            settings = self._settings(value)
            return bool(await self._check_image(settings.image))
        except Exception:
            return False

    def is_configured(
        self, value: dict[str, Any] | DockerSandboxSettings | None = None
    ) -> bool:
        """Return whether enabled settings, the CLI, and a local image are usable."""
        if not self.docker_path:
            return False
        try:
            settings = self._settings(value)
            checked = self._check_image(settings.image)
            return bool(self._run_awaitable_sync(checked))
        except Exception:
            return False

    @staticmethod
    def _safe_environment(config_dir: str | None = None) -> dict[str, str]:
        # In particular, do not inherit DOCKER_HOST/DOCKER_CONTEXT/credential
        # variables.  An empty per-call Docker config also prevents the CLI from
        # reading a user's current remote context or credential configuration.
        allowed = {
            "PATH", "PATHEXT", "SYSTEMROOT", "SystemRoot", "WINDIR",
            "TEMP", "TMP", "TMPDIR",
        }
        environment = {key: value for key, value in os.environ.items() if key in allowed}
        if config_dir is not None:
            environment["DOCKER_CONFIG"] = config_dir
        return environment

    async def _ensure_configured(self, settings: DockerSandboxSettings) -> None:
        if not self.docker_path:
            raise DockerSandboxUnavailable(
                "本机 Docker CLI 不可用；请安装并启动 Docker Desktop 后重试"
            )
        if not await self._check_image(settings.image):
            raise DockerSandboxUnavailable(
                "本机 Docker 引擎或镜像不可用；请启动 Docker 并确认镜像已存在（不会自动拉取）"
            )

    @staticmethod
    async def _read_limited(stream, total: list[int]) -> bytes:
        output = bytearray()
        while True:
            chunk = await stream.read(8192)
            if not chunk:
                return bytes(output)
            if isinstance(chunk, str):
                chunk = chunk.encode("utf-8", "replace")
            total[0] += len(chunk)
            if total[0] > MAX_OUTPUT_BYTES:
                raise _OutputLimitExceeded
            output.extend(chunk)

    @staticmethod
    async def _cleanup_process(process, readers: list[asyncio.Task]) -> None:
        if process is None:
            return
        stdin = getattr(process, "stdin", None)
        if stdin is not None:
            try:
                stdin.close()
            except Exception:
                pass

        cleanup_error = False
        try:
            if getattr(process, "returncode", None) is None:
                try:
                    process.terminate()
                except Exception:
                    cleanup_error = True
                try:
                    await asyncio.wait_for(process.wait(), timeout=0.5)
                except BaseException:
                    cleanup_error = True
                    try:
                        process.kill()
                    except Exception:
                        cleanup_error = True
                    try:
                        await asyncio.wait_for(process.wait(), timeout=0.5)
                    except BaseException:
                        cleanup_error = True

                if getattr(process, "returncode", None) is None:
                    cleanup_error = True
            if cleanup_error and getattr(process, "returncode", None) is None:
                raise DockerSandboxUnavailable(
                    "无法停止 Docker 沙箱进程；请检查 Docker Desktop 状态"
                )
        finally:
            for reader in readers:
                if not reader.done():
                    reader.cancel()
            if readers:
                await asyncio.gather(*readers, return_exceptions=True)

    @staticmethod
    def _read_cidfile(cidfile: Path) -> str | None:
        try:
            value = cidfile.read_text(encoding="ascii").strip()
        except FileNotFoundError:
            return None
        except (OSError, UnicodeError):
            raise DockerSandboxUnavailable(
                "无法读取 Docker 容器清理元数据"
            ) from None
        if not value:
            return None
        if not _CID_PATTERN.fullmatch(value):
            raise DockerSandboxUnavailable(
                "Docker 容器清理元数据格式无效"
            )
        return value

    @classmethod
    async def _read_cidfile_bounded(
        cls, cidfile: Path, *, wait: bool = False
    ) -> str | None:
        deadline = asyncio.get_running_loop().time() + (CIDFILE_WAIT_SECONDS if wait else 0)
        while True:
            container_id = cls._read_cidfile(cidfile)
            if container_id is not None or not wait:
                return container_id
            if asyncio.get_running_loop().time() >= deadline:
                return None
            await asyncio.sleep(0.01)

    async def _remove_container(
        self, container_id: str, config_dir: tempfile.TemporaryDirectory[str]
    ) -> None:
        process = None
        try:
            async with asyncio.timeout(CONTAINER_CLEANUP_TIMEOUT_SECONDS):
                created = self.process_factory(
                    self.docker_path,
                    *DOCKER_LOCAL_CONTEXT_ARGS,
                    "rm",
                    "-f",
                    container_id,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                    env=self._safe_environment(config_dir.name),
                )
                process = await created if inspect.isawaitable(created) else created
                exit_code = await process.wait()
            if getattr(process, "returncode", exit_code) != 0:
                raise DockerSandboxUnavailable(
                    "无法清理 Docker 沙箱容器；请检查 Docker Desktop 状态"
                )
        except DockerSandboxUnavailable:
            raise
        except asyncio.CancelledError:
            raise DockerSandboxUnavailable(
                "Docker 沙箱容器清理被中断；请检查 Docker Desktop 状态"
            ) from None
        except Exception:
            raise DockerSandboxUnavailable(
                "无法清理 Docker 沙箱容器；请检查 Docker Desktop 状态"
            ) from None
        finally:
            if process is not None:
                await self._cleanup_process(process, [])

    async def _cleanup_run(
        self,
        process,
        readers: list[asyncio.Task],
        cidfile: Path,
        config_dir: tempfile.TemporaryDirectory[str],
        *,
        force_container_cleanup: bool,
    ) -> None:
        cleanup_error: DockerSandboxUnavailable | None = None
        container_id: str | None = None
        try:
            container_id = await self._read_cidfile_bounded(
                cidfile, wait=force_container_cleanup
            )
        except DockerSandboxUnavailable as error:
            cleanup_error = error
        try:
            await self._cleanup_process(process, readers)
        except DockerSandboxUnavailable as error:
            cleanup_error = error
        if force_container_cleanup and container_id is None:
            try:
                container_id = await self._read_cidfile_bounded(cidfile, wait=True)
            except DockerSandboxUnavailable as error:
                cleanup_error = cleanup_error or error
        if force_container_cleanup and container_id is not None:
            try:
                await self._remove_container(container_id, config_dir)
            except DockerSandboxUnavailable as error:
                cleanup_error = cleanup_error or error
        if cleanup_error is not None:
            raise cleanup_error

    async def _cleanup_shielded(
        self,
        process,
        readers: list[asyncio.Task],
        cidfile: Path,
        config_dir: tempfile.TemporaryDirectory[str],
        *,
        force_container_cleanup: bool,
    ) -> None:
        cleanup = asyncio.create_task(self._cleanup_run(
            process,
            readers,
            cidfile,
            config_dir,
            force_container_cleanup=force_container_cleanup,
        ))
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            await asyncio.shield(cleanup)

    @staticmethod
    def _parse_result(stdout: bytes, token: str) -> Any:
        marker = f"{WORKER_RESULT_PREFIX}{token}:".encode("ascii")
        found = False
        for line in stdout.splitlines():
            if not line.startswith(marker):
                continue
            found = True
            try:
                envelope = json.loads(line[len(marker):].decode("utf-8"))
            except (UnicodeDecodeError, TypeError, ValueError, json.JSONDecodeError):
                raise DockerSandboxUnavailable("Docker 沙箱未返回有效 JSON 结果") from None
            if not isinstance(envelope, dict) or "result" not in envelope:
                raise DockerSandboxUnavailable("Docker 沙箱返回的数据格式无效")
            return envelope["result"]
        if found:
            raise DockerSandboxUnavailable("Docker 沙箱返回的数据格式无效")
        raise DockerSandboxUnavailable("Docker 沙箱未返回有效结果")

    async def execute_python_async(
        self,
        source: str,
        arguments: dict[str, Any],
        *,
        timeout: int | None = None,
        function_name: str | None = None,
    ) -> Any:
        if not isinstance(source, str) or len(source.encode("utf-8")) > MAX_CODE_BYTES:
            raise ValueError("Python 源码不能大于 256 KiB")
        if not isinstance(arguments, dict):
            raise ValueError("Python Tool 参数必须是 JSON 对象")
        if function_name is not None and (
            not isinstance(function_name, str) or not function_name.isidentifier()
        ):
            raise ValueError("Python Tool 入口必须是有效的函数名")
        try:
            arguments_json = json.dumps(arguments, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError):
            raise ValueError("Python Tool 参数必须是有效 JSON") from None
        if len(arguments_json.encode("utf-8")) > MAX_ARGUMENT_BYTES:
            raise ValueError("Python Tool 参数不能大于 64 KiB")

        settings = self._settings()
        effective_timeout = settings.timeout_seconds if timeout is None else timeout
        if (
            isinstance(effective_timeout, bool)
            or not isinstance(effective_timeout, int)
            or not MIN_TIMEOUT_SECONDS <= effective_timeout <= settings.timeout_seconds
        ):
            raise ValueError("单次执行超时不能超过 Docker 沙箱配置上限")
        await self._ensure_configured(settings)

        token = secrets.token_hex(16)
        payload = json.dumps(
            {
                "source": source,
                "arguments": arguments,
                "function_name": function_name,
                "_result_token": token,
            },
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        config_dir = tempfile.TemporaryDirectory(prefix="harness-docker-config-")
        cidfile = Path(config_dir.name) / "container.cid"
        force_container_cleanup = True
        command = [
            self.docker_path,
            *DOCKER_LOCAL_CONTEXT_ARGS,
            "run",
            "--pull=never",
            "--cidfile",
            str(cidfile),
            "--rm",
            "-i",
            "--init",
            "--read-only",
            "--tmpfs",
            f"/tmp:rw,noexec,nosuid,size={TMPFS_SIZE_MB}m",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--pids-limit",
            "128",
            "--memory",
            f"{settings.memory_mb}m",
            "--cpus",
            str(settings.cpu_count),
        ]
        if not settings.network_enabled:
            command.extend(["--network", "none"])
        command.extend([settings.image, "python", "-I", "-B", "-c", FIXED_WORKER])

        process = None
        readers: list[asyncio.Task] = []
        total = [0]
        try:
            try:
                created = self.process_factory(
                    *command,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=self._safe_environment(config_dir.name),
                )
                process = await created if inspect.isawaitable(created) else created
            except Exception:
                raise DockerSandboxUnavailable(
                    "无法启动本机 Docker CLI；请确认 Docker Desktop 正在运行"
                ) from None

            stdin = getattr(process, "stdin", None)
            if stdin is None:
                raise DockerSandboxUnavailable("Docker 沙箱输入通道不可用")
            stdin.write(payload)
            await stdin.drain()
            stdin.close()
            stdout_stream = getattr(process, "stdout", None)
            stderr_stream = getattr(process, "stderr", None)
            if stdout_stream is None or stderr_stream is None:
                raise DockerSandboxUnavailable("Docker 沙箱输出通道不可用")
            readers = [
                asyncio.create_task(self._read_limited(stdout_stream, total)),
                asyncio.create_task(self._read_limited(stderr_stream, total)),
            ]
            async with asyncio.timeout(effective_timeout):
                stdout, _stderr = await asyncio.gather(*readers)
                exit_code = await process.wait()
            if exit_code != 0:
                raise DockerSandboxUnavailable(
                    "Docker 沙箱中的 Python 执行失败；请检查源码和本机镜像"
                )
            result = self._parse_result(stdout, token)
            force_container_cleanup = False
            return result
        except asyncio.TimeoutError:
            raise DockerSandboxUnavailable("Docker 沙箱执行超时，已停止") from None
        except _OutputLimitExceeded:
            raise DockerSandboxUnavailable("Docker 沙箱输出超过 256 KiB 限制，已停止") from None
        except asyncio.CancelledError:
            raise
        except DockerSandboxUnavailable:
            raise
        except Exception:
            raise DockerSandboxUnavailable(
                "Docker 沙箱执行失败；未回退到本机执行"
            ) from None
        finally:
            try:
                await self._cleanup_shielded(
                    process,
                    readers,
                    cidfile,
                    config_dir,
                    force_container_cleanup=force_container_cleanup,
                )
            finally:
                config_dir.cleanup()
