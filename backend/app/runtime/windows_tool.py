"""Run explicitly trusted Python tools through native Windows PowerShell, not a sandbox."""
from __future__ import annotations

import asyncio
import ctypes
import importlib.util
import json
import os
import secrets
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from typing import Any, Literal

from backend.app.runtime.docker_sandbox import MAX_ARGUMENT_BYTES, MAX_CODE_BYTES, MAX_OUTPUT_BYTES


ExecutionMode = Literal['local', 'sandbox']
tool_workspace: ContextVar[str | None] = ContextVar('tool_workspace', default=None)
tool_execution_mode: ContextVar[ExecutionMode | None] = ContextVar('tool_execution_mode', default=None)


@contextmanager
def use_tool_workspace(path: str | None, execution_mode: ExecutionMode | None = None):
    path_token = tool_workspace.set(path)
    mode_token = tool_execution_mode.set(execution_mode)
    try:
        yield
    finally:
        tool_execution_mode.reset(mode_token)
        tool_workspace.reset(path_token)


class WindowsToolError(RuntimeError):
    """A safe-to-display local execution error (never contains child output)."""


WORKER_RESULT_PREFIX = 'HARNESS_TOOL_RESULT:'


def _parse_worker_output(stdout: bytes, token: str, *, allow_legacy: bool = False) -> Any:
    lines = stdout.splitlines()
    marker = f'{WORKER_RESULT_PREFIX}{token}:'.encode('ascii')
    token_error = False
    for line in lines:
        if not line.startswith(marker):
            continue
        try:
            envelope = json.loads(line[len(marker):].decode('utf-8'))
            if isinstance(envelope, dict) and 'result' in envelope:
                return envelope['result']
        except (UnicodeDecodeError, TypeError, ValueError, json.JSONDecodeError):
            token_error = True
    if token_error:
        raise WindowsToolError('Windows 本机工具未返回有效结果')
    if allow_legacy:
        for line in lines:
            try:
                envelope = json.loads(line.decode('utf-8'))
                if isinstance(envelope, dict) and set(envelope) == {'result'}:
                    return envelope['result']
            except (UnicodeDecodeError, TypeError, ValueError, json.JSONDecodeError):
                continue
    raise WindowsToolError('Windows 本机工具未返回有效结果')


def _powershell() -> Path | None:
    if sys.platform != 'win32' or importlib.util.find_spec('win32job') is None:
        return None
    # Resolve from Windows itself, not PATH or an application/user override.
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    buffer = ctypes.create_unicode_buffer(32768)
    length = kernel.GetSystemDirectoryW(buffer, len(buffer))
    if not 0 < length < len(buffer):
        return None
    path = Path(buffer.value) / 'WindowsPowerShell' / 'v1.0' / 'powershell.exe'
    return path if path.is_file() else None


class WindowsToolExecutor:
    def __init__(self, *, timeout: float = 30):
        self.powershell = _powershell()
        self.timeout = timeout

    @property
    def available(self) -> bool:
        return self.powershell is not None

    worker_name = 'windows_tool_worker.py'

    async def execute(self, source: str, arguments: dict[str, Any], *, function_name: str | None = None, cwd: Path | None = None) -> Any:
        if self.powershell is None:
            raise WindowsToolError('Windows PowerShell 本机执行不可用；未切换执行环境')
        if len(source.encode('utf-8')) > MAX_CODE_BYTES:
            raise WindowsToolError('Python Tool 源码超过大小限制')
        result_token = secrets.token_hex(16)
        try:
            arguments_bytes = json.dumps(arguments, ensure_ascii=True, allow_nan=False).encode('ascii')
            payload = json.dumps({'source': source, 'arguments': arguments, 'function_name': function_name,
                                  'directory': str(cwd) if cwd is not None else None,
                                  '_result_token': result_token},
                                 ensure_ascii=True, allow_nan=False).encode('ascii')
        except (ValueError, TypeError):
            raise WindowsToolError('Python Tool 参数不是有效 JSON') from None
        if len(arguments_bytes) > MAX_ARGUMENT_BYTES:
            raise WindowsToolError('Python Tool 参数超过大小限制')
        try:
            async with asyncio.timeout(self.timeout):
                return await self._run(payload, result_token)
        except TimeoutError:
            raise WindowsToolError('Windows 本机工具执行超时，已停止') from None
        except WindowsToolError:
            raise
        except Exception:
            raise WindowsToolError('Windows 本机工具执行失败，请检查工具依赖或系统策略') from None

    def _worker_environment(self) -> dict[str, str]:
        return {}

    def _worker_result(self, stdout: bytes, stderr: bytes, returncode: int, result_token: str | None) -> Any:
        if returncode != 0:
            raise WindowsToolError('Windows 本机工具运行失败，请检查工具源码或依赖')
        return _parse_worker_output(stdout, result_token or '', allow_legacy=self.worker_name != 'windows_tool_worker.py')

    async def _run(self, payload: bytes, result_token: str | None = None) -> Any:
        import win32api
        import win32con
        import win32job

        assert self.powershell is not None
        # -Command is constant; paths are ordinary values, source and arguments go over stdin.
        command = '[Console]::In.ReadToEnd() | & $env:HARNESS_PYTHON -I -B -X utf8 $env:HARNESS_WORKER; exit $LASTEXITCODE'
        env = {name: value for name, value in os.environ.items()
               if name.upper() in {'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'SYSTEMDRIVE', 'COMSPEC', 'PATHEXT'}}
        env.update(HARNESS_PYTHON=sys.executable,
                   HARNESS_WORKER=str(Path(__file__).with_name(self.worker_name).resolve()),
                   PATH=os.pathsep.join([str(Path(sys.base_prefix) / 'Library' / 'bin'),
                                         str(Path(sys.executable).parent), str(self.powershell.parents[2])]))
        env.update(self._worker_environment())
        with TemporaryDirectory(prefix='harness-local-tool-') as working_dir:
            job = win32job.CreateJobObject(None, '')
            process = None
            readers = []
            try:
                limits = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
                limits['BasicLimitInformation']['LimitFlags'] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, limits)
                process = await asyncio.create_subprocess_exec(
                    str(self.powershell), '-NoLogo', '-NoProfile', '-NonInteractive', '-Command', command,
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                    cwd=working_dir, env=env, creationflags=0x08000000,  # CREATE_NO_WINDOW
                )
                handle = win32api.OpenProcess(win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE, False, process.pid)
                try:
                    win32job.AssignProcessToJobObject(job, handle)
                finally:
                    handle.Close()

                async def read(stream):
                    output = bytearray()
                    while chunk := await stream.read(8192):
                        output.extend(chunk)
                        if len(output) > MAX_OUTPUT_BYTES:
                            raise WindowsToolError('Windows 本机工具输出超过大小限制，已停止')
                    return output

                readers = [asyncio.create_task(read(stream)) for stream in (process.stdout, process.stderr)]
                process.stdin.write(payload)
                await process.stdin.drain()
                process.stdin.close()
                stdout, stderr = await asyncio.gather(*readers)
                return self._worker_result(stdout, stderr, await process.wait(), result_token)
            finally:
                job.Close()
                # If assignment failed, no payload was sent. Close stdin to release that child too.
                if process is not None:
                    process.stdin.close()
                    if process.returncode is None:
                        try:
                            process.kill()
                        except ProcessLookupError:
                            pass
                    # Drain after shutdown so asyncio's Windows pipes can finish closing.
                    if readers:
                        await asyncio.gather(*readers, return_exceptions=True)
                    await process.communicate()
