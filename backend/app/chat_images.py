"""Private, workspace-scoped raster attachments from local Skill tool results."""
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import stat
from time import time
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from backend.app.db import database_connection


router = APIRouter(prefix='/api/chat-images', tags=['chat images'])
MAX_IMAGES = 4
MAX_BYTES = 16 * 1024 * 1024
DEBUG_TTL = 6 * 60 * 60
_ID = re.compile(r'[0-9a-f]{32}')
_TYPES = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp', '.gif': 'image/gif'}


def _mime(header: bytes) -> str | None:
    if header.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if header.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if header[:4] == b'RIFF' and header[8:12] == b'WEBP':
        return 'image/webp'
    if header.startswith((b'GIF87a', b'GIF89a')):
        return 'image/gif'
    return None


def _folder(state, debug: bool) -> Path:
    return state.data_dir / 'chat-images' / ('debug' if debug else 'saved')


def _safe_text(value: str) -> str:
    return value.encode('utf-8', errors='replace').decode('utf-8')


def _label(value, default: str) -> str:
    return (_safe_text(value.strip()[:160]) or default) if isinstance(value, str) else default


def image_refs(metadata: dict) -> list[dict]:
    images = metadata.get('images')
    if not isinstance(images, list):
        return []
    return [{'id': item['id'], 'label': _label(item.get('label'), '生成图片')}
            for item in images[:MAX_IMAGES] if isinstance(item, dict)
            and isinstance(item.get('id'), str) and _ID.fullmatch(item['id'])]


def public_images(refs: list[dict], *, session_id: str | None = None, message_id: str | None = None) -> list[dict]:
    scope = f'messages/{session_id}/{message_id}' if session_id is not None else 'debug'
    return [{**item, 'url': f"/api/chat-images/{scope}/{item['id']}"} for item in refs]


def _workspace_relative_result(value, root: Path):
    if isinstance(value, str):
        path = Path(value)
        if path.is_absolute():
            if path.is_relative_to(root) and '..' not in path.parts:
                return path.relative_to(root).as_posix()
            return '[工作区外路径已隐藏]'
        if PureWindowsPath(value).is_absolute():
            return '[工作区外路径已隐藏]'
    elif isinstance(value, list):
        return [_workspace_relative_result(item, root) for item in value]
    elif isinstance(value, dict):
        return {key: _workspace_relative_result(item, root) for key, item in value.items()}
    return value


def _workspace_source(path: Path, root: Path) -> Path | None:
    if not path.is_absolute() or not path.is_relative_to(root) or path == root or '..' in path.parts:
        return None
    current = root
    for part in path.relative_to(root).parts:
        current /= part
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode) or (
            getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0)
        ):
            return None
    return current if stat.S_ISREG(info.st_mode) else None


def _matches_open_path(fd: int, source: Path, info) -> bool:
    if os.name == 'nt':
        import msvcrt
        import win32file

        try:
            final = win32file.GetFinalPathNameByHandle(msvcrt.get_osfhandle(fd), 0)
        except win32file.error:
            return False
        prefix = '\\\\?\\'
        if final.startswith(prefix + 'UNC\\'):
            final = '\\\\' + final[len(prefix) + 4:]
        else:
            final = final.removeprefix(prefix)
        return Path(final) == source
    proc_path = Path(f'/proc/self/fd/{fd}')
    if proc_path.is_symlink():
        return proc_path.resolve(strict=True) == source
    return source.resolve(strict=True) == source and os.path.samestat(info, source.stat())


