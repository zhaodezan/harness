# MCP 传输方式扩展实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to execute this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 支持 Local/stdio、SSE、Streamable HTTP 三种 MCP 连接方式，并兼容已有 `http` 配置。

**Architecture:** 保持 MCPManager 的 JSON-RPC 工具发现/调用接口不变，仅在连接状态中区分 `stdio`、`sse` 和 `streamable_http`。SSE 连接保留事件流以获取 endpoint 和后续 JSON-RPC 响应；Streamable HTTP 复用当前 POST + JSON/SSE 响应解析。路由层规范化旧 `http`，前端两个 MCP 配置入口使用同一传输枚举。

**Tech Stack:** FastAPI、Pydantic、httpx、asyncio、pytest、React、TypeScript。

---

## 文件映射

- 修改 `backend/app/routes/mcp.py`：传输枚举、旧值规范化、配置响应和校验。
- 修改 `backend/app/mcp.py`：SSE 初始化/endpoint/响应读取，Streamable HTTP 分支命名和清理。
- 修改 `frontend/src/pages/PluginLibraryPage.tsx`：MCP 选择器和 payload。
- 修改 `frontend/src/App.tsx`：设置弹窗 MCP 选择器和 payload。
- 修改 `tests/test_mcp.py`：三种传输和旧 `http` 兼容测试。
- 修改 `tests/test_frontend_model_library.py`、新建 `tests/test_frontend_mcp_transports.py`：前端选择器契约。

## Task 1: 失败测试——API 接受三种传输并拒绝未知值

**Files:** `tests/test_mcp.py`

- [ ] **Step 1: 添加配置校验测试**

对 `/api/mcp/servers` POST 分别提交 `stdio`、`sse`、`streamable_http`，断言 201、列表返回规范化值；提交 `http` 时断言兼容为 `streamable_http`；提交 `websocket` 时断言 422 且错误说明允许值。

```python
@pytest.mark.parametrize("transport", ["stdio", "sse", "streamable_http"])
def test_mcp_accepts_supported_transports(tmp_path, transport):
    app = create_app(tmp_path / f"{transport}.sqlite3", secret_store=MemorySecrets())
    with TestClient(app) as client:
        payload = {"name": transport, "transport": transport}
        if transport == "stdio":
            payload["command"] = "python"
        else:
            payload["url"] = "https://example.test/mcp"
        response = client.post("/api/mcp/servers", json=payload)
    assert response.status_code == 201
```

- [ ] **Step 2: 运行测试确认失败**

运行：`python -m pytest tests/test_mcp.py -q`

预期：SSE/Streamable HTTP 测试失败，当前模型只允许 `stdio/http`。

## Task 2: 实现路由层传输规范化

**Files:** `backend/app/routes/mcp.py`

- [ ] **Step 1: 定义允许值和规范化函数**

增加：

```python
SUPPORTED_TRANSPORTS = {"stdio", "sse", "streamable_http"}


def normalize_transport(value: str) -> str:
    if value == "http":
        return "streamable_http"
    if value not in SUPPORTED_TRANSPORTS:
        raise ValueError("MCP 传输方式必须是 stdio、sse 或 streamable_http")
    return value
```

`MCPServerCreate` 的 model validator 使用规范化值；Pydantic validator 必须返回 `self`，避免只校验不转换。URL 校验应用于 `sse` 和 `streamable_http`；命令校验只应用于 `stdio`。

- [ ] **Step 2: 规范化读取旧数据**

`list_servers` 将数据库中的 `http` 转为 `streamable_http` 后返回；连接前将配置值归一化，旧数据无需立即迁移。创建时只写规范化值。`url`、`bearer_token` 和 env secret 的现有安全规则保持不变。

- [ ] **Step 3: 运行路由测试**

运行：`python -m pytest tests/test_mcp.py -q`

预期：配置校验和旧值列表测试通过；真正连接测试暂时因 MCPManager 未支持而失败。

