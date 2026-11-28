"""Validate an untrusted .difypkg as data only; never import it on the host."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import stat
import zipfile

from packaging.requirements import InvalidRequirement, Requirement
import yaml

from .marketplace import MAX_PACKAGE_BYTES, PLUGIN_ID, VERSION, localized

MAX_EXPANDED = 64 * 1024 * 1024
MAX_MANIFEST = 512 * 1024
MAX_FILES = 1024
MAX_TOOLS = 64
SUPPORTED_DIFY_SDK = {'0.10.2', '0.11.1'}
NAME = re.compile(r'[A-Za-z_][A-Za-z0-9_-]{0,63}\Z')
MEMBER = re.compile(r'[A-Za-z0-9_.@+\- ]{1,128}\Z')
DOS_DEVICE = re.compile(r'(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?\Z', re.I)


class PackageError(ValueError):
    pass


@dataclass(frozen=True)
class PackageInfo:
    plugin_id: str
    version: str
    label: str
    description: str
    digest: str
    tools: list[dict]
    credentials: dict[str, dict]
    dependencies: list[str]


def _path(name: str) -> str:
    parts = name.split('/')
    if len(name) > 240 or any(not MEMBER.fullmatch(part) or part in {'.', '..'} or part.endswith(('.', ' ')) or DOS_DEVICE.fullmatch(part) for part in parts):
        raise PackageError('插件包包含不安全的文件路径。')
    return name


def _members(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    result = {}
    paths: dict[str, str] = {}
    seen: set[str] = set()
    expanded = 0
    if len(archive.infolist()) > MAX_FILES:
        raise PackageError('插件包文件数量超过限制。')
    for member in archive.infolist():
        name = _path(member.orig_filename.removesuffix('/') if member.is_dir() else member.orig_filename)
        normalized = name.casefold()
        mode = stat.S_IFMT(member.external_attr >> 16)
        if normalized in seen or mode not in (0, stat.S_IFDIR if member.is_dir() else stat.S_IFREG) or member.flag_bits & 1:
            raise PackageError('插件包包含重复、链接或加密文件。')
        seen.add(normalized)
        path = PurePosixPath(name)
        for partial in (path, *path.parents):
            spelling = str(partial)
            if paths.setdefault(spelling.casefold(), spelling) != spelling:
                raise PackageError('插件包文件路径大小写冲突。')
        expanded += member.file_size
        if expanded > MAX_EXPANDED or member.is_dir() and member.file_size:
            raise PackageError('插件包解压后超过大小限制。')
        if not member.is_dir():
            result[name] = member
    for name in result:
        if any(str(parent) in result for parent in PurePosixPath(name).parents):
            raise PackageError('插件包的文件和目录路径冲突。')
    return result


class _PackageLoader(yaml.SafeLoader):
    def compose_node(self, parent, index):
        if self.check_event(yaml.AliasEvent):
            raise PackageError('插件声明不能使用 YAML 别名。')
        return super().compose_node(parent, index)

    def construct_mapping(self, node, deep=False):
        mapping = super().construct_mapping(node, deep=deep)
        if len(mapping) != len(node.value):
            raise PackageError('插件声明包含重复字段。')
        return mapping


def _read_yaml(archive: zipfile.ZipFile, files: dict, name: str) -> dict:
    name = _path(name)
    if not name.endswith(('.yaml', '.yml')) or name not in files or files[name].file_size > MAX_MANIFEST:
        raise PackageError('插件缺少有效的 YAML 声明。')
    content = yaml.load(archive.read(files[name]), Loader=_PackageLoader)
    if not isinstance(content, dict):
        raise PackageError('插件 YAML 声明必须是对象。')
    return content


def _dict(value) -> dict:
    if not isinstance(value, dict):
        raise PackageError('插件声明字段格式无效。')
    return value


def _list(value) -> list:
    if not isinstance(value, list) or len(value) > MAX_TOOLS:
        raise PackageError('插件声明列表无效或超过数量限制。')
    return value


def _name(value) -> str:
    if not isinstance(value, str) or not NAME.fullmatch(value):
        raise PackageError('工具、参数或凭据声明名称无效。')
    return value


def _source(declaration: dict, files: dict) -> None:
    value = _dict(_dict(declaration.get('extra')).get('python')).get('source')
    if not isinstance(value, str) or _path(value) not in files or not value.endswith('.py'):
        raise PackageError('插件缺少声明的 Python 工具源码。')


def _dependencies(archive: zipfile.ZipFile, files: dict) -> list[str]:
    if 'requirements.txt' not in files:
        return []
    if files['requirements.txt'].file_size > 16 * 1024:
        raise PackageError('插件依赖声明过大。')
    result = []
    for line in archive.read(files['requirements.txt']).decode('utf-8-sig').splitlines():
        line = line.split('#', 1)[0].strip()
        if not line:
            continue
        try:
            requirement = Requirement(line)
            normalized_name = re.sub(r'[-_.]+', '-', requirement.name).lower()
            if requirement.url is not None:
                raise InvalidRequirement(line)
            if requirement.marker is not None:
                # Docker evaluates safe PEP 508 markers in its Linux CPython environment.
                str(requirement.marker)
            if normalized_name == 'dify-plugin':
                versions = {str(spec.version) for spec in requirement.specifier if spec.operator in ('==', '===')}
                if requirement.url is not None or versions.isdisjoint(SUPPORTED_DIFY_SDK):
                    raise PackageError('插件依赖的 Dify SDK 版本不受独立运行器支持。')
                result.append(str(requirement))
                continue
            if normalized_name == 'pip' or normalized_name == 'setuptools':
                raise PackageError('插件不得覆盖运行器的安装工具。')
            if len(requirement.specifier) == 0 or not any(spec.operator in ('==', '===') for spec in requirement.specifier):
                raise PackageError('插件运行时依赖必须固定到精确版本。')
        except InvalidRequirement:
            raise PackageError('插件依赖必须来自标准 Python 注册表，不支持 URL、路径或额外选项。') from None
        result.append(str(requirement))
    if len(result) > 128:
        raise PackageError('插件依赖数量超过限制。')
    return result


def _parameters(values: list) -> tuple[dict, dict]:
    properties, defaults, required = {}, {}, []
    for raw in values:
        item = _dict(raw)
        name = _name(item.get('name'))
        kind = item.get('type')
        if name in properties or item.get('form', 'llm') not in ('llm', 'form'):
            raise PackageError('插件包含重复或动态工具参数。')
        schema_type = {'string': 'string', 'select': 'string', 'boolean': 'boolean', 'number': 'number', 'integer': 'integer'}.get(kind) if isinstance(kind, str) else None
        if schema_type is None:
            raise PackageError('插件包含本系统不支持的参数类型。')
        schema = {'type': schema_type, 'description': localized(item.get('llm_description')) or localized(item.get('human_description'))}
        if kind in ('number', 'integer'):
            for source_key, target_key in (('min', 'minimum'), ('max', 'maximum')):
                limit = item.get(source_key)
                if limit is not None:
                    if isinstance(limit, bool) or not isinstance(limit, (int, float)) or kind == 'integer' and not isinstance(limit, int):
                        raise PackageError('工具参数数值限制无效。')
                    schema[target_key] = limit
            if 'minimum' in schema and 'maximum' in schema and schema['minimum'] > schema['maximum']:
                raise PackageError('工具参数数值范围无效。')
        if kind == 'select':
            if item.get('multiple') or item.get('multi_select'):
                raise PackageError('暂不支持工具的多选参数。')
            choices = [_dict(option).get('value') for option in _list(item.get('options'))]
            if not choices or any(not isinstance(value, str) for value in choices):
                raise PackageError('工具的选择参数必须提供静态文本选项。')
            schema['enum'] = choices
        if item.get('default') is not None:
            default = item['default']
            if not isinstance(default, (str, float, int, bool)):
                raise PackageError('工具参数默认值不受支持。')
            json.dumps(default, allow_nan=False)
            defaults[name] = default
        elif item.get('required') is True:
            required.append(name)
        properties[name] = schema
    return {'type': 'object', 'properties': properties, 'required': required, 'additionalProperties': False}, defaults


def inspect_package(data: bytes, expected_id: str | None = None, expected_version: str | None = None) -> PackageInfo:
    if len(data) > MAX_PACKAGE_BYTES:
        raise PackageError('插件包超过大小限制。')
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            files = _members(archive)
            manifest = _read_yaml(archive, files, 'manifest.yaml')
            identity = f"{manifest.get('author')}/{manifest.get('name')}"
            version = manifest.get('version')
            if not PLUGIN_ID.fullmatch(identity) or not isinstance(version, str) or not re.fullmatch(VERSION, version):
                raise PackageError('插件身份或版本无效。')
            if (expected_id is not None and identity != expected_id) or (expected_version is not None and version != expected_version):
                raise PackageError('插件包身份或版本与市场记录不一致。')
            plugins = _dict(manifest.get('plugins'))
            runner = _dict(_dict(manifest.get('meta')).get('runner'))
            if manifest.get('type') not in (None, 'plugin') or not plugins.get('tools') or any(key != 'tools' and values for key, values in plugins.items()) or runner.get('language') != 'python' or str(runner.get('version')) != '3.12':
                raise PackageError('仅支持 Python 3.12 的独立 Python 工具插件。')
            if f"{_name(runner.get('entrypoint'))}.py" not in files:
                raise PackageError('插件缺少 Python 入口。')
            tools, credentials, seen = [], {}, set()
            provider_paths = _list(plugins['tools'])
            if len(provider_paths) != 1 or not isinstance(provider_paths[0], str):
                raise PackageError('第一版仅支持单供应商工具插件。')
            provider = _read_yaml(archive, files, provider_paths[0])
            provider_name = _name(_dict(provider.get('identity')).get('name'))
            _source(provider, files)
            if provider.get('oauth_schema') or provider.get('oauth'):
                raise PackageError('本系统不支持依赖 Dify OAuth 的插件。')
            for key, raw in _dict(provider.get('credentials_for_provider') or {}).items():
                key, credential = _name(key), _dict(raw)
                if credential.get('type') not in ('secret-input', 'text-input'):
                    raise PackageError('插件包含本系统不支持的凭据类型。')
                credentials[key] = {'type': credential['type'], 'label': localized(credential.get('label'), key), 'required': credential.get('required') is True}
            for tool_path in _list(provider.get('tools')):
                if not isinstance(tool_path, str):
                    raise PackageError('工具声明路径无效。')
                tool = _read_yaml(archive, files, tool_path)
                _source(tool, files)
                tool_identity = _dict(tool.get('identity'))
                name = _name(tool_identity.get('name'))
                if name in seen or tool.get('has_runtime_parameters'):
                    raise PackageError('插件包含重复或动态工具。')
                seen.add(name)
                description = _dict(tool.get('description') or {})
                schema, defaults = _parameters(_list(tool.get('parameters', [])))
                tools.append({'name': name, 'provider': provider_name, 'label': localized(tool_identity.get('label'), name),
                              'description': localized(description.get('llm')) or localized(description.get('human'), name),
                              'schema': schema, 'defaults': defaults})
            if not tools:
                raise PackageError('插件没有可安装的工具。')
            return PackageInfo(identity, version, localized(manifest.get('label'), identity), localized(manifest.get('description')),
                               hashlib.sha256(data).hexdigest(), tools, credentials, _dependencies(archive, files))
    except PackageError:
        raise
    except (zipfile.BadZipFile, yaml.YAMLError, OSError, RuntimeError, UnicodeError, ValueError, TypeError, NotImplementedError):
        raise PackageError('插件包损坏，或包含无法解析的声明。') from None


def extract_package(data: bytes, destination: Path) -> None:
    """Call with inspected bytes and a fresh destination inside a private build context."""
    destination.mkdir()
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for name, member in _members(archive).items():
                target = destination / name
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source, target.open('xb') as sink:
                    remaining = member.file_size
                    while chunk := source.read(65536):
                        remaining -= len(chunk)
                        if remaining < 0:
                            raise PackageError('插件包展开大小与声明不一致。')
                        sink.write(chunk)
                    if remaining != 0:
                        raise PackageError('插件包展开大小与声明不一致。')
    except (zipfile.BadZipFile, OSError, RuntimeError, NotImplementedError):
        raise PackageError('无法安全展开插件包。') from None
