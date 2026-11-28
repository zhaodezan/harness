"""Durable installation catalog and Agent-tool registry bridge."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import threading
import time
import uuid

from backend.app.tools import ToolDefinition, ToolRegistry
from backend.app.secrets import SecretStore

from .docker import DockerError, DockerRunner, IMAGE_PREFIX
from .marketplace import IDENTIFIER, Marketplace
from .package import MAX_PACKAGE_BYTES, PackageError, PackageInfo, inspect_package


class InstallError(RuntimeError):
    pass


SECRET_ID = re.compile(r'[a-z0-9-]{1,64}\Z')
PREVIEW_TTL = 600
MAX_PREVIEWS = 8
MAX_INSTALLED = 100
MAX_CREDENTIAL = 4096


class DifyPluginManager:
    def __init__(self, root: str | Path, registry: ToolRegistry, secrets: SecretStore,
                 runner: DockerRunner, market: Marketplace):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.catalog = self.root / 'catalog.json'
        self.registry, self.secrets, self.runner, self.market = registry, secrets, runner, market
        self._lock = threading.RLock()
        self._previews: dict[str, tuple[bytes, PackageInfo, float, str]] = {}
        self._installed: dict[str, dict] = {}
        self._registered: set[str] = set()
        self._mutation = asyncio.Lock()
        self.catalog_warning: str | None = None

    @staticmethod
    def _public(item: dict) -> dict:
        return {key: value for key, value in item.items() if key != 'image'}

    def list_installed(self) -> list[dict]:
        with self._lock:
            return [self._public(item) for item in sorted(self._installed.values(), key=lambda value: value['id'])]

    def list_tools(self) -> list[dict]:
        return [self._tool_public(tool) for tool in self.registry.list() if (tool.get('plugin_id') or '').startswith('dify:')]

    @staticmethod
    def _tool_public(tool: dict) -> dict:
        return {'id': tool['plugin_id'], 'name': tool['name'], 'description': tool['description'],
                'risk': 'side_effect', 'plugin_id': tool['plugin_id'], 'input_schema': tool['input_schema']}

    async def restore(self) -> None:
        if not self.catalog.is_file():
            return
        try:
            catalog = json.loads(self.catalog.read_text(encoding='utf-8'))
            if not isinstance(catalog, list) or len(catalog) > 1000:
                raise ValueError
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            self.catalog_warning = 'Dify 插件清单无法读取，已暂停本机插件管理。请检查数据目录。'
            return
        for raw in catalog:
            if not isinstance(raw, dict):
                continue
            identity = raw.get('id')
            digest = raw.get('digest')
            if not isinstance(identity, str) or not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
                continue
            folder = (self.root / identity).resolve()
            if folder.parent != self.root or not SECRET_ID.fullmatch(identity):
                continue
            try:
                data = (folder / 'plugin.difypkg').read_bytes()
                info = inspect_package(data, raw['plugin_id'], raw['version'])
                if info.digest != digest:
                    continue
                if IMAGE_PREFIX + digest != raw.get('image'):
                    continue
                tools = raw.get('tools')
                if not isinstance(tools, list) or len(tools) != len(info.tools):
                    continue
                record = dict(raw)
                record['network_enabled'] = raw.get('network_enabled') if type(raw.get('network_enabled')) is bool else False
                saved_fields = raw.get('credentials') or {}
                if not isinstance(saved_fields, dict):
                    continue
                record['credentials'] = {key: {**field, 'configured': isinstance(saved_fields.get(key), dict)
                                                and saved_fields[key].get('configured') is True}
                                         for key, field in info.credentials.items()}
                if raw.get('status') == 'cleanup_pending':
                    record['status'] = 'cleanup_pending'
                else:
                    record['status'] = 'ready' if await self.runner.exists(record['image']) else 'image_missing'
                self._installed[identity] = record
                if record['status'] == 'ready':
                    self._register(record, info)
            except (OSError, PackageError, KeyError, TypeError, ValueError):
                continue

    async def search(self, query: str, page: int) -> dict:
        return await self.market.search(query, page)

    async def prepare(self, value: str) -> dict:
        candidate = value.strip()
        match = IDENTIFIER.fullmatch(candidate)
        if match:
            data = await self.market.download(candidate)
            return self.preview(data, match['id'], match['version'], candidate)
        resolved = await self.market.resolve(candidate)
        data = await self.market.download(resolved['identifier'])
        return self.preview(data, resolved['plugin_id'], resolved['version'], resolved['identifier'])

    def prepare_file(self, data: bytes) -> dict:
        return self.preview(data, None, None, 'local-upload')

    def preview(self, data: bytes, expected_id: str | None = None, expected_version: str | None = None,
                identifier: str = 'local-upload') -> dict:
        if len(data) > MAX_PACKAGE_BYTES:
            raise PackageError('插件包不能大于 16 MB。')
        info = inspect_package(data, expected_id, expected_version)
        if identifier != 'local-upload' and (not isinstance(identifier, str) or len(identifier) > 512):
            raise PackageError('市场插件版本标识无效。')
        with self._lock:
            now = time.monotonic()
            self._previews = {key: value for key, value in self._previews.items() if value[2] > now}
            token = uuid.uuid4().hex + uuid.uuid4().hex
            self._previews[token] = (bytes(data), info, now + PREVIEW_TTL, identifier)
            while len(self._previews) > MAX_PREVIEWS:
                self._previews.pop(next(iter(self._previews)))
        return {'token': token, 'plugin_id': info.plugin_id, 'version': info.version, 'label': info.label,
                'description': info.description, 'digest': info.digest, 'source': 'marketplace' if identifier != 'local-upload' else 'unverified_upload',
                'identifier': identifier if identifier != 'local-upload' else None, 'tools': [
                    {'name': item['name'], 'provider': item['provider'], 'label': item['label'], 'description': item['description'],
                     'parameters': item['schema']['properties']} for item in info.tools],
                'credentials': {name: {'type': value['type'], 'label': value['label'], 'required': value['required']}
                                for name, value in info.credentials.items()},
                'dependencies': list(info.dependencies),
                'warning': '工具代码将在本机容器运行；启用联网后可向插件服务发送工具输入及其凭据。市场未提供可验证的签名。' if identifier != 'local-upload'
                           else '这个包来自本地，发布者身份未经市场验证。工具代码将在本机容器运行。'}

    async def install(self, token: str, *, network_enabled: bool) -> dict:
        if self.catalog_warning:
            raise InstallError(self.catalog_warning)
        if type(network_enabled) is not bool:
            raise InstallError('插件联网授权无效。')
        if self._mutation.locked():
            raise InstallError('另一个 Dify 插件操作正在进行，请稍后重试。')
        async with self._mutation:
            return await self._install(token, network_enabled)

    async def _install(self, token: str, network_enabled: bool) -> dict:
        with self._lock:
            preview = self._previews.pop(token, None)
            if not preview or preview[2] < time.monotonic():
                raise InstallError('安装预览已过期；请重新检查插件包。')
            data, info, _, identifier = preview
            installed_key = info.digest[:32]
            previous = self._installed.get(installed_key)
            if previous and previous.get('status') != 'image_missing':
                raise InstallError('这个插件版本已经安装。')
            if previous is None and len(self._installed) >= MAX_INSTALLED:
                raise InstallError('已达 Dify 插件安装数量上限。')
            for tool in info.tools:
                plugin_id = self._plugin_id(info, installed_key, tool)
                if any(existing['plugin_id'] == plugin_id for existing in self.registry.list()):
                    raise InstallError('这个插件工具名称发生冲突；请先卸载冲突插件。')
        image = None
        folder = self.root / installed_key
        try:
            image = await self.runner.build(data, info)
            # A fake runner and the Docker worker have the same probe contract.
            await self.runner.run(image, {'probe': [{'provider': item['provider'], 'tool': item['name']} for item in info.tools]})
            if not re.fullmatch(r'harness-dify-plugin:[0-9a-f]{64}', image):
                raise InstallError('插件构建产物未通过镜像标识检查。')
            folder.mkdir(mode=0o700, exist_ok=True)
            self._write_private(folder / 'plugin.difypkg', data, replace=True)
            credentials = previous['credentials'] if previous else {name: {**field, 'configured': False} for name, field in info.credentials.items()}
            item = {'id': installed_key, 'plugin_id': info.plugin_id, 'version': info.version, 'label': info.label,
                    'description': info.description, 'digest': info.digest, 'source': 'marketplace' if identifier != 'local-upload' else 'unverified_upload',
                    'network_enabled': network_enabled, 'credentials': credentials, 'image': image,
                    'tools': [{'id': self._plugin_id(info, installed_key, tool), 'name': tool['name'], 'provider': tool['provider'],
                               'label': tool['label'], 'description': tool['description'], 'schema': tool['schema'],
                               'defaults': tool['defaults']} for tool in info.tools], 'status': 'ready'}
            with self._lock:
                if installed_key in self._installed and self._installed[installed_key].get('status') != 'image_missing':
                    raise InstallError('这个插件版本已经安装。')
                self._installed[installed_key] = item
                self._persist()
                self._register(item, info)
            return self._public(item)
        except Exception as error:
            with self._lock:
                if previous:
                    self._installed[installed_key] = previous
                else:
                    self._unregister(installed_key)
                    self._installed.pop(installed_key, None)
                try:
                    self._persist()
                except OSError:
                    pass
            shutil.rmtree(folder, ignore_errors=True)
            if image:
                await self.runner.remove(image)
            if isinstance(error, (InstallError, PackageError, DockerError, FileExistsError)):
                raise InstallError(str(error)) from None
            raise InstallError('插件构建或检查失败。请确认 Docker 已启动且插件依赖可以安装。') from None

    @staticmethod
    def _plugin_id(info: PackageInfo, installed_key: str, tool: dict) -> str:
        return f"dify:{info.plugin_id}:{info.version}:{installed_key}:{tool['provider']}:{tool['name']}"

    @staticmethod
    def _tool_name(tool_id: str) -> str:
        readable = re.sub(r'[^a-zA-Z0-9_-]', '_', tool_id)[:36]
        return f"dify_{readable}_{hashlib.sha256(tool_id.encode()).hexdigest()[:10]}"

    def _register(self, item: dict, info: PackageInfo) -> None:
        if item['status'] != 'ready':
            return
        for tool in item['tools']:
            credential_ref = f"dify:{item['id']}"

            async def invoke(arguments, *, installed_id=item['id'], reference=tool, ref=credential_ref):
                current = self._installed.get(installed_id)
                if not current or current['status'] != 'ready' or reference['id'] not in {value['id'] for value in current['tools']}:
                    raise RuntimeError('Dify 插件已卸载或不可用。')
                credentials = {}
                for key, specification in current['credentials'].items():
                    try:
                        value = self.secrets.get(f'{ref}:{key}') if specification['configured'] else None
                    except Exception:
                        raise RuntimeError('无法读取 Dify 插件的系统凭据。') from None
                    if value is not None:
                        credentials[key] = value
                    if specification.get('required') and not value:
                        raise RuntimeError(f"请先配置插件凭据：{specification['label']}。")
                values = {**reference['defaults'], **arguments}
                messages = await self.runner.run(current['image'], {'provider': reference['provider'],
                    'tool': reference['name'], 'credentials': credentials, 'arguments': values},
                    network=current['network_enabled'])
                return '\n'.join(message['text'] for message in messages if message['type'] == 'text') if all(message['type'] == 'text' for message in messages) else messages

            definition = ToolDefinition(name=self._tool_name(tool['id']), description=tool['description'],
                input_schema=tool['schema'], risk='side_effect', handler=invoke, plugin_id=tool['id'])
            self.registry.register(definition)
            self._registered.add(tool['id'])

    def _unregister(self, installed_id: str) -> None:
        item = self._installed.get(installed_id)
        if item:
            for tool in item['tools']:
                self.registry.unregister(self._tool_name(tool['id']))
                self._registered.discard(tool['id'])

    async def settings(self, installed_id: str, credentials: dict[str, str], network_enabled: bool) -> dict:
        if type(network_enabled) is not bool or not isinstance(credentials, dict) or len(credentials) > 64:
            raise InstallError('插件设置无效。')
        with self._lock:
            item = self._installed.get(installed_id)
            if not item:
                raise FileNotFoundError
            info_data = (self.root / installed_id / 'plugin.difypkg').read_bytes()
            info = inspect_package(info_data, item['plugin_id'], item['version'])
            invalid = set(credentials) - set(info.credentials)
            if invalid or any(not isinstance(value, str) or len(value) > MAX_CREDENTIAL for value in credentials.values()):
                raise InstallError('插件凭据字段无效或超出长度限制。')
            old_values = {}
            try:
                for key, value in credentials.items():
                    if value:
                        old_values[key] = self.secrets.get(f'dify:{installed_id}:{key}')
            except Exception:
                raise InstallError('无法读取系统凭据库。') from None

            changes = {key: {**info.credentials[key], 'configured': True}
                       for key, value in credentials.items() if value}
            updated = {**item, 'credentials': {**item['credentials'], **changes}, 'network_enabled': network_enabled}

            def rollback_secrets() -> bool:
                succeeded = True
                for key, old_value in old_values.items():
                    try:
                        if old_value is None:
                            self.secrets.delete(f'dify:{installed_id}:{key}')
                        else:
                            self.secrets.set(f'dify:{installed_id}:{key}', old_value)
                    except Exception:
                        succeeded = False
                return succeeded

            try:
                for key, value in credentials.items():
                    if value:
                        self.secrets.set(f'dify:{installed_id}:{key}', value)
            except Exception:
                restored = rollback_secrets()
                message = '无法写入系统凭据库。' if restored else '无法写入系统凭据库，且凭据回滚失败。'
                raise InstallError(message) from None
            try:
                self._persist({**self._installed, installed_id: updated})
            except OSError:
                restored = rollback_secrets()
                message = '无法保存插件设置，凭据已回滚。' if restored else '无法保存插件设置，且凭据回滚失败。'
                raise InstallError(message) from None
            self._installed[installed_id] = updated
            return self._public(updated)

    async def uninstall(self, installed_id: str) -> None:
        if self._mutation.locked():
            raise InstallError('另一个 Dify 插件操作正在进行，请稍后重试。')
        async with self._mutation:
            await self._uninstall(installed_id)

    async def _uninstall(self, installed_id: str) -> None:
        with self._lock:
            item = self._installed.get(installed_id)
            if not item:
                raise FileNotFoundError
            if item.get('status') != 'cleanup_pending':
                item = {**item, 'status': 'cleanup_pending'}
                self._unregister(installed_id)
                self._installed[installed_id] = item
                try:
                    self._persist()
                except OSError:
                    self._installed[installed_id] = {**item, 'status': 'ready'}
                    raise InstallError('无法记录卸载状态；未清理插件文件。') from None
        for key in item['credentials']:
            try:
                self.secrets.delete(f"dify:{installed_id}:{key}")
            except Exception:
                raise InstallError('系统凭据库暂时无法清理。插件已停止调用；请重试卸载。') from None
        shutil.rmtree(self.root / installed_id, ignore_errors=True)
        await self.runner.remove(item['image'])
        with self._lock:
            self._installed.pop(installed_id, None)
            self._persist()

    @staticmethod
    def _write_private(path: Path, data: bytes, *, replace: bool = False) -> None:
        if replace:
            path.write_bytes(data)
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
            return
        with path.open('xb') as output:
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
            output.write(data)

    def _persist(self, records: dict[str, dict] | None = None) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        values = list((self._installed if records is None else records).values())
        payload = json.dumps(values, ensure_ascii=False, allow_nan=False).encode('utf-8')
        fd, path = tempfile.mkstemp(prefix='.catalog-', dir=self.root)
        try:
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
            with os.fdopen(fd, 'wb') as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(path, self.catalog)
        except Exception:
            Path(path).unlink(missing_ok=True)
            raise