## Task 3: 失败测试——SSE 初始化和消息读取

**Files:** `tests/test_mcp.py`

- [ ] **Step 1: 增加 SSE mock transport**

mock transport 对 GET 返回持久响应，事件顺序包含 endpoint 和 initialize 响应；对消息 endpoint 的 POST 根据 JSON-RPC method 返回 `notifications/initialized` 202、`tools/list` JSON。测试创建 `transport=sse`，连接后断言工具发现成功、Bearer Token 出现在 GET/POST 请求中。

- [ ] **Step 2: 增加 Streamable HTTP 测试**

将现有 HTTP 测试的 transport 改为 `streamable_http`，保留 JSON 响应、`Mcp-Session-Id`、工具发现和 Token 断言；另加 `http` 数据库旧值连接测试，确认 manager 以 Streamable HTTP 处理。

- [ ] **Step 3: 运行新增测试确认失败**

运行：`python -m pytest tests/test_mcp.py -q`

预期：SSE 连接失败，当前 manager 只接受 `stdio` 或 `http`。

## Task 4: 实现 MCPManager 的三种传输

**Files:** `backend/app/mcp.py`

- [ ] **Step 1: 扩展连接状态和分支**

在 `connect` 中对配置 transport 做规范化：`http` 映射到 `streamable_http`。`stdio` 保持子进程创建；`sse` 调用 `_open_sse_stream`；`streamable_http` 不创建进程。未知值抛出 `MCPError("不支持的 MCP 传输方式")`。

- [ ] **Step 2: 实现 SSE stream 打开和 endpoint 解析**

增加 `_open_sse_stream(state)`：使用 `self.http_client.stream("GET", url, headers={"Accept": "text/event-stream", ...})`，进入响应上下文后逐行读取，直到收到 `event: endpoint` 的 data。用 `urljoin(state["url"], endpoint_data)` 得到 `state["message_url"]`；若 endpoint data 已是绝对 URL，保持绝对 URL。保存 context manager/response 到 state，后续请求继续读取该流。HTTP 错误、无 endpoint、超时和无效 endpoint 分别抛安全 `MCPError`。

- [ ] **Step 3: 实现 SSE JSON-RPC 请求**

在 `_request` 中对 `state["transport"] == "sse"`：

1. POST JSON-RPC 到 `state["message_url"]`，带 `Accept: application/json, text/event-stream`、Content-Type、Bearer Token；
2. notification 收到 202 后返回空对象；
3. 普通 POST 若直接返回 JSON，按请求 ID 校验并返回；
4. 若响应为 202，则从保存的 SSE response 逐行解析 `data:` JSON，跳过无效/非目标事件，直到匹配 request ID；
5. JSON-RPC error 转为 `MCPError`，响应超时转为明确超时错误。

增加内部 `_read_sse_json(state, request_id)`，处理跨多行 data 缓冲和 `event` 行，不把服务端原始敏感内容直接抛出。

- [ ] **Step 4: 保留并改名 Streamable HTTP 分支**

将当前 `_request` 中的非-stdio分支明确限定为 `streamable_http` 和兼容映射后的 `http`；保留 `Mcp-Session-Id`、JSON 响应和单次 SSE 响应解析。不要把 Streamable HTTP 当成持久 SSE 连接。

- [ ] **Step 5: 清理 SSE 资源**

在 `disconnect` 中关闭保存的 SSE response 和 stream context manager，再清理 process；即使初始化失败也调用同一清理逻辑。`close()` 继续逐个 disconnect，避免后台连接泄漏。

- [ ] **Step 6: 运行 MCP 后端测试**

运行：`python -m pytest tests/test_mcp.py tests/test_mcp_stdio.py tests/test_mcp_env_secrets.py tests/test_mcp_plugin_deletion.py -q`

预期：stdio、SSE、Streamable HTTP、旧 http、Token 隔离、删除引用保护全部通过。

