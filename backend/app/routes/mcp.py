import json
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, model_validator

from backend.app.db import database_connection
from backend.app.mcp import MCPError


SUPPORTED_TRANSPORTS = {"stdio", "sse", "streamable_http"}
router = APIRouter(prefix="/api/mcp/servers", tags=["mcp"])


def normalize_transport(value: str) -> str:
    if value == "http":
        return "streamable_http"
    if value not in SUPPORTED_TRANSPORTS:
        raise ValueError("MCP 传输方式必须是 stdio、sse 或 streamable_http")
    return value


class MCPServerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    transport: str
    url: str = ""
    command: str = ""
    args: list[str] = Field(default_factory=list, max_length=64)
    cwd: str = ""
    env: dict[str, str] = Field(default_factory=dict, max_length=50)
    bearer_token: str = Field(default="", max_length=4096)

    @model_validator(mode="after")
    def validate_transport(self):
        self.transport = normalize_transport(self.transport)
        if self.transport == "stdio" and not self.command.strip():
            raise ValueError("stdio MCP Server 必须提供启动命令")
        if self.transport in {"sse", "streamable_http"} and not self.url.startswith(("http://", "https://")):
            raise ValueError("远程 MCP Server 必须使用 http 或 https 地址")
        return self


class ConfirmStart(BaseModel):
    confirmed: bool


@router.get("")
def list_servers(request: Request):
    connected = set(request.app.state.mcp_manager.servers)
    errors = request.app.state.mcp_connections.errors
    with database_connection(request.app.state.database_path) as connection:
        rows = connection.execute(
            "SELECT id,name,transport,command,args_json,cwd,url,enabled,env_names_json,created_at,auto_connect FROM mcp_servers ORDER BY name"
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["transport"] = normalize_transport(item["transport"])
        item["args"] = json.loads(item.pop("args_json"))
        item["env_names"] = json.loads(item.pop("env_names_json") or "[]")
        item["connected"] = item["id"] in connected
        item["auto_connect"] = bool(item["auto_connect"])
        item["connection_error"] = errors.get(item["id"])
        result.append(item)
    return result


@router.post("", status_code=201)
def create_server(payload: MCPServerCreate, request: Request):
    server_id = uuid4().hex
    try:
        with database_connection(request.app.state.database_path) as connection:
            connection.execute(
                "INSERT INTO mcp_servers(id,name,transport,command,args_json,cwd,url,env_names_json) VALUES(?,?,?,?,?,?,?,?)",
                (server_id, payload.name.strip(), payload.transport, payload.command,
                 json.dumps(payload.args), payload.cwd, payload.url, json.dumps(sorted(payload.env))),
            )
    except Exception as error:
        if "UNIQUE" in str(error):
            raise HTTPException(status_code=409, detail="MCP Server 名称已存在") from None
        raise
    request.app.state.secret_store.set(f"mcp:{server_id}:bearer_token", payload.bearer_token) if payload.bearer_token else None
    for env_name, env_value in payload.env.items():
        request.app.state.secret_store.set(f"mcp:{server_id}:env:{env_name}", env_value)
    return {"id": server_id, "env_names": sorted(payload.env), **payload.model_dump(exclude={"bearer_token", "env"})}


@router.post("/{server_id}/connect")
async def connect_server(server_id: str, payload: ConfirmStart, request: Request):
    if not payload.confirmed:
        raise HTTPException(status_code=409, detail="连接 MCP Server 前需要确认启动命令或远程地址")
    try:
        tools = await request.app.state.mcp_connections.connect(server_id, confirmed=True)
    except TimeoutError:
        raise HTTPException(status_code=504, detail="连接 MCP Server 超时") from None
    except MCPError as error:
        raise HTTPException(status_code=502, detail=str(error)) from None
    except OSError:
        raise HTTPException(status_code=502, detail="无法启动或连接 MCP Server，请检查命令、路径和权限") from None
    except (ValueError, TypeError, KeyError):
        raise HTTPException(status_code=409, detail="无法加载 MCP 工具，请检查配置、工具定义或名称冲突") from None
    if tools is None:
        raise HTTPException(status_code=404, detail="MCP Server 不存在或已禁用")
    return {"status": "connected", "tools": tools}


@router.get("/{server_id}/tools")
def list_server_tools(server_id: str, request: Request):
    state = request.app.state.mcp_manager.servers.get(server_id)
    if state is None:
        raise HTTPException(status_code=409, detail="MCP Server 尚未连接")
    result = []
    for tool in state["tools"]:
        annotations = tool.get("annotations") or {}
        result.append({
            "id": f"mcp:{server_id}:{tool['name']}",
            "name": f"{state['name']}/{tool['name']}",
            "description": tool.get("description", ""),
            "input_schema": tool.get("inputSchema", {}),
            "risk": "read_only" if annotations.get("readOnlyHint") is True else "side_effect",
        })
    return result


@router.post("/{server_id}/disconnect")
async def disconnect_server(server_id: str, request: Request):
    if not await request.app.state.mcp_connections.disconnect(server_id):
        raise HTTPException(status_code=404, detail="MCP Server 不存在")
    return {"status": "disconnected"}


@router.delete("/{server_id}", status_code=204)
async def delete_server(server_id: str, request: Request):
    with database_connection(request.app.state.database_path) as connection:
        agents = connection.execute("SELECT name, selected_mcp_tools_json FROM agents").fetchall()
    prefix = f"mcp:{server_id}:"
    references = []
    for agent in agents:
        selected = json.loads(agent["selected_mcp_tools_json"] or "[]")
        if any(isinstance(item, str) and item.startswith(prefix) for item in selected):
            references.append(agent["name"])
    if references:
        raise HTTPException(status_code=409, detail=f"MCP 工具正被智能体使用：{', '.join(references)}")
    await request.app.state.mcp_connections.disconnect(server_id)
    with database_connection(request.app.state.database_path) as connection:
        cursor = connection.execute("DELETE FROM mcp_servers WHERE id = ?", (server_id,))
    if not cursor.rowcount:
        raise HTTPException(status_code=404, detail="MCP Server 不存在")
    request.app.state.secret_store.delete(f"mcp:{server_id}:bearer_token")
