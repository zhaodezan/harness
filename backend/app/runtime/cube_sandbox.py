from __future__ import annotations

import asyncio
import json
import re
import threading
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlsplit

MAX_CODE_BYTES = 256 * 1024
MAX_ARGUMENT_BYTES = 64 * 1024
MAX_OUTPUT_BYTES = 256 * 1024
DEFAULT_TIMEOUT_SECONDS = 30
MIN_TIMEOUT_SECONDS = 1
MAX_TIMEOUT_SECONDS = 300

DENIED_EGRESS = [
    "0.0.0.0/8",
    "10.0.0.0/8",
    "100.64.0.0/10",
    "127.0.0.0/8",
    "169.254.0.0/16",
    "172.16.0.0/12",
    "192.0.0.0/24",
    "192.168.0.0/16",
    "198.18.0.0/15",
    "224.0.0.0/4",
    "240.0.0.0/4",
    "::/128",
    "::1/128",
    "fc00::/7",
    "fe80::/10",
    "ff00::/8",
    "169.254.169.254",
    "metadata.google.internal",
    "metadata.google",
    "metadata.azure.internal",
]


class SandboxUnavailable(RuntimeError):
    """Raised when the configured remote sandbox cannot safely run code."""


@dataclass(frozen=True)
class SandboxSettings:
    api_url: str
    template_id: str
    api_key: str
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    allow_internet_access: bool = True
    enabled: bool = True

    @classmethod
    def from_value(cls, value: dict[str, Any] | None) -> "SandboxSettings":
        if not value:
            raise SandboxUnavailable("CubeSandbox 未配置，已拒绝在本机执行代码")
        api_url = value.get("api_url")
        template_id = value.get("template_id")
        api_key = value.get("api_key")
        timeout = value.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
        internet = value.get("allow_internet_access", True)
        enabled = value.get("enabled", True)
        if not all(isinstance(item, str) and item.strip() for item in (api_url, template_id, api_key)):
            raise SandboxUnavailable("CubeSandbox 配置不完整，已拒绝执行")
        parsed = urlsplit(api_url.strip())
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise SandboxUnavailable("CubeSandbox 地址必须是无凭据的 HTTPS URL")
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", template_id.strip()):
            raise SandboxUnavailable("CubeSandbox 模板 ID 格式无效")
        if isinstance(timeout, bool) or not isinstance(timeout, int) or not MIN_TIMEOUT_SECONDS <= timeout <= MAX_TIMEOUT_SECONDS:
            raise SandboxUnavailable("CubeSandbox 超时必须在 1 到 300 秒之间")
        if not isinstance(internet, bool) or not isinstance(enabled, bool):
            raise SandboxUnavailable("CubeSandbox 网络或启用设置无效")
        if not enabled:
            raise SandboxUnavailable("CubeSandbox 已禁用，已拒绝执行")
        return cls(api_url.strip().rstrip("/"), template_id.strip(), api_key, timeout, internet, enabled)


