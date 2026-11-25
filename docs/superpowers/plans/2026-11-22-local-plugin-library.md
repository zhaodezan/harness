# 本地插件库与智能体调试页实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现本地 Agent/Skill/MCP/Python Tool 插件库、智能体级插件授权、自动保存配置，以及使用当前配置实时调试的并排界面。

**Architecture:** 新增本机受管理插件存储和 Skill ZIP/Python Tool 管理 API；扩展 Agent 数据模型保存每个智能体授权的插件 ID，并在运行时同时过滤模型可见工具和执行权限。前端新增插件库页面，并把现有智能体配置改成左侧配置、右侧调试；配置防抖自动保存，调试请求传送当前配置快照，不保存调试会话。

**Tech Stack:** Python 3.11、FastAPI、SQLite、Pydantic、现有 ToolRegistry/MCPManager、React 19、TypeScript、Vite、pytest。

---

## 文件结构与责任

### 后端

- `backend/app/plugin_store.py`（新建）：安全管理 `AGENT_HARNESS_HOME/plugins/skills` 和 `.../tools`，提供 ZIP 安装、Skill 列表、Python Tool 文件校验/存储。
- `backend/app/tool_plugins.py`（新建）：加载受管理 Python Tool 模块、校验模块契约，并注册/卸载 ToolRegistry handler。
- `backend/app/routes/skills.py`：增加已加载 Skill 列表、ZIP 导入和删除接口；保留旧目录 discover 路由供迁移兼容。
- `backend/app/routes/tools.py`（新建）：本地 Python Tool 列表、导入、保存、删除 API。
- `backend/app/main.py`：初始化 plugin store 和 tool plugin manager，注册路由；生命周期启动时加载已安装工具；`create_app` 增加可注入的 `data_dir` 以隔离测试数据。
- `pyproject.toml`：添加 FastAPI multipart 上传依赖 `python-multipart`，安装器通过现有 `pip install -e .` 自动安装。
- `backend/app/db.py`：新增 Agent 插件授权列及必要的 schema migration。
- `backend/app/models.py`、`backend/app/routes/agents.py`、`backend/app/routes/setup.py`：支持 MCP/Tool 选择字段与更新/创建，创建时费率为 `null`。
- `backend/app/routes/sessions.py`：只加载已选 Skills，只向模型展示 Agent 已授权工具，并在执行时二次拒绝未授权工具。
- `backend/app/routes/data_transfer.py`：Agent 插件授权字段进入备份/恢复。
- `backend/app/routes/debug.py`：接收配置快照和临时消息，复用现有 Provider/Tool 执行与审批逻辑，不写入持久 sessions/messages。

### 前端

- `frontend/src/pages/PluginLibraryPage.tsx`（新建）：Agent、Skill、MCP、Tool 四个本地插件 Tab。
- `frontend/src/pages/AgentDebugChat.tsx`（新建）：独立调试聊天面板，维护仅在页面内存中的消息，使用当前配置快照发送请求。
- `frontend/src/api.ts`：让 `api` helper 在 body 为 `FormData` 时跳过 JSON 序列化/`Content-Type`，供 ZIP 上传使用。
- `frontend/src/pages/AgentConfigPage.tsx`：两栏配置布局、插件勾选、自动保存、新建时连接成功即创建、移除费率控件。
- `frontend/src/App.tsx`：插件库路由和工作区导航；向配置页提供当前智能体列表更新回调。
- `frontend/src/styles.css`：插件库 Tab/列表/Tool 编辑器和响应式配置调试分栏样式。

### 测试

- 新建 `tests/test_skill_import.py`、`tests/test_tool_plugins.py`、`tests/test_agent_plugin_access.py`、`tests/test_agent_debug.py`。
- 扩展 `tests/test_agents.py`、`tests/test_setup_wizard.py`、`tests/test_data_transfer.py`、`tests/test_frontend_routes.py`、`tests/test_frontend.py`。
- 现有 `tests/test_skills.py`、`tests/test_skills_runtime.py`、`tests/test_tool_runtime.py`、`tests/test_mcp.py`、`tests/test_mcp_stdio.py`、`tests/test_costs.py` 是回归验证入口。

