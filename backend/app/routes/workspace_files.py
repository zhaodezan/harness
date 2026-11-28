from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from backend.app.db import database_connection
from backend.app.routes.workspaces import session_workspace
from backend.app.workspace_files import (
    MAX_EDIT_BYTES,
    list_workspace_entries,
    read_workspace_text,
    resolve_workspace_file,
    write_workspace_text,
)

router = APIRouter(prefix='/api', tags=['workspace-files'])

PREVIEW_MEDIA_TYPES: dict[str, str] = {
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.gif': 'image/gif',
    '.webp': 'image/webp',
    '.pdf': 'application/pdf',
    '.mp3': 'audio/mpeg',
    '.wav': 'audio/wav',
    '.mp4': 'video/mp4',
    '.webm': 'video/webm',
}
MAX_PREVIEW_BYTES = 20 * 1024 * 1024


class WorkspaceTextUpdate(BaseModel):
    path: str = Field(min_length=1, max_length=4096)
    content: str = Field(max_length=MAX_EDIT_BYTES)
    expected_revision: str = Field(min_length=64, max_length=64, pattern=r'^[0-9a-f]{64}$')


def _workspace_root(request: Request, session_id: str) -> Path:
    with database_connection(request.app.state.database_path) as connection:
        row = connection.execute(
            'SELECT execution_mode FROM sessions WHERE id=?', (session_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(404, '会话不存在')
        if (row['execution_mode'] or 'local') != 'local':
            raise HTTPException(409, '沙箱会话没有可浏览的本机工作区')
        connection.execute('BEGIN IMMEDIATE')
        workspace = session_workspace(
            connection, request, session_id,
            sandbox_validated_mode='local',
        )
    path = workspace.get('path')
    if not path:
        raise HTTPException(409, '当前会话没有可浏览的工作区')
    return Path(path)


def _regular_file(path: Path) -> None:
    try:
        if not path.is_file() or path.is_symlink():
            raise HTTPException(422, '目标不是普通文件')
    except OSError:
        raise HTTPException(403, '无法访问工作区文件') from None


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        raise HTTPException(403, '无法访问工作区文件') from None


def _file_headers(disposition: Literal['inline', 'attachment']) -> dict[str, str]:
    return {
        'Content-Disposition': disposition,
        'X-Content-Type-Options': 'nosniff',
        'Cross-Origin-Resource-Policy': 'same-origin',
        'Cache-Control': 'no-store',
    }


@router.get('/sessions/{session_id}/workspace-files')
def list_files(
    session_id: str,
    request: Request,
    path: str = Query(default='', max_length=4096),
):
    root = _workspace_root(request, session_id)
    return {'path': path, 'entries': list_workspace_entries(root, path)}


@router.get('/sessions/{session_id}/workspace-files/content')
def get_text_file(
    session_id: str,
    request: Request,
    path: str = Query(min_length=1, max_length=4096),
):
    root = _workspace_root(request, session_id)
    content, revision = read_workspace_text(root, path)
    return {'path': path, 'content': content, 'revision': revision}


@router.put('/sessions/{session_id}/workspace-files/content')
def save_text_file(session_id: str, payload: WorkspaceTextUpdate, request: Request):
    root = _workspace_root(request, session_id)
    revision = write_workspace_text(root, payload.path, payload.content, payload.expected_revision)
    return {'path': payload.path, 'content': payload.content, 'revision': revision}


@router.get('/sessions/{session_id}/workspace-files/preview')
def preview_file(
    session_id: str,
    request: Request,
    path: str = Query(min_length=1, max_length=4096),
):
    root = _workspace_root(request, session_id)
    target = resolve_workspace_file(root, path)
    _regular_file(target)
    media_type = PREVIEW_MEDIA_TYPES.get(target.suffix.lower())
    if media_type is None:
        raise HTTPException(415, '此文件格式不支持内嵌预览')
    if _file_size(target) > MAX_PREVIEW_BYTES:
        raise HTTPException(413, '预览文件过大')
    return FileResponse(
        target,
        media_type=media_type,
        headers=_file_headers('inline'),
    )


@router.get('/sessions/{session_id}/workspace-files/download')
def download_file(
    session_id: str,
    request: Request,
    path: str = Query(min_length=1, max_length=4096),
):
    root = _workspace_root(request, session_id)
    target = resolve_workspace_file(root, path)
    _regular_file(target)
    return FileResponse(
        target,
        media_type='application/octet-stream',
        filename=target.name,
        headers=_file_headers('attachment'),
    )
