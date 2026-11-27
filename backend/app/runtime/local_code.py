"""Run user-approved generated Python in a local working directory, not a sandbox."""
import asyncio
import json
import os
import secrets
from pathlib import Path
import signal
import sys

from backend.app.runtime.docker_sandbox import MAX_OUTPUT_BYTES
from backend.app.runtime.windows_tool import (
    WindowsToolError, WindowsToolExecutor, _parse_worker_output,
)


def _parse_posix_output(stdout: bytes, token: str | None = None):
    return _parse_worker_output(stdout, token or '', allow_legacy=True)


class LocalCodeExecutor(WindowsToolExecutor):
    worker_name = 'local_code_worker.py'

    async def execute(self, source: str, arguments: dict, *, cwd: Path):
        from backend.app.runtime.sandbox_tools import MAX_CODE_ARGUMENT_BYTES, MAX_GENERATED_CODE_CHARS
        try:
            encoded = json.dumps(arguments, allow_nan=False)
            if len(encoded.encode('utf-8')) > MAX_CODE_ARGUMENT_BYTES or len(source) > MAX_GENERATED_CODE_CHARS:
                raise WindowsToolError('Python 源码或参数超过大小限制')
        except (TypeError, ValueError):
            raise WindowsToolError('Python 参数不是有效 JSON') from None
        if os.name == 'nt':
            return await super().execute(source, arguments, cwd=cwd)
        result_token = secrets.token_hex(16)
        try:
            payload = json.dumps({'source': source, 'arguments': arguments, 'directory': str(cwd),
                                  '_result_token': result_token}, allow_nan=False).encode()
            async with asyncio.timeout(self.timeout):
                return await self._run_posix(payload, cwd, result_token)
        except TimeoutError:
            raise WindowsToolError('本机 Python 执行超时，已停止') from None
        except WindowsToolError:
            raise
        except Exception:
            raise WindowsToolError('本机 Python 执行失败，请检查源码、目录或依赖') from None

    async def _run_posix(self, payload: bytes, cwd: Path, result_token: str | None = None):
        process = await asyncio.create_subprocess_exec(
            sys.executable, '-I', '-B', '-X', 'utf8', str(Path(__file__).with_name(self.worker_name).resolve()),
            cwd=cwd, env={key: os.environ[key] for key in ('PATH', 'TMPDIR', 'LANG') if key in os.environ},
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )

        async def read(stream):
            output = bytearray()
            while chunk := await stream.read(8192):
                output.extend(chunk)
                if len(output) > MAX_OUTPUT_BYTES:
                    raise WindowsToolError('本机 Python 输出超过大小限制，已停止')
            return output

        readers = [asyncio.create_task(read(stream)) for stream in (process.stdout, process.stderr)]
        try:
            process.stdin.write(payload)
            await process.stdin.drain()
            process.stdin.close()
            stdout, _ = await asyncio.gather(*readers)
            if await process.wait() != 0:
                raise WindowsToolError('本机 Python 执行失败，请检查源码、目录或依赖')
            return _parse_posix_output(stdout, result_token)
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.stdin.close()
            await asyncio.gather(*readers, return_exceptions=True)
            await process.communicate()