## 实施步骤

### Task 1: 管理本地插件目录并安全导入 Skills ZIP

**Files:**
- Create: `backend/app/plugin_store.py`
- Create: `tests/test_skill_import.py`
- Modify: `backend/app/routes/skills.py`
- Modify: `backend/app/main.py`
- Modify: `pyproject.toml`

- [x] **Step 1: 写 ZIP 导入失败与成功测试**

在 `tests/test_skill_import.py` 测试有效单 Skill ZIP 导入、超 10 MB、路径穿越、缺少有效 `SKILL.md`、重名和损坏 ZIP。以 FastAPI `TestClient(create_app(tmp_path / "skills.sqlite3", secret_store=Secrets()))` 调用 `POST /api/skills/import`，上传 `files={"file": ("skill.zip", archive_bytes, "application/zip")}`；成功断言 201 和列表含 skill 名，失败断言 4xx 且 `GET /api/skills` 不含失败项。

```python
import io
import zipfile

from fastapi.testclient import TestClient
from backend.app.main import create_app


def test_import_rejects_zip_slip_without_writing_outside_data_dir(tmp_path):
    archive_bytes = io.BytesIO()
    with zipfile.ZipFile(archive_bytes, "w") as archive:
        archive.writestr("../escape/SKILL.md", "---\\nname: escape\\ndescription: bad\\n---\\n")
    data_dir = tmp_path / "app-data"
    app = create_app(tmp_path / "skills.sqlite3", data_dir=data_dir)
    with TestClient(app) as client:
        response = client.post("/api/skills/import", files={"file": ("bad.zip", archive_bytes.getvalue(), "application/zip")})
        listed = client.get("/api/skills").json()
    assert response.status_code == 400
    assert listed == []
    assert not (data_dir / "plugins" / "skills" / "escape" / "SKILL.md").exists()
```

- [x] **Step 2: 运行新测试确认失败**

Run: `python -m pytest tests/test_skill_import.py -q`  
Expected: FAIL，当前没有 `/api/skills/import` 路由，断言 HTTP 400 会失败。

- [x] **Step 3: 实现安全插件目录与原子导入**

在 `plugin_store.py` 定义 `PluginStore(data_dir: Path)`；Skills 存放在 `data_dir / "plugins" / "skills"`。导入函数先将 ZIP 解压到同目录临时目录，逐项验证相对路径、禁止 symlink、总解压字节不超过 10 MB、只接收含一个或多个 `*/SKILL.md` 的 Skill 树；调用 `discover_skills` 验证 frontmatter 和唯一名称，成功后原子移动到正式目录。失败清理临时目录。用 `get_data_dir()` 初始化 Store，并从 `/api/skills` 暴露 list/import/delete。

- [x] **Step 4: 运行新测试确认通过**

Run: `python -m pytest tests/test_skill_import.py tests/test_skills.py -q`  
Expected: PASS；非法归档不在管理目录外写入文件，导入失败不留下可见 Skill。

### Task 2: 实现本地 Python Tool 加载和管理 API

**Files:**
- Create: `backend/app/tool_plugins.py`
- Create: `backend/app/routes/tools.py`
- Create: `tests/test_tool_plugins.py`
- Modify: `backend/app/plugin_store.py`
- Modify: `backend/app/main.py`

- [x] **Step 1: 写工具契约校验与执行测试**

新增一份临时 Python 文件，包含 `TOOL = {"name": "lookup", "description": "Lookup", "input_schema": {"type": "object", ...}, "risk": "read_only"}` 和 `def run(arguments): return ...`。测试加载后 ToolRegistry 可列出并执行；再分别测试无执行函数、无效 Schema、重复名称和副作用 Tool 未批准时不得运行 handler。