def collect_skill_images(state, result, workspace: dict, *, debug: bool, seen: set[bytes], limit: int) -> tuple[list[dict], object]:
    if workspace['execution_mode'] != 'local' or not workspace.get('path') or not isinstance(result, str):
        return [], result
    try:
        outcome = json.loads(result)
        delivery = outcome.get('delivery') if isinstance(outcome, dict) else None
        if not isinstance(delivery, list):
            return [], result
        root = Path(workspace['path'])
        safe_result = _safe_text(json.dumps(_workspace_relative_result(outcome, root), ensure_ascii=False))
    except (ValueError, RuntimeError):
        return [], result
    try:
        if limit <= 0 or not root.is_absolute() or root.resolve(strict=True) != root:
            return [], safe_result
        folder = _folder(state, debug)
        store = folder.parent.resolve()
    except (OSError, ValueError, RuntimeError):
        return [], safe_result
    refs = []
    for item in delivery[:32]:
        if len(refs) >= limit:
            break
        value = item.get('preview_path') if isinstance(item, dict) else None
        if not isinstance(value, str) or not value or len(value) > 4096:
            continue
        try:
            path = Path(value)
            expected = _TYPES.get(path.suffix.lower())
            if not expected or not path.is_absolute():
                continue
            source = _workspace_source(path, root)
            if source is None or source.is_relative_to(store):
                continue
            flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_NOFOLLOW', 0)
            with os.fdopen(os.open(source, flags), 'rb') as stream:
                info = os.fstat(stream.fileno())
                if (not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_BYTES
                        or not _matches_open_path(stream.fileno(), source, info)):
                    continue
                header = stream.read(12)
                if _mime(header) != expected:
                    continue
                contents = header + stream.read(MAX_BYTES + 1 - len(header))
            if len(contents) > MAX_BYTES:
                continue
            fingerprint = hashlib.sha256(contents).digest()
            if fingerprint in seen:
                continue
            folder.mkdir(parents=True, exist_ok=True)
            image_id = uuid4().hex
            target = folder / image_id
            with target.open('xb') as stream:
                stream.write(contents)
        except (OSError, ValueError, RuntimeError):
            continue
        seen.add(fingerprint)
        refs.append({'id': image_id, 'label': _label(item.get('image_name'), _safe_text(source.name[:160]))})
    return refs, safe_result


def _parse_metadata(raw: str) -> dict:
    try:
        value = json.loads(raw)
    except (TypeError, ValueError, RecursionError):
        return {}
    return value if isinstance(value, dict) else {}


def prune_chat_images(state) -> None:
    now = time()
    debug_dir = _folder(state, True)
    saved_dir = _folder(state, False)
    # Never collect recent snapshots: a chat might still be publishing its metadata.
    try:
        recent_cutoff = now - 24 * 60 * 60
        orphans = [path for path in saved_dir.iterdir() if _ID.fullmatch(path.name)
                   and path.is_file() and path.stat().st_mtime < recent_cutoff] if saved_dir.is_dir() else []
        if orphans:
            with database_connection(state.database_path) as connection:
                referenced = {item['id'] for row in connection.execute(
                    "SELECT metadata_json FROM messages WHERE role='assistant' AND instr(metadata_json, 'images') > 0"
                ) for item in image_refs(_parse_metadata(row['metadata_json']))}
            for path in orphans:
                if path.name not in referenced:
                    path.unlink(missing_ok=True)
        if debug_dir.is_dir():
            for path in debug_dir.iterdir():
                if _ID.fullmatch(path.name) and path.is_file() and path.stat().st_mtime <= now - DEBUG_TTL:
                    path.unlink(missing_ok=True)
    except OSError:
        pass  # An unavailable cache must not stop the app or maintenance.


def _reject_cross_origin(request: Request) -> None:
    if request.headers.get('sec-fetch-site') in {'cross-site', 'same-site'}:
        raise HTTPException(403, '仅允许在本聊天界面查看图片')


def _image_response(request: Request, image_id: str, *, debug: bool):
    folder = _folder(request.app.state, debug)
    path = folder / image_id
    try:
        if (path.is_symlink() or not path.is_file() or
                (debug and path.stat().st_mtime <= time() - DEBUG_TTL)):
            raise FileNotFoundError
        with path.open('rb') as stream:
            media_type = _mime(stream.read(12))
    except OSError:
        media_type = None
    if not media_type:
        raise HTTPException(404, '图片不可用')
    return FileResponse(path, media_type=media_type, headers={
        'Cache-Control': 'private, no-store',
        'X-Content-Type-Options': 'nosniff',
        'Cross-Origin-Resource-Policy': 'same-origin',
        'Content-Security-Policy': "default-src 'none'; sandbox",
    })


@router.get('/debug/{image_id}')
def debug_image(image_id: str, request: Request):
    _reject_cross_origin(request)
    if not _ID.fullmatch(image_id):
        raise HTTPException(404, '图片不可用')
    return _image_response(request, image_id, debug=True)


@router.get('/messages/{session_id}/{message_id}/{image_id}')
def message_image(session_id: str, message_id: str, image_id: str, request: Request):
    _reject_cross_origin(request)
    if not _ID.fullmatch(image_id):
        raise HTTPException(404, '图片不可用')
    with database_connection(request.app.state.database_path) as connection:
        row = connection.execute(
            "SELECT metadata_json FROM messages WHERE id=? AND session_id=? AND role='assistant'", (message_id, session_id),
        ).fetchone()
    if row is None or not any(item['id'] == image_id for item in image_refs(_parse_metadata(row['metadata_json']))):
        raise HTTPException(404, '图片不可用')
    return _image_response(request, image_id, debug=False)
