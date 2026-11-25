import ipaddress
import json

from fastapi import APIRouter, File, HTTPException, Request, UploadFile

from backend.app.db import database_connection
from backend.app.tool_plugins import MAX_TOOL_SOURCE_BYTES

router = APIRouter(prefix="/api/tools", tags=["tools"])


@router.get("")
def list_tools(request: Request):
    return request.app.state.local_tool_manager.list_tools()


@router.post("/import", status_code=201)
async def import_tool(request: Request, file: UploadFile = File(...)):
    if not file.filename or not file.filename.lower().endswith(".py"):
        raise HTTPException(status_code=400, detail="只能加载 Python .py 文件")
    source_bytes = await file.read(MAX_TOOL_SOURCE_BYTES + 1)
    await file.close()
    if len(source_bytes) > MAX_TOOL_SOURCE_BYTES:
        raise HTTPException(status_code=413, detail="Tool 源码不能大于 256 KB")
    try:
        source = source_bytes.decode("utf-8")
        return request.app.state.local_tool_manager.install_source(source)
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="Python Tool 必须使用 UTF-8 编码") from None
    except (SyntaxError, ValueError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from None


@router.post("", status_code=201)
def create_tool(request: Request, payload: dict):
    source = payload.get("source")
    if not isinstance(source, str):
        raise HTTPException(status_code=422, detail="请提供 Python Tool 源码")
    try:
        return request.app.state.local_tool_manager.install_source(source)
    except (SyntaxError, ValueError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from None


@router.put("/{tool_id}")
def update_tool(tool_id: str, request: Request, payload: dict):
    source = payload.get("source")
    if not isinstance(source, str):
        raise HTTPException(status_code=422, detail="请提供 Python Tool 源码")
    try:
        return request.app.state.local_tool_manager.update_source(tool_id, source)
    except KeyError:
        raise HTTPException(status_code=404, detail="Tool 不存在") from None
    except (SyntaxError, ValueError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from None


def _require_local_peer(request: Request):
    try:
        local = request.client is not None and ipaddress.ip_address(request.client.host).is_loopback
    except ValueError:
        local = False
    if not local:
        raise HTTPException(status_code=403, detail='只能在本机确认工具执行方式')


@router.put('/{tool_id}/windows-trust')
def trust_windows_tool(tool_id: str, request: Request, payload: dict):
    _require_local_peer(request)
    digest = payload.get('source_sha256')
    if payload.get('confirmed') is not True or not isinstance(digest, str) or len(digest) != 64:
        raise HTTPException(status_code=422, detail='请明确确认当前版本的工具源码')
    try:
        return request.app.state.local_tool_manager.set_windows_trust(tool_id, source_sha256=digest)
    except KeyError:
        raise HTTPException(status_code=404, detail='Tool 不存在') from None
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from None


@router.delete('/{tool_id}/windows-trust')
def revoke_windows_tool(tool_id: str, request: Request):
    _require_local_peer(request)
    try:
        return request.app.state.local_tool_manager.set_windows_trust(tool_id)
    except KeyError:
        raise HTTPException(status_code=404, detail='Tool 不存在') from None
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None


@router.delete("/{tool_id}", status_code=204)
def delete_tool(tool_id: str, request: Request):
    with database_connection(request.app.state.database_path) as connection:
        rows = connection.execute("SELECT name, selected_local_tools_json FROM agents").fetchall()
    references = [row["name"] for row in rows if tool_id in json.loads(row["selected_local_tools_json"] or "[]")]
    if references:
        raise HTTPException(status_code=409, detail=f"Tool 正被智能体使用：{', '.join(references)}")
    if not request.app.state.local_tool_manager.delete_tool(tool_id):
        raise HTTPException(status_code=404, detail="Tool 不存在")
    return None