```python
import asyncio

from backend.app.tools import ToolRegistry
from backend.app.tool_plugins import LocalToolManager


def test_local_tool_registers_declared_handler(tmp_path):
    registry = ToolRegistry()
    manager = LocalToolManager(tmp_path, registry)
    source = '''TOOL = {
        "name": "lookup",
        "description": "Lookup",
        "input_schema": {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"], "additionalProperties": False},
        "risk": "read_only",
    }
def run(arguments):
    return arguments["key"] + "-result"
'''
    item = manager.install_source(source)
    assert item["name"] == "lookup"
    assert asyncio.run(registry.execute("lookup", {"key": "x"}, approved=True)) == "x-result"
```

- [x] **Step 2: 运行新测试确认失败**

Run: `python -m pytest tests/test_tool_plugins.py -q`  
Expected: FAIL，当前没有 `LocalToolManager`。

- [x] **Step 3: 实现显式安装、校验及加载**

在 `tool_plugins.py` 定义固定契约解析、Schema 校验和动态加载；单个模块只注册一个 `ToolDefinition`，允许 `run(arguments)` 为同步或异步函数。Store 将源码复制到由 UUID/slug 组成的受管理目录，拒绝用户文件名参与路径拼接。管理 API 支持列出、上传 `.py`、保存编辑器源码和删除；删除前检查智能体引用。应用启动时从受管理目录逐个加载，单个工具加载异常记为该工具错误，不阻断启动。前端接口响应不包含本机绝对路径或代码外的敏感数据。

- [x] **Step 4: 运行新测试与既有工具测试**

Run: `python -m pytest tests/test_tool_plugins.py tests/test_tool_runtime.py tests/test_tools.py -q`  
Expected: PASS；既有 ToolRegistry 风险审批行为不变。

### Task 3: 增加智能体级插件授权字段与数据迁移

**Files:**
- Modify: `backend/app/db.py`
- Modify: `backend/app/models.py`
- Modify: `backend/app/routes/agents.py`
- Modify: `backend/app/routes/setup.py`
- Modify: `backend/app/routes/data_transfer.py`
- Create or modify: `tests/test_agent_plugin_access.py`
- Modify: `tests/test_agents.py`、`tests/test_data_transfer.py`、`tests/test_costs.py`

- [ ] **Step 1: 写新旧智能体授权数据测试**

断言创建/读取/更新 Agent 保留 `selected_skills`、`selected_mcp_tools`、`selected_local_tools`；MCP 权限键格式固定为 `mcp:<server_id>:<remote_tool_name>`，兼容历史已连接 Server 的 `mcp:<server_id>:*` 全工具授权；空列表不自动授权新工具；数据库旧版本升级后列有默认 JSON `[]`，不丢字段。备份导出/恢复保持字段一致。

```python
from backend.app.main import create_app
from fastapi.testclient import TestClient


def test_agent_patch_persists_independent_tool_selections(tmp_path):
    with TestClient(create_app(tmp_path / "agents.sqlite3")) as client:
        agent = client.post("/api/agents", json={"name": "A"}).json()
        updated = client.patch(f"/api/agents/{agent['id']}", json={
            "selected_mcp_tools": ["mcp:srv-1:tool-a"],
            "selected_local_tools": ["tool-1"],
        }).json()
    assert updated["selected_mcp_tools"] == ["mcp:srv-1:tool-a"]
    assert updated["selected_local_tools"] == ["tool-1"]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_agent_plugin_access.py tests/test_agents.py -q`  
Expected: FAIL，Agent API 和 SQLite 当前没有工具选择字段。

- [ ] **Step 3: 添加 SQLite migration 与 schema/API 字段**

