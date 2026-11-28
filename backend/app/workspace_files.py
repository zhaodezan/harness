"""Safe access to files inside a user-selected local workspace."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import tempfile
from typing import Final

from fastapi import HTTPException


EDITABLE_SUFFIXES: Final = frozenset({
    '.txt', '.md', '.rst', '.json', '.yaml', '.yml', '.toml', '.ini', '.csv',
    '.py', '.js', '.jsx', '.ts', '.tsx', '.css', '.html', '.xml', '.sql',
    '.sh', '.ps1', '.bat', '.c', '.h', '.cpp', '.java', '.go', '.rs', '.rb', '.svg',
})
MAX_EDIT_BYTES: Final = 2 * 1024 * 1024
MAX_DIRECTORY_ENTRIES: Final = 1000

_DRIVE_PATH = re.compile(r'^[A-Za-z]:')


def _error(status_code: int, detail: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail=detail)


def _workspace_root(root: Path) -> Path:
    try:
        resolved = root.expanduser().resolve(strict=True)
    except (OSError, RuntimeError):
        raise _error(404, '工作区不可用') from None
    if not resolved.is_dir():
        raise _error(422, '工作区不是目录')
    return resolved


def _parts(relative_path: str, *, allow_root: bool) -> tuple[str, ...]:
    if not isinstance(relative_path, str) or '\x00' in relative_path:
        raise _error(422, '文件路径无效')
    if not relative_path:
        if allow_root:
            return ()
        raise _error(422, '文件路径不能为空')
    if '\\' in relative_path or relative_path.startswith('/') or _DRIVE_PATH.match(relative_path):
        raise _error(422, '文件路径必须是工作区内的相对路径')
    parts = tuple(relative_path.split('/'))
    if any(not part or part in {'.', '..'} for part in parts):
        raise _error(422, '文件路径包含不允许的路径段')
    return parts


def resolve_workspace_file(root: Path, relative_path: str, *, must_exist: bool = True) -> Path:
    """Resolve a relative path while rejecting symlinks and workspace escapes."""
    resolved_root = _workspace_root(root)
    parts = _parts(relative_path, allow_root=False)
    current = resolved_root
    for index, part in enumerate(parts):
        current = current / part
        try:
            if current.is_symlink():
                raise _error(422, '不允许访问符号链接')
            if index < len(parts) - 1 and not current.is_dir():
                raise _error(422, '路径中的目录无效')
            if current.exists() and not current.is_dir() and index < len(parts) - 1:
                raise _error(422, '路径中的目录无效')
        except OSError:
            raise _error(403, '无法访问工作区文件') from None
    if must_exist and not current.exists():
        raise _error(404, '文件不存在')
    try:
        checked = current.resolve(strict=must_exist)
    except FileNotFoundError:
        raise _error(404, '文件不存在') from None
    except (OSError, RuntimeError):
        raise _error(403, '无法访问工作区文件') from None
    try:
        checked.relative_to(resolved_root)
    except ValueError:
        raise _error(422, '文件路径超出工作区范围') from None
    if must_exist and current.is_symlink():
        raise _error(422, '不允许访问符号链接')
    return current


def _require_regular_file(path: Path) -> None:
    try:
        if not path.is_file() or path.is_symlink():
            raise _error(422, '目标不是普通文件')
    except OSError:
        raise _error(403, '无法访问工作区文件') from None


def _require_editable(path: Path) -> None:
    if path.suffix.lower() not in EDITABLE_SUFFIXES:
        raise _error(415, '此文件格式不支持文本编辑')


def _read_bytes(path: Path) -> bytes:
    _require_regular_file(path)
    try:
        size = path.stat().st_size
    except OSError:
        raise _error(403, '无法读取工作区文件') from None
    if size > MAX_EDIT_BYTES:
        raise _error(413, '文本文件过大')
    try:
        return path.read_bytes()
    except OSError:
        raise _error(403, '无法读取工作区文件') from None


def _revision(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def read_workspace_text(root: Path, relative_path: str) -> tuple[str, str]:
    path = resolve_workspace_file(root, relative_path)
    _require_regular_file(path)
    _require_editable(path)
    content = _read_bytes(path)
    try:
        text = content.decode('utf-8')
    except UnicodeDecodeError:
        raise _error(422, '文件不是有效的 UTF-8 文本') from None
    return text, _revision(content)


def write_workspace_text(root: Path, relative_path: str, content: str, expected_revision: str) -> str:
    path = resolve_workspace_file(root, relative_path)
    _require_editable(path)
    _require_regular_file(path)
    if not isinstance(content, str):
        raise _error(422, '文件内容无效')
    encoded = content.encode('utf-8')
    if len(encoded) > MAX_EDIT_BYTES:
        raise _error(413, '文本文件过大')
    current = _read_bytes(path)
    actual_revision = _revision(current)
    if actual_revision != expected_revision:
        raise _error(409, '文件已在外部发生修改，请重新加载后再保存')
    parent = path.parent
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile('wb', dir=parent, prefix=f'.{path.name}.', suffix='.tmp', delete=False) as temporary:
            temporary_name = temporary.name
            temporary.write(encoded)
            temporary.flush()
            os.fsync(temporary.fileno())
        try:
            os.chmod(temporary_name, path.stat().st_mode & 0o777)
        except OSError:
            pass
        os.replace(temporary_name, path)
        temporary_name = None
    except OSError:
        raise _error(403, '无法保存工作区文件') from None
    finally:
        if temporary_name:
            try:
                os.unlink(temporary_name)
            except OSError:
                pass
    return _revision(encoded)


def list_workspace_entries(root: Path, relative_path: str) -> list[dict]:
    resolved_root = _workspace_root(root)
    parts = _parts(relative_path, allow_root=True)
    directory = resolved_root if not parts else resolve_workspace_file(root, relative_path)
    if directory.is_symlink():
        raise _error(422, '不允许访问符号链接')
    if not directory.exists():
        raise _error(404, '目录不存在')
    if not directory.is_dir():
        raise _error(422, '目标不是目录')
    entries: list[dict] = []
    try:
        children = list(directory.iterdir())
    except OSError:
        raise _error(403, '无法读取工作区目录') from None
    for child in children:
        try:
            if child.is_symlink():
                continue
            if not child.is_dir() and not child.is_file():
                continue
            stat = child.stat()
            relative = child.relative_to(resolved_root).as_posix()
            entries.append({
                'name': child.name,
                'path': relative,
                'kind': 'directory' if child.is_dir() else 'file',
                'size': stat.st_size if child.is_file() else None,
                'modified_at': datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
            })
        except OSError:
            continue
    entries.sort(key=lambda item: (item['kind'] != 'directory', item['name'].casefold(), item['name']))
    return entries[:MAX_DIRECTORY_ENTRIES]