class SandboxExecutor:
    """Execute untrusted Python only in a fresh, unmounted CubeSandbox."""

    def __init__(self, settings_provider: Callable[[], dict[str, Any] | None], *, sdk=None):
        self.settings_provider = settings_provider
        self.sdk = sdk

    async def execute_python_async(
        self, source: str, arguments: dict[str, Any], *, timeout: int | None = None,
        function_name: str | None = None,
    ) -> Any:
        sandbox_holder: dict[str, Any] = {}
        holder_lock = threading.Lock()
        cancellation_requested = threading.Event()

        def on_created(sandbox):
            with holder_lock:
                sandbox_holder["sandbox"] = sandbox
            if cancellation_requested.is_set():
                try:
                    sandbox.kill()
                except Exception:
                    pass

        operation = asyncio.create_task(asyncio.to_thread(
            self.execute_python, source, arguments, timeout=timeout, function_name=function_name, _on_created=on_created
        ))
        operation.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)
        try:
            return await asyncio.shield(operation)
        except asyncio.CancelledError:
            cancellation_requested.set()
            with holder_lock:
                sandbox = sandbox_holder.get("sandbox")
            if sandbox is not None:
                try:
                    await asyncio.to_thread(sandbox.kill)
                except Exception:
                    pass
            raise

    def execute_python(
        self, source: str, arguments: dict[str, Any], *, timeout: int | None = None,
        function_name: str | None = None, _on_created: Callable[[Any], None] | None = None,
    ) -> Any:
        if function_name is not None and not function_name.isidentifier():
            raise ValueError("Python Tool 入口必须是有效的函数名")
        if not isinstance(source, str) or len(source.encode("utf-8")) > MAX_CODE_BYTES:
            raise ValueError("Python 源码不能大于 256 KiB")
        try:
            args_json = json.dumps(arguments, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError):
            raise ValueError("Python Tool 参数必须是有效 JSON") from None
        if len(args_json.encode("utf-8")) > MAX_ARGUMENT_BYTES:
            raise ValueError("Python Tool 参数不能大于 64 KiB")

        settings = SandboxSettings.from_value(self.settings_provider())
        effective_timeout = settings.timeout_seconds if timeout is None else timeout
        if isinstance(effective_timeout, bool) or not isinstance(effective_timeout, int) or not 1 <= effective_timeout <= settings.timeout_seconds:
            raise ValueError("单次执行超时不能超过 CubeSandbox 配置上限")
        sdk = self.sdk or self._load_sdk()
        sandbox = None
        try:
            sdk_config = sdk.Config(
                api_url=settings.api_url,
                api_key=settings.api_key,
                template_id=settings.template_id,
                request_timeout=min(15, settings.timeout_seconds),
            )
            sandbox = sdk.Sandbox.create(
                template=settings.template_id,
                timeout=effective_timeout,
                allow_internet_access=settings.allow_internet_access,
                network={"deny_out": DENIED_EGRESS},
                volume_mounts=None,
                config=sdk_config,
            )
            if _on_created:
                _on_created(sandbox)
            invoke = 'module.run(arguments)' if function_name is None else f'getattr(module, {function_name!r})(**arguments)'
            entrypoint = f'''import asyncio, importlib.util, inspect, json\nspec = importlib.util.spec_from_file_location("sandbox_tool", "/tmp/harness_tool.py")\nmodule = importlib.util.module_from_spec(spec)\nspec.loader.exec_module(module)\nwith open("/tmp/harness_arguments.json", encoding="utf-8") as stream:\n    arguments = json.load(stream)\nresult = {invoke}\nif inspect.isawaitable(result):\n    result = asyncio.run(result)\nprint(json.dumps({{"result": result}}, ensure_ascii=False, allow_nan=False))\n'''
            sandbox.files.write("/tmp/harness_tool.py", source)
            sandbox.files.write("/tmp/harness_arguments.json", args_json)
            sandbox.files.write("/tmp/harness_entry.py", entrypoint)
            result = sandbox.commands.run(
                "set -o pipefail; python /tmp/harness_entry.py 2>/dev/null | head -c 262145",
                timeout=effective_timeout,
            )
            stdout = result.stdout or ""
            stderr = result.stderr or ""
            if len(stdout.encode("utf-8")) > MAX_OUTPUT_BYTES or len(stderr.encode("utf-8")) > MAX_OUTPUT_BYTES:
                raise SandboxUnavailable("CubeSandbox 输出超过 256 KiB 限制")
            if result.exit_code != 0:
                # Avoid reflecting untrusted output (which may contain credentials) into logs or prompts.
                raise SandboxUnavailable("CubeSandbox 中的 Python 执行失败")
            payload = json.loads(stdout.strip().splitlines()[-1])
            return payload.get("result")
        except SandboxUnavailable:
            raise
        except Exception:
            # SDK/API exception text may contain the URL or other credential-bearing details.
            raise SandboxUnavailable("CubeSandbox 沙箱执行失败，未回退到本机") from None
        finally:
            if sandbox is not None:
                try:
                    sandbox.kill()
                except Exception:
                    pass

    def test_connection(self, value: dict[str, Any] | None = None) -> bool:
        try:
            settings = SandboxSettings.from_value(value if value is not None else self.settings_provider())
            sdk = self.sdk or self._load_sdk()
            config = sdk.Config(
                api_url=settings.api_url,
                api_key=settings.api_key,
                template_id=settings.template_id,
                request_timeout=min(15, settings.timeout_seconds),
            )
            sdk.Sandbox.list(config=config)
            return True
        except Exception:
            return False

    @staticmethod
    def _load_sdk():
        try:
            from cubesandbox import Config, Sandbox
        except ImportError:
            raise SandboxUnavailable("未安装 cubesandbox SDK，已拒绝执行") from None
        return type("CubeSandboxSdk", (), {"Config": Config, "Sandbox": Sandbox})