提升 `SCHEMA_VERSION`，新增 `selected_mcp_tools_json` 和 `selected_local_tools_json` 默认 `[]`。在 `AgentCreate`、`AgentPatch`、`AgentResponse` 加入 `list[str]` 字段，MCP/Tool 最多各 100 项；Agent routes 编码/解码 JSON。`ModelSetup` 支持 Skill/MCP/Tool 列表，凭据仍只通过 secret store 保存。备份导出和导入处理新字段。`AgentPatch` 只允许费率字段显式为 null：

```python
nullable_fields = {"input_rate_per_million", "output_rate_per_million"}
if not updates or any(value is None for field, value in updates.items() if field not in nullable_fields):
    raise HTTPException(status_code=422, detail="请提供有效的更新内容")
```

配置保存明确将费率设置为 `null`，不删除旧运行记录中的估算费用。

- [ ] **Step 4: 运行数据模型与备份回归测试**

Run: `python -m pytest tests/test_agent_plugin_access.py tests/test_agents.py tests/test_setup_wizard.py tests/test_data_transfer.py tests/test_costs.py -q`  
Expected: PASS；旧备份无新字段时仍可导入，现有 Agent 返回新字段为空列表。

### Task 4: 迁移旧 Skills 并按 Agent 权限过滤运行时能力

**Files:**
- Create: `backend/app/plugin_migrations.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/routes/sessions.py`
- Modify: `backend/app/routes/mcp.py`
- Modify: `backend/app/tools.py`
- Modify: `tests/test_skills_runtime.py`、`tests/test_tool_runtime.py`
- Modify: `tests/test_agent_plugin_access.py`

- [ ] **Step 1: 写隔离和旧数据迁移测试**

注册工具 `allowed`、`denied`，创建只选中 `allowed` 的 Agent；mock provider 请求只能看到 `allowed`，并模拟 provider 返回 `denied`，断言 handler 没执行且响应包含未知/未授权工具错误。迁移测试验证旧 `skill_directory` 中有效技能复制到 managed store 并保存为该 Agent 已选；无效旧路径保留并记录可见迁移错误。

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_agent_plugin_access.py tests/test_skills_runtime.py tests/test_tool_runtime.py -q`  
Expected: FAIL，当前 sessions 使用全局 registry.list() 和仅按目录 discover Skills。

- [ ] **Step 3: 按 Agent 的明确列表过滤 Skills 和 Tools**

升级时对旧数据执行幂等迁移：有效旧 Skills 复制进应用 Skills 库、旧名字加入 selected list；不能迁移时保留旧路径且不静默清空。为 `ToolDefinition` 增加稳定 `plugin_id`；本地 Tool 使用其工具 ID，MCP route 注册时使用 `mcp:<server_id>:<remote_tool_name>`。既有 Agent 迁移为已有 MCP Server 的 `mcp:<server_id>:*` 授权，保持原有可用范围；新 Agent 仍默认为空授权。运行时只从 managed store 加载被选中的 Skills，并仅向 Provider 传 `plugin_id` 在该 Agent 授权集合内的工具。模型返回调用后、审批和 handler 执行前再次检查授权，未授权调用拒绝。MCP server 连接、发现及未选中工具不应因此被关闭或删除。

- [ ] **Step 4: 运行运行时与 MCP 回归测试**

Run: `python -m pytest tests/test_skills_runtime.py tests/test_tool_runtime.py tests/test_provider_tools.py tests/test_mcp.py tests/test_mcp_stdio.py tests/test_agent_plugin_access.py -q`  
Expected: PASS；被授权工具工作，未授权工具模型不可见且不可执行，现有 MCP 调用流程仍通过。

### Task 5: 新增插件库页面和工作区入口

**Files:**
- Modify: `frontend/src/pages/PluginLibraryPage.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/styles.css`
- Modify: `tests/test_frontend_plugin_library.py`

- [x] **Step 1: 写导航、页面路由和市场未启用测试**

增加前端源码验收：`App.tsx` 有 `/plugins` route 和“插件库”导航，插件页有 Agent/Skill/MCP/Tool 四个本地管理 Tab，且不出现在线市场入口；`api.ts` 对 `FormData` 不设置 JSON Content-Type。后端静态 SPA 路由对 `/plugins` 返回 index，未知 API 仍 404。

```python
from fastapi.testclient import TestClient
from backend.app.main import create_app