## Task 5: 更新插件库 MCP 表单

**Files:** `frontend/src/pages/PluginLibraryPage.tsx`、新建 `tests/test_frontend_mcp_transports.py`

- [ ] **Step 1: 添加前端契约测试**

断言传输类型 union 包含 `'stdio' | 'sse' | 'streamable_http'`，选择器包含“Local（stdio）”“SSE”“Streamable HTTP”，HTTP 表单分支同时适用于 sse/streamable_http，payload 的 `transport` 使用选中值。

- [ ] **Step 2: 运行测试确认失败**

运行：`python -m pytest tests/test_frontend_mcp_transports.py -q`

预期：失败，当前选择器只有 stdio/http。

- [ ] **Step 3: 修改类型和选择器**

将 state 类型改为：

```tsx
type MCPTransport = 'stdio' | 'sse' | 'streamable_http';
const [mcpTransport, setMcpTransport] = useState<MCPTransport>('stdio');
```

选择器改为：

```tsx
<select value={mcpTransport} onChange={(e) => setMcpTransport(e.target.value as MCPTransport)}>
  <option value="stdio">Local（本机程序 / stdio）</option>
  <option value="sse">SSE</option>
  <option value="streamable_http">Streamable HTTP</option>
</select>
```

表单字段判断只使用 `mcpTransport === 'stdio'`；非 stdio 均显示 URL/Bearer Token。payload 中非 stdio 的 `url` 和 `bearer_token` 保留，command/args/cwd/env 置空或空数组。

- [ ] **Step 4: 修改列表显示**

列表的 target 文案按 transport 显示 Local 命令、SSE URL 或 Streamable HTTP URL；连接确认消息不要显示 Token。旧 API 返回 `http` 时前端按 streamable_http 标签显示。

- [ ] **Step 5: 运行插件库契约和 typecheck**

运行：`python -m pytest tests/test_frontend_mcp_transports.py tests/test_frontend_model_library.py -q`  
运行：`npm --prefix frontend run typecheck`

预期：通过。

## Task 6: 更新设置弹窗 MCP 表单

**Files:** `frontend/src/App.tsx`

- [ ] **Step 1: 复用 MCPTransport 类型**

将 App 中的 MCP state 改为同一 union，移除 `mcpTransport === 'http'` 判断，统一使用 `mcpTransport === 'stdio'` 判断本地字段。

- [ ] **Step 2: 更新选择器和 payload**

使用与插件库相同的三项标签；创建请求传递 `transport: mcpTransport`，非 stdio 时传 URL/Token，stdio 时传命令、参数、工作目录和 env。连接/断开显示逻辑使用 `server.transport === 'stdio'`，其他两种均以 URL 作为 target。

- [ ] **Step 3: 运行前端回归**

运行：`python -m pytest tests/test_frontend_mcp_transports.py tests/test_frontend_routes.py -q`  
运行：`npm --prefix frontend run typecheck`

预期：通过。

## Task 7: MCP 前端/后端整体验收

- [ ] **Step 1: 运行专项后端测试**

运行：`python -m pytest tests/test_mcp.py tests/test_mcp_stdio.py tests/test_mcp_env_secrets.py tests/test_mcp_plugin_deletion.py tests/test_agent_plugin_access.py -q`

预期：通过。

- [ ] **Step 2: 构建前端**

运行：`npm --prefix frontend run build`

预期：成功。

- [ ] **Step 3: 手工验证表单**

打开模型及插件库 MCP Tab 和设置弹窗，确认都能选择 Local、SSE、Streamable HTTP；Local 显示命令字段，另两种显示 URL/Token 字段；创建后列表显示正确类型，连接确认不包含 Token。

- [ ] **Step 4: 手工验证旧配置**

使用已有数据库中 transport 为 `http` 的 MCP 配置，确认列表仍可读取并按 Streamable HTTP 连接，不要求用户重新填写。
