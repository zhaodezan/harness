"""HTTP API for searching, reviewing, installing and configuring Dify tools."""
from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from .marketplace import MarketplaceError
from .package import MAX_PACKAGE_BYTES, PackageError
from .manager import DifyPluginManager, InstallError

router = APIRouter(prefix='/api/dify-plugins', tags=['dify-plugins'])


class SearchPayload(BaseModel):
    query: str = Field(default='', max_length=100)
    page: int = Field(default=1, ge=1, le=10000)


class LinkPayload(BaseModel):
    link: str = Field(min_length=1, max_length=2048)


class InstallPayload(BaseModel):
    token: str = Field(min_length=32, max_length=128)
    network_enabled: bool = False


class SettingsPayload(BaseModel):
    credentials: dict[str, str] = Field(default_factory=dict, max_length=64)
    network_enabled: bool = False


def _manager(request: Request) -> DifyPluginManager:
    return request.app.state.dify_manager


def _raise(error: Exception) -> None:
    if isinstance(error, (MarketplaceError, PackageError, ValueError)):
        raise HTTPException(status_code=400, detail=str(error)) from None
    if isinstance(error, FileNotFoundError):
        raise HTTPException(status_code=404, detail='未找到插件或插件文件。') from None
    if isinstance(error, FileExistsError):
        raise HTTPException(status_code=409, detail='该插件版本已安装。') from None
    if isinstance(error, InstallError):
        code = 409 if any(text in str(error) for text in ('已安装', '预览已过期', '冲突')) else 503
        raise HTTPException(status_code=code, detail=str(error)) from None
    raise HTTPException(status_code=409, detail='插件状态已改变，请重试。') from None


@router.post('/market')
async def search_market(request: Request, payload: SearchPayload):
    try:
        return await _manager(request).search(payload.query.strip(), payload.page)
    except (MarketplaceError, ValueError) as error:
        _raise(error)


@router.post('/preview/market')
async def preview_market(request: Request, payload: LinkPayload):
    try:
        return await _manager(request).prepare(payload.link)
    except (MarketplaceError, PackageError, ValueError, InstallError) as error:
        _raise(error)


@router.post('/preview/file')
async def preview_file(request: Request, file: UploadFile = File(...)):
    if not file.filename or not file.filename.lower().endswith('.difypkg'):
        raise HTTPException(status_code=400, detail='请选择 .difypkg 插件包。')
    data = await file.read(MAX_PACKAGE_BYTES + 1)
    await file.close()
    if len(data) > MAX_PACKAGE_BYTES:
        raise HTTPException(status_code=413, detail='插件包不能大于 16 MB。')
    try:
        return _manager(request).prepare_file(data)
    except (PackageError, ValueError) as error:
        _raise(error)


@router.post('/install', status_code=201)
async def install(request: Request, payload: InstallPayload):
    try:
        return await _manager(request).install(payload.token, network_enabled=payload.network_enabled)
    except (InstallError, PackageError, ValueError) as error:
        _raise(error)


@router.get('/installed')
def installed(request: Request):
    return _manager(request).list_installed()


@router.get('/tools')
def tools(request: Request):
    return _manager(request).list_tools()


@router.put('/installed/{installed_id}/settings')
async def update_settings(request: Request, installed_id: str, payload: SettingsPayload):
    try:
        return await _manager(request).settings(installed_id, payload.credentials, payload.network_enabled)
    except (InstallError, FileNotFoundError, PackageError, ValueError) as error:
        _raise(error)


@router.delete('/installed/{installed_id}', status_code=204)
async def uninstall(request: Request, installed_id: str):
    try:
        await _manager(request).uninstall(installed_id)
    except (FileNotFoundError, InstallError, ValueError) as error:
        _raise(error)
    return None