def test_plugin_library_route_serves_frontend(tmp_path):
    with TestClient(create_app(tmp_path / "plugins.sqlite3")) as client:
        response = client.get("/plugins")
    assert response.status_code == 200
    assert '<div id="root"></div>' in response.text
```

- [x] **Step 2: 运行新测试确认失败**

Run: `python -m pytest tests/test_frontend_plugin_library.py tests/test_frontend_routes.py -q`  
Expected: FAIL，当前无插件库 route。

- [x] **Step 3: 实现四个本地管理 Tab**

创建页面组件，通过 `api.ts` 调用 Skills/Tools/MCP/Agents API。更新 `api.ts` 使 `FormData` 使用浏览器生成的 multipart boundary，其余请求仍默认 JSON：

```ts
const headers = new Headers(init?.headers);
if (!(init?.body instanceof FormData)) headers.set('Content-Type', 'application/json');
const response = await fetch(`/api${path}`, { ...init, headers });
```

Agent Tab 复用现有 Agent 展示与配置入口；Skill Tab 有 ZIP file input 和已加载列表；MCP Tab 有现有配置、连接、断开、发现工具与删除 UI；Tool Tab 有可信代码提示、源码编辑器、`.py` file input 和已安装列表。顶层导航 Tab 固定为 `Agent`、`Skill`、`MCP`、`Tool`、`第三方插件`；第三方插件 Tab 下有 `Dify` 和 `DSH` 两个占位子 Tab，只显示“暂未实现”文案，无加载/安装/连接控件或后端请求。删除 App 侧栏原“工具与连接”导航按钮，保留 MCP 页面在插件库内。添加插件库样式及窄屏 Tab 滚动。App 新增导航和路由；`main.py` SPA allowlist 接受 `plugins`。

- [x] **Step 4: 先扩展前端行为测试**

```python
def test_third_party_tab_contains_dify_and_dsh_placeholders_without_install_actions():
    source = (FRONTEND / "pages" / "PluginLibraryPage.tsx").read_text(encoding="utf-8")
    app = (FRONTEND / "App.tsx").read_text(encoding="utf-8")
    assert "第三方插件" in source
    assert "Dify" in source
    assert "DSH" in source
    assert "暂未实现" in source
    assert "工具与连接" not in app
    third_party = source.split("activeTab === '第三方插件'", 1)[1]
    assert "install" not in third_party.lower()
    assert "fetch(" not in third_party
```

Run: `python -m pytest tests/test_frontend_plugin_library.py::test_third_party_tab_contains_dify_and_dsh_placeholders_without_install_actions -q`  
Expected: FAIL，因为当前只有四个顶层 Tab，App 仍显示重复的“工具与连接”入口。

- [x] **Step 5: 实现第三方插件占位 Tab 并移除重复导航**

给 `PluginLibraryPage.tsx` 的 `Tab` 联合类型及 `tabs` 数组增加 `'第三方插件'`；增加独立的 `thirdPartyTab` 状态（`'Dify' | 'DSH'`），只在第三方插件 Tab 内渲染子标签和“暂未实现，后续版本开放”占位文案。占位区域不挂载上传控件、不调用插件 API。MCP 配置页原样保留。`App.tsx` 删除“工具与连接”侧栏按钮，不删除已可访问的插件库 MCP Tab。更新测试确认第三方 Tab 正常且不含安装行为。

- [x] **Step 6: 运行前端源测试和 TypeScript 构建**

Run: `python -m pytest tests/test_frontend_plugin_library.py tests/test_frontend_routes.py tests/test_frontend_agent_list.py -q`  
Expected: PASS。  
Run: `npm --prefix frontend run build`  
Expected: TypeScript 检查及 Vite 构建成功。

### Task 6: 在配置页选择插件并实现自动保存

**Files:**
- Modify: `frontend/src/pages/AgentConfigPage.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/styles.css`
- Modify: `tests/test_frontend.py`、`tests/test_frontend_styles.py`
- Create or modify: `tests/test_frontend_agent_config.py`

- [ ] **Step 1: 写自动保存、授权选择、费率移除与双栏布局测试**

测试确保编辑页含 `selected_skills`、`selected_mcp_tools`、`selected_local_tools` 控件，存在 saving/saved/error 状态和显式 retry，任何配置保存 payload 中费率为 `null`，创建/编辑页面无费率输入；CSS 提供桌面双栏与窄屏堆叠。新建向导测试连接成功后只创建一次 Agent。

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_frontend_agent_config.py tests/test_frontend.py tests/test_frontend_styles.py -q`  
Expected: FAIL，当前表单需手动保存、无 MCP/Tool 选择且含费率输入。

