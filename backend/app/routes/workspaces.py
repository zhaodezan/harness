"""Local directory choices made by the user, never by model tool arguments."""
import asyncio
from datetime import datetime
import inspect
import os
from pathlib import Path
import re
import sys
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from backend.app.db import database_connection

router = APIRouter(prefix='/api', tags=['workspaces'])
ExecutionMode = Literal['local', 'sandbox']
SANDBOX_UNAVAILABLE_MESSAGE = (
    '本机 Docker 沙箱不可用；请安装并启动 Docker Desktop，并确认配置的本地镜像已存在。'
    '应用不会自动拉取镜像，也不会回退到本机执行。'
)


class GroupCreate(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    path: str | None = Field(default=None, max_length=4096)


class WorkspaceChoice(BaseModel):
    group_id: str | None = Field(default=None, min_length=32, max_length=32)
    execution_mode: ExecutionMode = 'local'


def _root(request: Request) -> Path:
    return request.app.state.data_dir.resolve() / 'workspaces'


def _directory(path: Path) -> Path:
    try:
        resolved = path.resolve(strict=True)
        if path.is_absolute() and resolved.is_dir():
            return resolved
    except (OSError, ValueError, RuntimeError):
        pass
    raise HTTPException(422, '请选择存在的本机绝对目录')


def _managed(root: Path, name: str) -> Path:
    try:
        root.mkdir(parents=True, exist_ok=True)
        path = root / name
        path.mkdir()
        return path
    except OSError:
        raise HTTPException(409, '无法创建工作目录，请检查目录权限后重试') from None


def default_directory(request: Request, session_id: str, created_at: str | None = None) -> Path:
    timestamp = datetime.fromisoformat(created_at) if created_at else datetime.now()
    root = _root(request)
    path = root / f'{timestamp:%Y%m%d-%H%M%S}-{session_id}'
    try:
        root.mkdir(parents=True, exist_ok=True)
        path.mkdir(exist_ok=True)
        if not path.is_dir() or path.resolve(strict=True).parent != root.resolve():
            raise OSError
        return path.resolve(strict=True)
    except OSError:
        raise HTTPException(409, '无法创建默认工作目录，请检查目录权限后重试') from None


def group_by_id(connection, group_id: str | None):
    if group_id is None:
        return None
    row = connection.execute('SELECT id, name, path FROM workspace_groups WHERE id=?', (group_id,)).fetchone()
    if row is None:
        raise HTTPException(404, '分组不存在')
    return dict(row)


def workspace_path(value: str) -> Path:
    path = Path(value)
    resolved = _directory(path)
    if resolved != path:
        raise HTTPException(409, '工作目录已变更，请重新选择')
    return resolved


@router.get('/workspaces/pick-directory')
async def pick_directory():
    if sys.platform != 'win32':
        raise HTTPException(501, '系统目录选择器目前仅支持 Windows；请直接输入本机绝对路径。')

    def show_dialog():
        try:
            import tkinter
            from tkinter import filedialog
            root = tkinter.Tk()
            root.withdraw()
            root.attributes('-topmost', True)
            try:
                return filedialog.askdirectory(title='选择本机工作目录', mustexist=True) or None
            finally:
                root.destroy()
        except Exception:
            return None

    result = await asyncio.to_thread(show_dialog)
    if not result:
        raise HTTPException(503, '无法打开系统目录选择器；请直接输入本机绝对路径。')
    return {'path': str(_directory(Path(result)))}


@router.get('/workspaces')
def list_groups(request: Request):
    with database_connection(request.app.state.database_path) as connection:
        return [dict(row) for row in connection.execute('SELECT id, name, path FROM workspace_groups ORDER BY name, id')]


@router.post('/workspaces', status_code=201)
def create_group(payload: GroupCreate, request: Request):
    if (payload.path is None) == (payload.name is None):
        raise HTTPException(422, '请提供分组名称或本机目录，两者选其一')
    group_id = uuid4().hex
    with database_connection(request.app.state.database_path) as connection:
        connection.execute('BEGIN IMMEDIATE')
        if payload.path is not None:
            path = _directory(Path(payload.path))
            name = path.name or path.anchor
            existing = connection.execute('SELECT id, name, path FROM workspace_groups WHERE path=?', (str(path),)).fetchone()
            if existing:
                return dict(existing)
        else:
            name = (payload.name or '').strip()
            if not name:
                raise HTTPException(422, '分组名称不能为空')
            label = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name).strip('. ')[:60] or 'group'
            root = _root(request)
            if re.fullmatch(r'(?i:con|prn|aux|nul|com[0-9¹²³]|lpt[0-9¹²³])', label.split('.')[0].rstrip()):
                label = f'_{label}'
            if os.path.lexists(root / label):
                label = f'{label}-{group_id[:8]}'
            path = _managed(root, label)
        connection.execute('INSERT INTO workspace_groups(id,name,path) VALUES(?,?,?)', (group_id, name, str(path)))
    return {'id': group_id, 'name': name, 'path': str(path)}


def session_lock(request: Request, session_id: str) -> asyncio.Lock:
    """Return the per-session lock shared by run admission and workspace changes."""
    locks = getattr(request.app.state, 'session_locks', None)
    if locks is None:
        locks = {}
        request.app.state.session_locks = locks
    return locks.setdefault(session_id, asyncio.Lock())


def _sandbox_checker(request: Request):
    executor = getattr(getattr(request.app, 'state', None), 'sandbox_executor', None)
    return executor, getattr(executor, 'is_configured', None)


