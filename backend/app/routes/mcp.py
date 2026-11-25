import json
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, model_validator

from backend.app.db import database_connection
from backend.app.mcp import MCPError
from backend.app.tools import ToolDefinition

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


def _config(row):
    item = dict(row)
    item["args"] = json.loads(item.pop("args_json"))
    item["connected"] = item["id"] in getattr(row, "connected_ids", set())
    return item


@router.get("")
def list_servers(request: Request):
    connected = set(request.app.state.mcp_manager.servers)
    with database_connection(request.app.state.database_path) as connection:
        rows = connection.execute(
            "SELECT id,name,transport,command,args_json,cwd,url,enabled,env_names_json,created_at FROM mcp_servers ORDER BY name"
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["transport"] = normalize_transport(item["transport"])
        item["args"] = json.loads(item.pop("args_json"))
        item["env_names"] = json.loads(item.pop("env_names_json") or "[]")
        item["connected"] = item["id"] in connected
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
    with database_connection(request.app.state.database_path) as connection:
        row = connection.execute("SELECT * FROM mcp_servers WHERE id = ? AND enabled = 1", (server_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="MCP Server 不存在或已禁用")
    config = dict(row)
    config["transport"] = normalize_transport(config["transport"])
    config["args"] = json.loads(config.pop("args_json"))
    env_names = json.loads(config.pop("env_names_json", "[]") or "[]")
    config["env"] = {name: request.app.state.secret_store.get(f"mcp:{server_id}:env:{name}") or "" for name in env_names}
    config["bearer_token"] = request.app.state.secret_store.get(f"mcp:{server_id}:bearer_token") or ""
    manager = request.app.state.mcp_manager
    if server_id in manager.servers:
        return {"status": "connected", "tools": [f"{manager.servers[server_id]['name']}/{item['name']}" for item in manager.servers[server_id]['tools']]}
    try:
        discovered = await manager.connect(config)
        for item in discovered:
            local_name = f"{config['name']}/{item['name']}"
            annotation = item.get("annotations") or {}
            risk = "read_only" if annotation.get("readOnlyHint") is True else "side_effect"
            request.app.state.tool_registry.register(ToolDefinition(
                name=local_name,
                description=(item.get("description") or f"MCP tool from {config['name']}")[:1000],
                input_schema=item.get("inputSchema") or {"type": "object", "properties": {}, "additionalProperties": False},
                risk=risk,
                handler=lambda arguments, mcp_manager=manager, sid=server_id, remote_name=item["name"]: mcp_manager.call_tool(sid, remote_name, arguments),
                plugin_id=f"mcp:{server_id}:{item['name']}",
            ))
    except MCPError as error:
        raise HTTPException(status_code=502, detail=str(error)) from None
    except OSError:
        raise HTTPException(status_code=502, detail="无法启动或连接 MCP Server，请检查命令、路径和权限") from None
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from None
    return {"status": "connected", "tools": [tool["name"] for tool in discovered]}


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
    manager = request.app.state.mcp_manager
    state = manager.servers.get(server_id)
    prefix = f"{state['name']}/" if state else ""
    await manager.disconnect(server_id)
    request.app.state.tool_registry.unregister_prefix(prefix)
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
    manager = request.app.state.mcp_manager
    state = manager.servers.get(server_id)
    if state:
        await manager.disconnect(server_id)
        request.app.state.tool_registry.unregister_prefix(f"{state['name']}/")
    with database_connection(request.app.state.database_path) as connection:
        cursor = connection.execute("DELETE FROM mcp_servers WHERE id = ?", (server_id,))
    if not cursor.rowcount:
        raise HTTPException(status_code=404, detail="MCP Server 不存在")
    request.app.state.secret_store.delete(f"mcp:{server_id}:bearer_token")
