"""Run explicitly trusted Python tools through native Windows PowerShell, not a sandbox."""
from __future__ import annotations

import asyncio
import ctypes
import importlib.util
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from typing import Any

from backend.app.runtime.cube_sandbox import MAX_ARGUMENT_BYTES, MAX_CODE_BYTES, MAX_OUTPUT_BYTES


class WindowsToolError(RuntimeError):
    """A safe-to-display local execution error (never contains child output)."""


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

    async def execute(self, source: str, arguments: dict[str, Any], *, function_name: str | None = None) -> Any:
        if self.powershell is None:
            raise WindowsToolError('Windows PowerShell 本机执行不可用；未切换执行环境')
        if len(source.encode('utf-8')) > MAX_CODE_BYTES:
            raise WindowsToolError('Python Tool 源码超过大小限制')
        try:
            arguments_bytes = json.dumps(arguments, ensure_ascii=True, allow_nan=False).encode('ascii')
            payload = json.dumps({'source': source, 'arguments': arguments, 'function_name': function_name},
                                 ensure_ascii=True, allow_nan=False).encode('ascii')
        except (ValueError, TypeError):
            raise WindowsToolError('Python Tool 参数不是有效 JSON') from None
        if len(arguments_bytes) > MAX_ARGUMENT_BYTES:
            raise WindowsToolError('Python Tool 参数超过大小限制')
        try:
            async with asyncio.timeout(self.timeout):
                return await self._run(payload)
        except TimeoutError:
            raise WindowsToolError('Windows 本机工具执行超时，已停止') from None
        except WindowsToolError:
            raise
        except Exception:
            raise WindowsToolError('Windows 本机工具执行失败，请检查工具依赖或系统策略') from None

    async def _run(self, payload: bytes) -> Any:
        import win32api
        import win32con
        import win32job

        assert self.powershell is not None
        # -Command is constant; paths are ordinary values, source and arguments go over stdin.
        command = '[Console]::In.ReadToEnd() | & $env:HARNESS_PYTHON -I -B -X utf8 $env:HARNESS_WORKER; exit $LASTEXITCODE'
        env = {name: value for name, value in os.environ.items()
               if name.upper() in {'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'SYSTEMDRIVE', 'COMSPEC', 'PATHEXT'}}
        env.update(HARNESS_PYTHON=sys.executable,
                   HARNESS_WORKER=str(Path(__file__).with_name('windows_tool_worker.py').resolve()),
                   PATH=os.pathsep.join([str(Path(sys.base_prefix) / 'Library' / 'bin'),
                                         str(Path(sys.executable).parent), str(self.powershell.parents[2])]))
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
                stdout, _ = await asyncio.gather(*readers)
                if await process.wait() != 0:
                    raise WindowsToolError('Windows 本机工具运行失败，请检查工具源码或依赖')
                return json.loads(stdout)['result']
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