def _sync_checker_is_subclass_override(executor) -> bool:
    """Detect a subclass sync override paired with an inherited async checker."""
    if 'is_configured' in getattr(executor, '__dict__', {}):
        return True
    method_resolution = type(executor).__mro__
    async_owner = next(
        (base for base in method_resolution if 'is_configured_async' in base.__dict__),
        None,
    )
    sync_owner = next(
        (base for base in method_resolution if 'is_configured' in base.__dict__),
        None,
    )
    if async_owner is None or sync_owner is None:
        return False
    return method_resolution.index(sync_owner) < method_resolution.index(async_owner)


async def validate_execution_mode(request: Request, mode: ExecutionMode) -> None:
    """Probe sandbox availability without blocking the event loop or DB locks."""
    if mode != 'sandbox':
        return
    executor, checker = _sandbox_checker(request)
    async_checker = getattr(executor, 'is_configured_async', None)
    sync_checker_overridden = _sync_checker_is_subclass_override(executor)
    try:
        if callable(async_checker) and not sync_checker_overridden:
            configured = async_checker()
            if inspect.isawaitable(configured):
                configured = await configured
        elif callable(checker):
            configured = await asyncio.to_thread(checker)
            if inspect.isawaitable(configured):
                configured = await configured
        else:
            configured = False
        configured = bool(configured)
    except Exception:
        configured = False
    if not configured:
        raise HTTPException(status_code=409, detail=SANDBOX_UNAVAILABLE_MESSAGE)


def session_execution_mode(request: Request, session_id: str) -> ExecutionMode:
    """Read the persisted mode without holding a write transaction."""
    with database_connection(request.app.state.database_path) as connection:
        row = connection.execute(
            'SELECT execution_mode FROM sessions WHERE id=?', (session_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(404, '会话不存在')
    return row['execution_mode'] or 'local'


def session_workspace(
    connection,
    request: Request,
    session_id: str,
    choice: WorkspaceChoice | None = None,
    *,
    sandbox_validated_mode: ExecutionMode | None = None,
):
    row = connection.execute(
        'SELECT s.workspace_group_id, s.default_workspace, s.execution_mode, s.created_at '
        'FROM sessions s WHERE s.id=?', (session_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(404, '会话不存在')
    mode = choice.execution_mode if choice else (row['execution_mode'] or 'local')
    if mode == 'sandbox' and sandbox_validated_mode != mode:
        detail = (
            '工作区执行方式已变更，请重试'
            if sandbox_validated_mode is not None else SANDBOX_UNAVAILABLE_MESSAGE
        )
        raise HTTPException(status_code=409, detail=detail)
    group = group_by_id(connection, choice.group_id if choice else row['workspace_group_id'])
    default = row['default_workspace']
    if group is None and mode == 'sandbox':
        if choice is not None:
            default = None
        path = None
    else:
        if group is None and default is None:
            default = str(default_directory(request, session_id, row['created_at']))
        path = workspace_path(group['path']) if group else workspace_path(default)
    if choice:
        connection.execute('UPDATE sessions SET workspace_group_id=?, default_workspace=?, execution_mode=? WHERE id=?',
                           (choice.group_id, default, mode, session_id))
    elif default != row['default_workspace'] or row['execution_mode'] != mode:
        connection.execute(
            'UPDATE sessions SET default_workspace=?, execution_mode=? WHERE id=?',
            (default, mode, session_id),
        )
    return {'group_id': group['id'] if group else None, 'name': group['name'] if group else (Path(path).name if path else None),
            'path': str(path) if path is not None else None, 'execution_mode': mode}


def debug_workspace(
    connection,
    request: Request,
    choice: WorkspaceChoice | None,
    *,
    sandbox_default: bool = False,
    sandbox_validated_mode: ExecutionMode | None = None,
):
    mode = choice.execution_mode if choice else 'local'
    if mode == 'sandbox' and sandbox_validated_mode != mode:
        raise HTTPException(status_code=409, detail=SANDBOX_UNAVAILABLE_MESSAGE)
    group = group_by_id(connection, choice.group_id) if choice else None
    if group is None and mode == 'sandbox':
        return {'path': None, 'execution_mode': mode}
    path = workspace_path(group['path']) if group else str(default_directory(request, uuid4().hex))
    return {'path': str(path), 'execution_mode': mode}


@router.get('/sessions/{session_id}/workspace')
async def get_session_workspace(session_id: str, request: Request):
    mode = session_execution_mode(request, session_id)
    await validate_execution_mode(request, mode)
    with database_connection(request.app.state.database_path) as connection:
        connection.execute('BEGIN IMMEDIATE')
        return session_workspace(
            connection, request, session_id,
            sandbox_validated_mode=mode,
        )


@router.put('/sessions/{session_id}/workspace')
async def set_session_workspace(session_id: str, payload: WorkspaceChoice, request: Request):
    if session_id in request.app.state.active_sessions:
        raise HTTPException(409, '请先停止当前运行，再切换工作目录或执行方式')
    await validate_execution_mode(request, payload.execution_mode)
    async with session_lock(request, session_id):
        if session_id in request.app.state.active_sessions:
            raise HTTPException(409, '请先停止当前运行，再切换工作目录或执行方式')
        with database_connection(request.app.state.database_path) as connection:
            connection.execute('BEGIN IMMEDIATE')
            return session_workspace(
                connection, request, session_id, payload,
                sandbox_validated_mode=payload.execution_mode,
            )