- [ ] **Step 3: 实现防抖保存与插件选择**

编辑状态将字段更新纳入一个 `AgentDraft`；防抖 PATCH 使用单队列/递增 revision，保证较旧请求不会覆盖新草稿。UI 显示“保存中 / 已保存 / 保存失败”，失败保留 draft 并可重试；卸载前 flush pending save，失败时确认离开。加载全局 skills、MCP tools 和 local tools，按稳定 ID 对 Agent 字段单独勾选。新建流程在模型连接测试成功后调用一次 `/api/setup/model` 创建 Agent 与凭据，路由到该 Agent 的配置页，此后相同自动保存机制处理更新。删除费率控件，更新 payload 将两费率清空。移除旧“Skills 文件夹路径”编辑控件；旧路径迁移失败时显示后端返回的警告，不清空原数据。

- [ ] **Step 4: 运行前端测试和构建**

Run: `python -m pytest tests/test_frontend_agent_config.py tests/test_frontend.py tests/test_frontend_styles.py tests/test_frontend_agent_list.py -q`  
Expected: PASS。  
Run: `npm --prefix frontend run build`  
Expected: TypeScript 检查及 Vite 构建成功。

### Task 7: 增加并排调试聊天及非持久化调试 API

**Files:**
- Create: `frontend/src/pages/AgentDebugChat.tsx`
- Create: `backend/app/routes/debug.py`
- Modify: `backend/app/main.py`
- Modify: `frontend/src/pages/AgentConfigPage.tsx`
- Modify: `frontend/src/styles.css`
- Create: `tests/test_agent_debug.py`
- Create or modify: `tests/test_frontend_debug_chat.py`

- [ ] **Step 1: 写临时调试 API 测试**

用 MockTransport 验证 debug 请求接收当前表单 config snapshot 和 `[{role, content}]` 临时历史，使用 Agent secret store 中已保存凭据；SSE 响应包含 delta/trace，数据库中 sessions/messages 数量保持 0；只允许快照授权的 Tools。对于传入未知 Agent、无凭据或无效变量返回明确 4xx。

```python
import sqlite3

import httpx
from fastapi.testclient import TestClient
from backend.app.main import create_app


class Secrets:
    def get(self, name): return "test-key"
    def set(self, name, value): pass
    def delete(self, name): pass


def test_debug_does_not_persist_messages_or_sessions(tmp_path):
    async def provider(_request):
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"ok"}}]}\\n\\ndata: [DONE]\\n\\n')

    database = tmp_path / "debug.sqlite3"
    app = create_app(database, http_transport=httpx.MockTransport(provider), secret_store=Secrets())
    with TestClient(app) as client:
        agent = client.post("/api/agents", json={
            "name": "Debug", "base_url": "https://example.test/v1", "model": "m"
        }).json()
        response = client.post(f"/api/agents/{agent['id']}/debug", json={
            "config": {
                "base_url": "https://example.test/v1", "model": "m", "mode": "react",
                "context_window": 32768, "system_prompt": "Be helpful.",
                "prompt_variables": {}, "selected_skills": [],
                "selected_mcp_tools": [], "selected_local_tools": [],
            },
            "messages": [{"role": "user", "content": "hello"}],
        })
    with sqlite3.connect(database) as connection:
        sessions = connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        messages = connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    assert response.status_code == 200
    assert '"text":"ok"' in response.text
    assert sessions == messages == 0
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_agent_debug.py -q`  
Expected: FAIL，当前无调试 route。

