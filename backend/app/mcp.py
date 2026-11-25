import asyncio
import json
from typing import Any
from urllib.parse import urljoin

import httpx


class MCPError(RuntimeError):
    pass


class MCPManager:
    """Small JSON-RPC MCP client for local stdio and Streamable HTTP servers."""

    def __init__(self, http_client: httpx.AsyncClient):
        self.http_client = http_client
        self.servers: dict[str, dict[str, Any]] = {}
        self._next_id = 0

    def _id(self) -> int:
        self._next_id += 1
        return self._next_id

    async def connect(self, config: dict[str, Any]) -> list[dict[str, Any]]:
        server_id = config["id"]
        existing = self.servers.get(server_id)
        if existing:
            return existing["tools"]
        transport = "streamable_http" if config.get("transport") == "http" else config.get("transport")
        state = {**config, "transport": transport, "session_id": None, "process": None,
                 "lock": asyncio.Lock(), "tools": []}
        if transport == "stdio":
            if not config.get("command"):
                raise MCPError("stdio Server 未配置启动命令")
            process = await asyncio.create_subprocess_exec(
                config["command"], *config["args"], cwd=config.get("cwd") or None,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL, env={**__import__("os").environ, **config.get("env", {})},
            )
            state["process"] = process
        elif transport == "sse":
            await self._open_sse_stream(state)
        elif transport != "streamable_http":
            raise MCPError("不支持的 MCP 传输方式")
        self.servers[server_id] = state
        try:
            await self._request(state, "initialize", {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "local-agent-harness", "version": "0.1.0"},
            })
            await self._request(state, "notifications/initialized", notification=True)
            result = await self._request(state, "tools/list", {})
            tools = result.get("tools", [])
            state["tools"] = tools
            state["status"] = "connected"
            return tools
        except Exception:
            await self.disconnect(server_id)
            raise

    async def _open_sse_stream(self, state: dict[str, Any]) -> None:
        headers = {"Accept": "text/event-stream"}
        if state.get("bearer_token"):
            headers["Authorization"] = f"Bearer {state['bearer_token']}"
        try:
            response = await self.http_client.get(state["url"], headers=headers)
        except httpx.RequestError:
            raise MCPError("无法连接 SSE MCP Server") from None
        if response.status_code >= 400:
            raise MCPError(f"SSE MCP Server 请求失败（HTTP {response.status_code}）")
        endpoint = None
        for line in response.text.splitlines():
            if line.startswith("data:"):
                candidate = line[5:].strip()
                if candidate.startswith(("http://", "https://", "/")):
                    endpoint = candidate
                    break
        if not endpoint:
            raise MCPError("SSE MCP Server 未返回消息 endpoint")
        state["message_url"] = urljoin(state["url"], endpoint)

    async def disconnect(self, server_id: str) -> None:
        state = self.servers.pop(server_id, None)
        if not state:
            return
        process = state.get("process")
        if process and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=3)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()

    async def close(self) -> None:
        for server_id in list(self.servers):
            await self.disconnect(server_id)

    async def call_tool(self, server_id: str, tool_name: str, arguments: dict[str, Any]) -> Any:
        state = self.servers.get(server_id)
        if state is None:
            raise MCPError("MCP Server 未连接")
        result = await self._request(state, "tools/call", {
            "name": tool_name, "arguments": arguments,
        })
        if result.get("isError"):
            raise MCPError("MCP 工具返回错误")
        return result

    async def _request(self, state: dict[str, Any], method: str,
                       params: dict[str, Any] | None = None,
                       notification: bool = False) -> dict[str, Any]:
        async with state["lock"]:
            message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
            if params is not None:
                message["params"] = params
            request_id = None if notification else self._id()
            if request_id is not None:
                message["id"] = request_id
            if state["transport"] == "stdio":
                process = state["process"]
                if process.returncode is not None or process.stdin is None or process.stdout is None:
                    raise MCPError("MCP stdio Server 已退出")
                process.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
                await process.stdin.drain()
                if notification:
                    return {}
                while True:
                    try:
                        line = await asyncio.wait_for(process.stdout.readline(), timeout=30)
                    except asyncio.TimeoutError:
                        raise MCPError("等待 MCP Server 响应超时") from None
                    if not line:
                        raise MCPError("MCP Server 提前关闭了输出流")
                    try:
                        response = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if response.get("id") == request_id:
                        if "error" in response:
                            raise MCPError("MCP Server 返回 JSON-RPC 错误")
                        return response.get("result", {})
            headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
            if state.get("bearer_token"):
                headers["Authorization"] = f"Bearer {state['bearer_token']}"
            if state.get("session_id"):
                headers["Mcp-Session-Id"] = state["session_id"]
            target_url = state.get("message_url", state["url"]) if state["transport"] == "sse" else state["url"]
            try:
                response = await self.http_client.post(target_url, json=message, headers=headers)
            except httpx.RequestError:
                raise MCPError("无法连接远程 MCP Server") from None
            if response.status_code >= 400:
                raise MCPError(f"远程 MCP Server 请求失败（HTTP {response.status_code}）")
            if response.headers.get("Mcp-Session-Id"):
                state["session_id"] = response.headers["Mcp-Session-Id"]
            if notification or response.status_code == 202:
                return {}
            content_type = response.headers.get("content-type", "")
            if "text/event-stream" in content_type:
                candidates = [line[5:].strip() for line in response.text.splitlines() if line.startswith("data:")]
                for candidate in reversed(candidates):
                    try:
                        parsed = json.loads(candidate)
                    except json.JSONDecodeError:
                        continue
                    if parsed.get("id") == request_id:
                        response_body = parsed
                        break
                else:
                    raise MCPError("MCP Server 的 SSE 响应中没有匹配的结果")
            else:
                try:
                    response_body = response.json()
                except ValueError:
                    raise MCPError("MCP Server 返回了无法识别的数据") from None
            if "error" in response_body:
                raise MCPError("MCP Server 返回 JSON-RPC 错误")
            return response_body.get("result", {})
