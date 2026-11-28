"""Run the fixed Dify SDK bridge in disposable local Linux containers."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import uuid

from .package import PackageInfo, extract_package

BASE_IMAGE = 'python:3.12-slim'
IMAGE_PREFIX = 'harness-dify-plugin:'
IMAGE = re.compile(r'harness-dify-plugin:[0-9a-f]{64}\Z')
MAX_OUTPUT = 256 * 1024
MAX_REQUEST = 128 * 1024
WORKER = Path(__file__).with_name('runner')


class DockerError(RuntimeError):
    pass


def _environment(config: str) -> dict[str, str]:
    safe_keys = {'PATH', 'PATHEXT', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'TMPDIR'}
    return {**{key: value for key, value in os.environ.items() if key.upper() in safe_keys}, 'DOCKER_CONFIG': config}


async def _read_output(stream) -> bytes:
    data = bytearray()
    while chunk := await stream.read(8192):
        if len(data) + len(chunk) > MAX_OUTPUT:
            raise DockerError('Dify 工具输出超过大小限制。')
        data.extend(chunk)
    return bytes(data)


class DockerRunner:
    def __init__(self, *, executable: str | None = None):
        self.executable = executable if executable is not None else shutil.which('docker')

    async def _command(self, args: list[str], *, timeout: float, payload: bytes | None = None, capture: bool = False) -> tuple[int, bytes]:
        if not self.executable:
            raise DockerError('未找到本机 Docker CLI。请先安装并启动 Docker Desktop。')
        process = None
        with tempfile.TemporaryDirectory(prefix='harness-dify-docker-') as config:
            try:
                async with asyncio.timeout(timeout):
                    process = await asyncio.create_subprocess_exec(
                        self.executable, '--context', 'default', *args,
                        stdin=asyncio.subprocess.PIPE if payload is not None else asyncio.subprocess.DEVNULL,
                        stdout=asyncio.subprocess.PIPE if capture else asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL, env=_environment(config),
                    )
                    async def send():
                        if payload is not None:
                            try:
                                process.stdin.write(payload)
                                await process.stdin.drain()
                            except (BrokenPipeError, ConnectionResetError):
                                pass
                            finally:
                                process.stdin.close()

                    if capture:
                        tasks = (asyncio.create_task(_read_output(process.stdout)), asyncio.create_task(send()))
                        try:
                            output, _ = await asyncio.gather(*tasks)
                        finally:
                            for task in tasks:
                                task.cancel()
                            await asyncio.gather(*tasks, return_exceptions=True)
                    else:
                        await send()
                        output = b''
                    return await process.wait(), output
            except TimeoutError:
                raise DockerError('Dify 插件容器操作超时。') from None
            except (OSError, NotImplementedError):
                raise DockerError('无法启动本机 Docker。请确认 Docker Desktop 的 Linux 引擎已运行。') from None
            finally:
                if process is not None and process.returncode is None:
                    try:
                        process.kill()
                        async with asyncio.timeout(2):
                            if capture:
                                # Close out the now-terminated child's pipe transport before waiting.
                                await process.stdout.read()
                            await process.wait()
                    except (OSError, TimeoutError):
                        pass

    async def exists(self, image: str) -> bool:
        if image != BASE_IMAGE and not IMAGE.fullmatch(image):
            return False
        try:
            code, _ = await self._command(['image', 'inspect', image], timeout=5)
            return code == 0
        except DockerError:
            return False

    async def build(self, data: bytes, info: PackageInfo) -> str:
        if not await self.exists(BASE_IMAGE):
            raise DockerError('本机 Docker Linux 引擎不可用，或缺少 python:3.12-slim 镜像。请启动 Docker 并准备基础镜像。')
        image = IMAGE_PREFIX + info.digest
        if not IMAGE.fullmatch(image):
            raise DockerError('Dify 插件安装标识无效。')
        with tempfile.TemporaryDirectory(prefix='harness-dify-build-') as folder:
            context = Path(folder)
            await asyncio.to_thread(extract_package, data, context / 'plugin')
            for name in ('Dockerfile', 'worker.py'):
                shutil.copyfile(WORKER / name, context / name)
            dependencies = list(info.dependencies)
            if not any(re.match(r'dify[-_]plugin(?=\W|$)', dependency, flags=re.I) for dependency in dependencies):
                dependencies.append('dify-plugin==0.11.1')
            (context / 'requirements.harness.txt').write_text('\n'.join(dependencies) + '\n', encoding='utf-8')
            code, _ = await self._command(['build', '--pull=false', '--tag', image, folder], timeout=420)
        if code != 0:
            raise DockerError('Dify 插件镜像构建失败。请检查依赖是否提供兼容的 Python 3.12 wheel，以及 Docker 是否能访问 PyPI。')
        return image

    async def remove(self, image: str) -> None:
        if IMAGE.fullmatch(image):
            try:
                await self._command(['image', 'rm', image], timeout=15)
            except DockerError:
                pass

    async def run(self, image: str, request: dict, *, network: bool = False) -> list:
        if not IMAGE.fullmatch(image):
            raise DockerError('Dify 插件运行标识无效。')
        payload = json.dumps(request, ensure_ascii=True, allow_nan=False).encode()
        if len(payload) > MAX_REQUEST:
            raise DockerError('Dify 工具输入超过大小限制。')
        name = 'harness-dify-' + uuid.uuid4().hex
        args = ['run', '--rm', '--name', name, '--pull=never', '-i', '--read-only', '--cap-drop=ALL',
                '--security-opt=no-new-privileges', '--user=65532:65532', '--pids-limit=64', '--memory=512m',
                '--cpus=1', '--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=32m', '--network=' + ('bridge' if network else 'none')]
        try:
            code, output = await self._command([*args, image], timeout=60, payload=payload, capture=True)
        finally:
            try:
                await self._command(['rm', '-f', name], timeout=5)
            except DockerError:
                pass
        if code != 0:
            raise DockerError('无法启动 Dify 插件容器。请确认 Docker 正在运行且插件镜像仍然存在。')
        try:
            response = json.loads(output)
            if not isinstance(response, dict):
                raise ValueError
            messages = response.get('messages')
            if isinstance(messages, list):
                return messages
        except (ValueError, UnicodeError, RecursionError):
            raise DockerError('Dify 工具返回了无效结果。') from None
        errors = {'incompatible': '插件的工具或 SDK 与独立运行器不兼容。',
                  'unsupported': '插件使用了本系统不支持的 Dify 宿主服务、文件或输出类型。',
                  'too_large': 'Dify 工具输出超过大小限制。'}
        error = response.get('error')
        raise DockerError(errors.get(error, 'Dify 工具执行失败。请检查插件设置、凭据及联网授权。') if isinstance(error, str)
                          else 'Dify 工具返回了无效结果。')