- [ ] **Step 3: 复用现有运行时能力实现临时调试**

抽取 `sessions.py` 中 provider streaming、tool call、approval 和事件流必需的公共运行逻辑，常规聊天和调试共用同一个执行实现以避免行为漂移。新增调试路由校验 Agent ID 与凭据，从请求快照组装模型/Prompt/插件权限，但不将配置写回数据库；对话上下文由前端传入，不创建 Session/Message/ContextSummary。调试工具批准复用 `pending_approvals` 流程，拒绝/超时沿用现有处理。前端 `AgentDebugChat` 接收当前 draft，每次发送携带其快照和内存消息，展示流式回答、运行轨迹、工具批准和错误。

- [ ] **Step 4: 运行调试与常规对话回归测试**

Run: `python -m pytest tests/test_agent_debug.py tests/test_chat.py tests/test_provider_tools.py tests/test_approval_roundtrip.py tests/test_plan_execute.py -q`  
Expected: PASS；debug 不产生持久对话，现有聊天、Plan-Execute 和审批不回归。

- [ ] **Step 5: 完成双栏响应式配置页面**

把 `AgentConfigPage` 内容放入 config column，将 `AgentDebugChat` 放入 debug column；桌面采用两栏且聊天区域可独立滚动，窄屏采用上下堆叠。发送调试消息时使用当前状态快照，不等待或读取旧的数据库版本；模型连接/配置未就绪时解释如何完成配置。前端测试确保更新一个字段后紧接着发送，debug body 使用更新后的值。

Run: `python -m pytest tests/test_frontend_debug_chat.py tests/test_frontend_agent_config.py tests/test_frontend_routes.py -q`  
Expected: PASS。  
Run: `npm --prefix frontend run build`  
Expected: PASS。

### Task 8: 端到端验收和全量回归

**Files:**
- Modify only files required by failing tests from prior tasks.
- Tests: `tests/` and `frontend/` build.

- [ ] **Step 1: 跑全量后端测试**

Run: `python -m pytest -q`  
Expected: 全部通过；若失败，只修复由本功能引入的回归，再重跑失败测试。

- [ ] **Step 2: 跑前端构建与完整本地启动验收**

Run: `npm --prefix frontend run build`  
Expected: TypeScript 与 Vite build 成功。随后运行 `python run.py --no-browser`，通过 `GET /plugins` 确认 SPA 页面可访问、`GET /api/health` 返回 `{"status":"ok"}`；在浏览器完成：导入 Skill ZIP、添加 MCP 配置、创建并加载本地 Python Tool、为智能体分别选择插件、自动保存改动、右侧调试并触发一次工具审批。

- [ ] **Step 3: 核对设计验收清单**

确认插件库无在线市场；四个 Tab 可用；Python 执行提示明确；Agent 工具在模型可见列表和执行路径均经过授权检查；调试记录退出页面即清空；费率表单消失且历史费用仍可见；ZIP/MCP/Tool/自动保存错误都有可见反馈。

## 执行约束

- 每一步先写失败测试，再实现最小改动，再运行该步指定测试；完成一个 Task 后确认该阶段测试通过，再进入下一 Task。
- 项目目录当前不是 Git 仓库，无法执行提交步骤；如后续在 Git 仓库中执行，再按项目约定分阶段提交。
- Python Tools 是明确由用户信任并加载的本机代码，不声称提供沙箱；ZIP 解压与工具授权必须按本计划测试，不能只依赖界面约束。
