# Windows PowerShell 默认执行与对话权限模式 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Windows 上让本机 Python Tool、生成代码和 Skill 脚本默认使用 PowerShell，并为主对话与调试对话增加浏览器持久化的“全部允许/用户审批”权限模式。

**Architecture:** 将审批模式作为每次聊天请求的协议字段，使用共享策略函数决定是否创建 pending approval；默认 `allow_all`，但保留工具选择、数据库授权、参数校验和资源限制。将工作区执行模式作为本机 PowerShell/本地 Docker 沙箱的唯一运行时选择，在工具调用上下文中传递执行模式；Windows 的本机路径统一进入受控 PowerShell 子进程，显式选择本地 Docker 沙箱时才进入容器。

**Tech Stack:** Python 3.11+, FastAPI/Pydantic, pytest/httpx, React 19 + TypeScript + Vite, `sessionStorage`, Windows PowerShell/pywin32 Job Object, Docker CLI/Docker Desktop。

---

## 文件与职责映射

### 新建

- `backend/app/runtime/approval_policy.py` — 审批模式类型、默认值和是否等待审批的纯策略函数。
- `tests/test_approval_mode.py` — 主会话和调试请求的审批模式行为测试。

### 修改后端

- `backend/app/routes/sessions.py` — `UserMessage.approval_mode`、主会话工具循环和 Skill 脚本执行路径。
- `backend/app/routes/debug.py` — `DebugRequest.approval_mode`、调试工具循环和 Skill 脚本执行路径。
- `backend/app/runtime/windows_tool.py` — 执行模式 ContextVar，并把工作目录上下文传给本机 Tool。
- `backend/app/runtime/windows_tool_worker.py` — 支持普通函数和旧版 `run(arguments)` Tool 的受控子进程入口。
- `backend/app/runtime/local_code.py` — 改为从 Docker/共享运行时常量模块读取大小限制。
- `backend/app/tool_plugins.py` — Windows PowerShell 不再只对静态只读表达式开放；按当前工作区执行模式选择 PowerShell 或 Docker 沙箱。
- `backend/app/runtime/sandbox_tools.py` — 统一生成代码和 Skill 脚本的本机/Docker 沙箱执行选择。
- `backend/app/runtime/docker_sandbox.py` — 通过本机 Docker CLI 创建临时、受限容器并执行代码。
- `backend/app/runtime/cube_sandbox.py` — 删除旧 CubeSandbox SDK 执行器，不能继续被生产路径导入。
- `backend/app/routes/workspaces.py` — 默认始终本机执行，显式选择本地 Docker 沙箱时验证配置。
- `backend/app/routes/sandbox.py` — 将配置 API 改为本地 Docker 镜像和资源限制。
- `backend/app/main.py` — 使用 `DockerSandboxExecutor` 初始化沙箱依赖。
- `pyproject.toml` — 删除 `cubesandbox` 依赖。
- `backend/app/routes/tools.py` — 删除或停用旧的静态 Windows trust 切换接口，避免旧接口继续暗示只有简单只读工具可走 PowerShell。
- `tests/test_windows_local_tools.py` — 更新 Windows 默认执行语义，增加复杂函数、旧格式和显式沙箱测试。
- `tests/test_sandbox_tools.py` — 增加生成代码默认本机和显式沙箱测试。
- `tests/test_session_workspaces.py` — 更新 `sandbox_code_enabled` 不再改变默认执行位置的断言。
- `tests/test_cube_sandbox.py` — 改为本地 Docker 执行器的容器、超时、输出和 fail-closed 测试。
- `tests/test_cube_sandbox_config.py` — 改为本地 Docker 镜像和资源配置 API 测试。
- `tests/test_chat_options.py` — 保留前端/请求兼容性相关测试并补充审批模式字段检查。

### 修改前端

- `frontend/src/ChatComposerOptions.tsx` — `ApprovalMode`/`ChatPreference` 类型、请求字段和权限下拉组件。
- `frontend/src/App.tsx` — 主对话读取/保存权限偏好、发送 `approval_mode`，保留用户审批弹窗。
- `frontend/src/pages/AgentDebugChat.tsx` — 调试对话的权限下拉、浏览器存储和请求字段。
- `frontend/src/components/WorkspacePicker.tsx` — 查询沙箱状态、禁用不可用的 Docker 沙箱、更新本机 PowerShell 文案。
- `frontend/src/components/WorkspacePicker.css` — 执行方式控件的布局样式。
- `frontend/src/pages/PluginLibraryPage.tsx` — 更新 Tool 和 Docker 沙箱 文案，去掉“只有简单只读函数可启用 PowerShell”的旧操作入口。
- `frontend/tests/shell.test.mjs` — 增加权限下拉、默认值和请求字段的静态检查。
- `tests/test_frontend_debug_chat.py` — 增加调试权限选择和 `approval_mode` 请求断言。
- `README.md` — 更新 Windows PowerShell 默认执行、显式 Docker 沙箱 选择和权限模式说明。

---

### Task 1: 建立共享审批模式策略

**Files:**
- Create: `backend/app/runtime/approval_policy.py`
- Create: `tests/test_approval_mode.py`
- Modify: `backend/app/routes/sessions.py`
- Modify: `backend/app/routes/debug.py`

- [ ] **Step 1: 写共享策略的失败测试**

在 `tests/test_approval_mode.py` 先写纯函数测试，固定协议值和 MCP 例外：

```python
from backend.app.runtime.approval_policy import (
    DEFAULT_APPROVAL_MODE,
    should_wait_for_approval,
)


def test_default_mode_is_allow_all():
    assert DEFAULT_APPROVAL_MODE == "allow_all"


def test_allow_all_skips_non_mcp_side_effect_approval():
    tool = {"risk": "side_effect", "plugin_id": "local-tool"}
    assert not should_wait_for_approval(tool, "allow_all")


def test_user_approval_waits_for_non_mcp_side_effect():
    tool = {"risk": "side_effect", "plugin_id": "local-tool"}
    assert should_wait_for_approval(tool, "user_approval")


def test_mcp_side_effect_never_waits_in_either_mode():
    tool = {"risk": "side_effect", "plugin_id": "mcp:server:write"}
    assert not should_wait_for_approval(tool, "allow_all")
    assert not should_wait_for_approval(tool, "user_approval")


def test_read_only_never_waits():
    tool = {"risk": "read_only", "plugin_id": "local-tool"}
    assert not should_wait_for_approval(tool, "allow_all")
    assert not should_wait_for_approval(tool, "user_approval")
```

- [ ] **Step 2: 运行测试确认 RED**

运行：

```bash
python -m pytest tests/test_approval_mode.py -q
```

预期：因 `backend.app.runtime.approval_policy` 尚不存在而失败。

- [ ] **Step 3: 实现最小共享策略**

创建 `backend/app/runtime/approval_policy.py`：

```python
from typing import Literal

from backend.app.tools import chat_tool_requires_approval

ApprovalMode = Literal["allow_all", "user_approval"]
DEFAULT_APPROVAL_MODE: ApprovalMode = "allow_all"


def should_wait_for_approval(tool: dict, mode: ApprovalMode) -> bool:
    return mode == "user_approval" and chat_tool_requires_approval(tool)
```

- [ ] **Step 4: 运行纯策略测试确认 GREEN**

运行：

```bash
python -m pytest tests/test_approval_mode.py -q
```

预期：`5 passed`。

- [ ] **Step 5: 在两个请求模型中加入协议字段**

在 `sessions.py` 的 `UserMessage` 和 `debug.py` 的 `DebugRequest` 中加入同一字段，默认值必须来自共享常量：

```python
from backend.app.runtime.approval_policy import ApprovalMode, DEFAULT_APPROVAL_MODE

class UserMessage(BaseModel):
    content: str = Field(min_length=1, max_length=100000)
    reuse_last_user: bool = False
    capabilities: ChatCapabilities | None = None
    model_config_id: str | None = Field(default=None, min_length=32, max_length=32)
    approval_mode: ApprovalMode = DEFAULT_APPROVAL_MODE
```

调试请求保持相同字段：

```python
class DebugRequest(BaseModel):
    config: DebugConfig
    messages: list[DebugMessage] = Field(min_length=1, max_length=40)
    workspace: WorkspaceChoice | None = None
    approval_mode: ApprovalMode = DEFAULT_APPROVAL_MODE
```

- [ ] **Step 6: 为主会话和调试循环接入共享策略**

将两个循环中现有的：

```python
approved = not chat_tool_requires_approval(definition)
if database_authorized and not approved:
```

改为：

```python
requires_approval = should_wait_for_approval(definition, payload.approval_mode)
approved = not requires_approval
if database_authorized and requires_approval:
```

仍保留后续 `database_authorized` 检查和 `definition["risk"] == "side_effect" and not approved` 防线。对于本来需要审批但当前模式为 `allow_all` 的工具，在跳过 pending approval 的分支发送审计轨迹：

```python
if chat_tool_requires_approval(definition) and payload.approval_mode == "allow_all":
    yield trace_frame(
        "approval_decision", "approved",
        f"工具 {tool_name} 已按“全部允许”自动批准",
    )
```

调试路由使用同样的逻辑和 `trace(...)` 函数。不要让 MCP 工具产生新的审批事件。

- [ ] **Step 7: 写主会话和调试端到端审批测试**

在 `tests/test_approval_mode.py` 使用现有 `create_app`、`create_test_agent_async`、`ToolDefinition`、`Secrets`、`sse` 和 `invoke` helper，注册一个 `side_effect` 工具；模型第一次返回该工具调用，第二次返回文本。先加入下面的完整测试夹具：

```python
async def side_effect_provider(request, calls, executions):
    body = json.loads(await request.aread())
    calls.append(body)
    if len(calls) == 1:
        return invoke("write_value", {"value": "ok"})
    return sse({"content": "done"})


def build_side_effect_app(tmp_path):
    calls, executions = [], []

    async def provider(request):
        return await side_effect_provider(request, calls, executions)

    app = create_app(
        tmp_path / "approval.sqlite3",
        data_dir=tmp_path / "data",
        secret_store=Secrets(),
        http_transport=httpx.MockTransport(provider),
    )
    app.state.tool_registry.register(ToolDefinition(
        "write_value", "Write a value", {"type": "object", "properties": {
            "value": {"type": "string"},
        }, "required": ["value"]}, "side_effect",
        lambda arguments: executions.append(arguments) or "ok",
    ))
    return app, calls, executions
```

然后加入下面三个完整行为测试。异步测试使用 `asyncio.create_task` 和 `ASGITransport`，因此不会调用真实模型或网络：

```python
def test_message_defaults_to_allow_all_and_does_not_create_pending_approval(tmp_path):
    async def run():
        app, calls, executions = build_side_effect_app(tmp_path)
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
                agent = (await create_test_agent_async(client, app, {"name": "Writer"})).json()
                session = (await client.post("/api/sessions", json={"agent_id": agent["id"]})).json()
                response = await client.post(
                    f"/api/sessions/{session['id']}/messages", json={"content": "write"}
                )
        assert response.status_code == 200
        assert "event: done" in response.text
        assert "event: approval_required" not in response.text
        assert executions == [{"value": "ok"}]
        assert len(calls) == 2

    asyncio.run(run())


def test_user_approval_pauses_main_message_until_decision(tmp_path):
    async def run():
        app, calls, executions = build_side_effect_app(tmp_path)
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
                agent = (await create_test_agent_async(client, app, {"name": "Writer"})).json()
                session = (await client.post("/api/sessions", json={"agent_id": agent["id"]})).json()
                task = asyncio.create_task(client.post(
                    f"/api/sessions/{session['id']}/messages",
                    json={"content": "write", "approval_mode": "user_approval"},
                ))
                for _ in range(200):
                    if app.state.pending_approvals:
                        break
                    await asyncio.sleep(0.01)
                assert app.state.pending_approvals
                assert executions == []
                approval_id = next(iter(app.state.pending_approvals))
                decision = await client.post(f"/api/approvals/{approval_id}", json={"approved": True})
                response = await task
        assert decision.status_code == 200
        assert response.status_code == 200
        assert "event: done" in response.text
        assert executions == [{"value": "ok"}]

    asyncio.run(run())


def test_debug_allow_all_does_not_create_pending_approval(tmp_path):
    async def run():
        app, calls, executions = build_side_effect_app(tmp_path)
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
                model_id = create_test_model(app)
                agent = (await client.post("/api/agents", json={
                    "name": "Debug Writer", "model_config_id": model_id,
                })).json()
                response = await client.post(f"/api/agents/{agent['id']}/debug", json={
                    "config": {"model_config_id": model_id},
                    "messages": [{"role": "user", "content": "write"}],
                    "approval_mode": "allow_all",
                })
        assert response.status_code == 200
        assert "event: done" in response.text
        assert "event: approval_required" not in response.text
        assert executions == [{"value": "ok"}]

    asyncio.run(run())
```

测试文件顶部必须导入 `asyncio`, `json`, `httpx`, `create_app`, `ToolDefinition`, `create_test_model`, `create_test_agent_async`, `Secrets`, `invoke` 和 `sse`。

- [ ] **Step 8: 运行审批相关回归测试**

运行：

```bash
python -m pytest tests/test_approval_mode.py tests/test_chat_options.py tests/test_sandbox_tools.py -q
```

预期：全部通过；若旧测试默认期待审批，更新它们明确发送 `approval_mode="user_approval"`，而不是改变新的默认值。

---

### Task 2: 统一 Windows PowerShell 本机执行

**Files:**
- Modify: `backend/app/runtime/windows_tool.py`
- Modify: `backend/app/runtime/windows_tool_worker.py`
- Modify: `backend/app/tool_plugins.py`
- Modify: `backend/app/runtime/sandbox_tools.py`
- Modify: `backend/app/routes/sessions.py`
- Modify: `backend/app/routes/debug.py`
- Modify: `tests/test_windows_local_tools.py`
- Modify: `tests/test_sandbox_tools.py`

- [ ] **Step 1: 为执行上下文写失败测试**

在 `tests/test_windows_local_tools.py` 增加 Windows-only 测试，覆盖复杂普通函数和旧格式 Tool。测试顶部先定义两个源码样本：

```python
COMPLEX_SOURCE = '''def uppercase(value: str) -> str:
    """Convert a value to uppercase.

    Args:
        value: Text to convert.
    """
    return value.upper()
'''

LEGACY_SOURCE = '''TOOL = {"name": "legacy", "description": "Legacy tool", "input_schema": {
    "type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"]
}, "risk": "side_effect"}

def run(arguments):
    return arguments["value"]
'''
```

测试核心断言为：

```python
@pytest.mark.skipif(os.name != "nt", reason="Requires native Windows PowerShell")
def test_complex_and_legacy_tools_default_to_windows_local(tmp_path):
    app = config(tmp_path)
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        complex_tool = client.post("/api/tools", json={"source": COMPLEX_SOURCE}).json()
        legacy_tool = client.post("/api/tools", json={"source": LEGACY_SOURCE}).json()
        assert complex_tool["execution_mode"] == "windows_local"
        assert complex_tool["windows_local_available"] is True
        assert legacy_tool["execution_mode"] == "windows_local"
```

同时增加显式沙箱执行上下文测试：`use_tool_workspace(path, execution_mode="sandbox")` 时，Windows Tool 不调用 PowerShell；未配置 Docker 沙箱时返回 `DockerSandboxUnavailable`。

- [ ] **Step 2: 运行 Windows 执行测试确认 RED**

在 Windows 上运行：

```bash
python -m pytest tests/test_windows_local_tools.py -q
```

预期：复杂 Tool 仍显示 `sandbox` 或执行失败，证明旧静态限制尚未移除。非 Windows 环境只运行不带 `nt` skip 的回归测试。

- [ ] **Step 3: 扩展工作区 ContextVar**

在 `windows_tool.py` 增加执行模式上下文，并保持旧调用方可用：

```python
from contextvars import ContextVar
from typing import Literal

ExecutionMode = Literal["local", "sandbox"]
tool_workspace: ContextVar[str | None] = ContextVar("tool_workspace", default=None)
tool_execution_mode: ContextVar[ExecutionMode | None] = ContextVar("tool_execution_mode", default=None)

@contextmanager
def use_tool_workspace(path: str | None, execution_mode: ExecutionMode | None = None):
    path_token = tool_workspace.set(path)
    mode_token = tool_execution_mode.set(execution_mode)
    try:
        yield
    finally:
        tool_execution_mode.reset(mode_token)
        tool_workspace.reset(path_token)
```

默认 `None` 在 Windows 上解释为本机 PowerShell，在非 Windows 上解释为已有沙箱路径，避免委派任务和旧调用方意外改变非 Windows 行为。

- [ ] **Step 4: 让 Windows worker 支持普通函数和旧版 Tool 入口**

当前 `windows_tool_worker.py` 只解释纯表达式，不能满足“所有 Tool 默认走 PowerShell”。改为在隔离的固定子进程中执行完整源码；当请求带 `function_name` 时调用该函数并按关键字参数传值，当没有 `function_name` 时调用旧版 `run(arguments)`：

```python
import asyncio
import inspect


def execute(payload):
    scope = {"__name__": "__main__", "__file__": "<python-tool>"}
    exec(compile(payload["source"], "<python-tool>", "exec"), scope)
    if payload.get("function_name"):
        entry = scope.get(payload["function_name"])
        if not callable(entry):
            raise ValueError("Python Tool 函数不存在")
        result = entry(**payload["arguments"])
    else:
        entry = scope.get("run")
        if not callable(entry):
            raise ValueError("Python Tool 必须定义 run(arguments)")
        result = entry(payload["arguments"])
    if inspect.isawaitable(result):
        result = asyncio.run(result)
    return result
```

文件仍通过 `python -I -B -X utf8` 启动，源码和参数通过 stdin JSON 传递；不要把源码拼进 PowerShell `-Command` 字符串。PowerShell 不是安全沙箱，因此前端和 README 必须明确警告。

- [ ] **Step 5: 修改 LocalToolManager 的运行时选择**

在 `tool_plugins.py` 中移除“只有 `function_name is not None and risk == read_only` 才能本机运行”的静态条件。`windows_local_available` 改为 Windows executor 实际可用；handler 按上下文选择：

```python
mode = tool_execution_mode.get()
if mode == "sandbox":
    if self.sandbox_executor is None:
        raise DockerSandboxUnavailable("Docker 沙箱未配置，无法执行已选择的沙箱 Tool")
    result = self.sandbox_executor(source, arguments, **sandbox_kwargs)
    return await result if inspect.isawaitable(result) else result

if self.windows_executor is not None and self.windows_executor.available:
    return await self.windows_executor.execute(
        source, arguments, function_name=function_name,
        cwd=Path(tool_workspace.get()) if tool_workspace.get() else None,
    )

# 非 Windows 保持现有 Docker 沙箱 路径；没有可用沙箱时继续 fail closed。
return await run_in_sandbox(arguments)
```

旧 `.windows-sandbox` 标记不再决定运行环境；它是历史静态信任机制，保留文件但忽略其运行时效果，防止旧配置把未明确选择的聊天请求切到沙箱。删除/停用 `windows-trust` UI 和 API 的切换语义，避免界面与新策略冲突。

- [ ] **Step 6: 统一 Skill 脚本执行路径**

在 `sandbox_tools.py` 增加：

```python
async def execute_skill_script(state, workspace: dict, source: str, arguments: dict):
    if workspace["execution_mode"] == "sandbox":
        return await state.sandbox_executor.execute_python_async(source, arguments)
    if os.name == "nt":
        from backend.app.routes.workspaces import workspace_path
        return await state.local_code_executor.execute(
            source, arguments, cwd=workspace_path(workspace["path"]),
        )
    return await state.sandbox_executor.execute_python_async(source, arguments)
```

替换 `sessions.py` 和 `debug.py` 中 Skill `script_tool` 分支对 `sandbox_executor.execute_python_async` 的直接调用。生成代码继续由 `execute_generated_code` 根据工作区执行模式选择 `LocalCodeExecutor` 或 `DockerSandboxExecutor`。

- [ ] **Step 7: 将本地 Tool 调用传入执行模式**

把两个路由中的：

```python
with use_tool_workspace(workspace['path'] if workspace['execution_mode'] == 'local' else None):
```

改成：

```python
with use_tool_workspace(
    workspace["path"] if workspace["execution_mode"] == "local" else None,
    execution_mode=workspace["execution_mode"],
):
```

这样显式选择 `sandbox` 的本地 Python Tool 与生成代码共享同一执行位置策略。

- [ ] **Step 8: 运行 Windows 和沙箱测试**

运行：

```bash
python -m pytest tests/test_windows_local_tools.py tests/test_sandbox_tools.py tests/test_tool_plugins.py -q
```

预期：Windows 上普通函数、复杂函数、旧格式 Tool 和生成代码默认使用 PowerShell；显式 sandbox 只调用 Docker 沙箱；非 Windows 旧沙箱行为通过。

---

### Task 3: 实现本地 Docker 沙箱

**Files:**
- Create: `backend/app/runtime/docker_sandbox.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/routes/sandbox.py`
- Modify: `backend/app/runtime/sandbox_tools.py`
- Modify: `pyproject.toml`
- Modify: `tests/test_cube_sandbox.py`
- Modify: `tests/test_cube_sandbox_config.py`
- Delete: `backend/app/runtime/cube_sandbox.py`

- [ ] **Step 1: 写 Docker 设置和执行器的失败测试**

在现有沙箱测试中替换 CubeSandbox 设置断言，新增本地配置字段和执行器测试。执行器必须接受可注入的 `run_process`/子进程工厂，单元测试不得依赖开发机是否安装 Docker。测试至少固定以下行为：

```python
def test_docker_settings_require_local_image_and_valid_limits():
    settings = DockerSandboxSettings.from_value({
        "enabled": True,
        "image": "python:3.11-slim",
        "timeout_seconds": 30,
        "memory_mb": 512,
        "cpu_count": 1,
        "network_enabled": False,
    })
    assert settings.image == "python:3.11-slim"
    with pytest.raises(DockerSandboxUnavailable):
        DockerSandboxSettings.from_value({"enabled": True, "image": "", "timeout_seconds": 30,
                                          "memory_mb": 512, "cpu_count": 1,
                                          "network_enabled": False})


def test_docker_executor_never_falls_back_when_unavailable():
    executor = DockerSandboxExecutor(lambda: None, docker_path=None)
    with pytest.raises(DockerSandboxUnavailable):
        asyncio.run(executor.execute_python_async("def run(arguments): return arguments", {}))
```

再测试命令构造包含 `--rm`, `--read-only`, `--cap-drop=ALL`, `--security-opt=no-new-privileges`, `--network none`, `--memory`, `--cpus` 和 `--pids-limit`，且用户源码只出现在 stdin payload，不出现在 argv。

- [ ] **Step 2: 运行测试确认 RED**

运行：

```bash
python -m pytest tests/test_cube_sandbox.py tests/test_cube_sandbox_config.py -q
```

预期：因新的 Docker 设置/执行器和配置字段尚未实现而失败。

- [ ] **Step 3: 实现 Docker 设置和错误类型**

创建 `backend/app/runtime/docker_sandbox.py`，定义配置、脱敏错误和共享限制常量 `MAX_CODE_BYTES`, `MAX_ARGUMENT_BYTES`, `MAX_OUTPUT_BYTES`；

```python
@dataclass(frozen=True)
class DockerSandboxSettings:
    image: str
    timeout_seconds: int = 30
    memory_mb: int = 512
    cpu_count: int = 1
    network_enabled: bool = False
    enabled: bool = True

    @classmethod
    def from_value(cls, value: dict[str, Any] | None) -> "DockerSandboxSettings":
        if not value or not value.get("enabled"):
            raise DockerSandboxUnavailable("本地 Docker 沙箱未启用")
        image = value.get("image")
        if not isinstance(image, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/@:-]{0,255}", image.strip()):
            raise DockerSandboxUnavailable("Docker 镜像名无效")
        timeout = value.get("timeout_seconds", 30)
        memory = value.get("memory_mb", 512)
        cpu = value.get("cpu_count", 1)
        if not isinstance(timeout, int) or not 1 <= timeout <= 300:
            raise DockerSandboxUnavailable("Docker 沙箱超时必须在 1 到 300 秒之间")
        if not isinstance(memory, int) or not 128 <= memory <= 8192:
            raise DockerSandboxUnavailable("Docker 沙箱内存必须在 128 到 8192 MB 之间")
        if not isinstance(cpu, int) or not 1 <= cpu <= 8:
            raise DockerSandboxUnavailable("Docker 沙箱 CPU 数必须在 1 到 8 之间")
        network_enabled = value.get("network_enabled", False)
        if not isinstance(network_enabled, bool):
            raise DockerSandboxUnavailable("Docker 沙箱网络设置无效")
        return cls(image.strip(), timeout, memory, cpu, network_enabled, True)
```

`DockerSandboxUnavailable` 的错误消息不得包含 Docker 环境变量、凭据、完整命令行或容器输出。

- [ ] **Step 4: 实现本机 Docker CLI 执行器**

`DockerSandboxExecutor.execute_python_async` 使用 `asyncio.create_subprocess_exec` 调用本机 Docker CLI。固定容器参数如下：

```python
args = [
    docker_path, "run", "--rm", "-i", "--init",
    "--read-only", "--cap-drop=ALL",
    "--security-opt=no-new-privileges",
    "--pids-limit", "128", "--memory", f"{settings.memory_mb}m",
    "--cpus", str(settings.cpu_count),
]
if not settings.network_enabled:
    args.extend(["--network", "none"])
args.extend([settings.image, "python", "-I", "-B", "-c", FIXED_WORKER])
```

`FIXED_WORKER` 只从 stdin 读取 `{source, arguments, function_name}` JSON，在容器内执行普通函数或 `run(arguments)`，输出单行 JSON。源码和参数通过 stdin 写入，不拼接到 argv；stdout/stderr 读取超过 256 KiB 立即终止。使用 `asyncio.timeout(settings.timeout_seconds)`，超时/取消时关闭 stdin、终止 Docker CLI 子进程并执行容器清理。镜像不存在、Docker Engine 未运行或非零退出统一转换成 `DockerSandboxUnavailable`。

提供：

```python
def is_configured(self) -> bool:
    try:
        settings = DockerSandboxSettings.from_value(self.settings_provider())
        return self.docker_path is not None and self.image_exists(settings.image)
    except DockerSandboxUnavailable:
        return False
```

`image_exists` 只执行本机 `docker image inspect image`，不执行 `docker pull`，也不访问远程 Docker API。

- [ ] **Step 5: 替换应用依赖和设置 API**

在 `main.py` 用 `DockerSandboxExecutor` 替换旧 `SandboxExecutor`，保留 `application.state.sandbox_executor` 这个内部属性名以减少路由改动；删除 `cube_sandbox.py` 导入和 `pyproject.toml` 的 `cubesandbox==0.8.0` 依赖。

在 `routes/sandbox.py` 将 Pydantic 配置改成：

```python
class SandboxConfigPayload(BaseModel):
    enabled: bool = False
    image: str = Field(default="python:3.11-slim", min_length=1, max_length=256)
    timeout_seconds: int = Field(default=30, ge=1, le=300)
    memory_mb: int = Field(default=512, ge=128, le=8192)
    cpu_count: int = Field(default=1, ge=1, le=8)
    network_enabled: bool = False
```

`GET /api/sandbox` 返回上述字段和 `configured`；不再读取/返回 `api_url`、`template_id`、`key_configured` 或任何密钥。`PUT /api/sandbox` 只保存 `app_settings` 中的 Docker 配置；启用时调用 `is_configured()`，镜像不存在返回 422/409，并且绝不自动拉取。

- [ ] **Step 6: 统一沙箱调用接口**

保持 `sandbox_tools.py`、`sessions.py` 和 `debug.py` 使用 `state.sandbox_executor.execute_python_async` 的调用形状，但将类型/错误捕获从旧 SDK 改为 `DockerSandboxUnavailable`。删除所有 CubeSandbox SDK 特有的 `files.write`、Template、API URL 和网络段配置逻辑。

- [ ] **Step 7: 运行 Docker 沙箱和配置测试**

运行：

```bash
python -m pytest tests/test_cube_sandbox.py tests/test_cube_sandbox_config.py tests/test_session_workspaces.py -q
```

预期：配置字段、镜像本地存在检查、Docker CLI 参数、安全限制、超时清理、显式沙箱 fail closed 和工作区默认 local 全部通过；测试不要求远程服务。

---

### Task 4: 固定工作区默认值并校验显式 Docker 沙箱

**Files:**
- Modify: `backend/app/runtime/docker_sandbox.py`
- Modify: `backend/app/routes/workspaces.py`
- Modify: `tests/test_session_workspaces.py`
- Modify: `tests/test_cube_sandbox.py`
- Modify: `tests/test_cube_sandbox_config.py`

- [ ] **Step 1: 写默认值和不可用沙箱的失败测试**

在 `tests/test_session_workspaces.py` 增加（并在文件顶部导入 `create_test_agent`）：

```python
def test_agent_code_enabled_still_defaults_to_local(tmp_path):
    app = create_app(tmp_path / "workspace.sqlite3", data_dir=tmp_path / "data")
    with TestClient(app) as client:
        agent = create_test_agent(client, {"name": "Code", "sandbox_code_enabled": True}).json()
        session = client.post("/api/sessions", json={"agent_id": agent["id"]}).json()
        assert session["workspace"]["execution_mode"] == "local"
```

再增加：

```python
def test_explicit_sandbox_requires_valid_configuration(tmp_path):
    app = create_app(tmp_path / "workspace.sqlite3", data_dir=tmp_path / "data")
    with TestClient(app) as client:
        session = client.post("/api/sessions", json={}).json()
        response = client.put(
            f"/api/sessions/{session['id']}/workspace",
            json={"group_id": None, "execution_mode": "sandbox"},
        )
        assert response.status_code == 409
        assert "Docker 沙箱" in response.json()["detail"]
```

- [ ] **Step 2: 运行测试确认 RED**

运行：

```bash
python -m pytest tests/test_session_workspaces.py -q
```

预期：启用 Agent 代码时仍返回 `sandbox`，或显式沙箱选择没有被拒绝。

- [ ] **Step 3: 将 Docker 配置有效性接入工作区**

使用 Task 3 实现的 `DockerSandboxExecutor.is_configured()`；该方法只检查本机 Docker CLI、启用配置和本地镜像，不连接远程服务。

- [ ] **Step 4: 修改工作区默认和显式选择校验**

在 `session_workspace` 和 `debug_workspace`：

1. `choice is None` 时始终设置 `mode = "local"`，不再依据 `sandbox_code_enabled` 自动选择 `sandbox`。
2. `choice.execution_mode == "sandbox"` 时调用 `request.app.state.sandbox_executor.is_configured()`；失败则抛出 `HTTPException(409, "Docker 沙箱 尚未配置或配置不可用，请改选本机执行或先配置 Docker 沙箱")`。
3. 旧会话保存的 `sandbox` 在读取/执行时同样验证，不可用时 fail closed，不改写数据库为 local。

- [ ] **Step 5: 运行工作区和沙箱测试确认 GREEN**

运行：

```bash
python -m pytest tests/test_session_workspaces.py tests/test_cube_sandbox.py tests/test_cube_sandbox_config.py -q
```

预期：默认本机、显式有效沙箱、显式无效沙箱和旧配置行为均符合断言。

---

### Task 5: 主对话权限下拉和浏览器保存

**Files:**
- Modify: `frontend/src/ChatComposerOptions.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/chatComposerOptions.css`
- Modify: `frontend/tests/shell.test.mjs`

- [ ] **Step 1: 写前端静态失败测试**

在 `frontend/tests/shell.test.mjs` 增加对源码的检查：

```js
const composer = read('src/ChatComposerOptions.tsx');
const app = read('src/App.tsx');
assert.match(composer, /ApprovalMode/);
assert.match(composer, /allow_all/);
assert.match(composer, /user_approval/);
assert.match(app, /approval_mode/);
assert.match(app, /sessionStorage/);
```

- [ ] **Step 2: 运行前端测试确认 RED**

运行现有静态测试脚本：

```bash
node frontend/tests/shell.test.mjs
```

预期：新增权限断言失败。不要在本任务中引入新的测试框架或 npm script。

- [ ] **Step 3: 扩展 ChatPreference 和请求构造**

在 `ChatComposerOptions.tsx` 加入：

```ts
export type ApprovalMode = 'allow_all' | 'user_approval';
export const DEFAULT_APPROVAL_MODE: ApprovalMode = 'allow_all';
export type ChatPreference = {
  capabilities?: CapabilitySelection;
  model?: Pick<ModelOption, 'id' | 'name'>;
  approval_mode?: ApprovalMode;
};

export function chatRequestOptions(
  selection: CapabilitySelection,
  model?: ChatPreference['model'],
  approvalMode: ApprovalMode = DEFAULT_APPROVAL_MODE,
) {
  return {
    capabilities: Object.fromEntries(kinds.map((kind) => [kind, selection[kind].map((item) => item.id)])),
    approval_mode: approvalMode,
    ...(model ? { model_config_id: model.id } : {}),
  };
}
```

增加原生组件：

```tsx
export function ApprovalModePicker({ value, onChange, disabled }: {
  value: ApprovalMode;
  onChange: (value: ApprovalMode) => void;
  disabled: boolean;
}) {
  return <label className="approval-mode-picker">
    <span>执行权限</span>
    <select aria-label="工具执行权限" value={value} disabled={disabled}
      onChange={(event) => onChange(event.target.value as ApprovalMode)}>
      <option value="allow_all">全部允许</option>
      <option value="user_approval">用户审批</option>
    </select>
  </label>;
}
```

- [ ] **Step 4: 在 App 中按对话读取/保存权限并发送请求**

在 `App.tsx`：

```tsx
const approvalMode = preference?.approval_mode ?? DEFAULT_APPROVAL_MODE;
const sendOptions = chatRequestOptions(capabilitySelection, preference?.model, approvalMode);
```

`updateComposer` 继续写入现有 `composerByChat`，因此 `useChatTabState` 会把选择保存到当前对话 key 的 `sessionStorage`。旧值缺少字段时显示 `allow_all`。

在 composer 工具区插入：

```tsx
<ApprovalModePicker value={approvalMode}
  onChange={(approval_mode) => updateComposer({ approval_mode })}
  disabled={busy || creatingSession}/>
```

不要修改现有 `pendingApproval` modal；`user_approval` 仍由服务端事件触发该 modal，`allow_all` 不应产生事件。

- [ ] **Step 5: 添加样式并运行类型检查**

在已有 composer 样式中加入紧凑、可聚焦的下拉布局：

```css
.approval-mode-picker {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
}

.approval-mode-picker span {
  color: #526457;
  font-size: 0.75rem;
}
```

运行：

```bash
npm --prefix frontend run typecheck
```

预期：通过。

---

### Task 6: Agent 调试对话权限和执行位置 UI

**Files:**
- Modify: `frontend/src/pages/AgentDebugChat.tsx`
- Modify: `frontend/src/components/WorkspacePicker.tsx`
- Modify: `frontend/src/components/WorkspacePicker.css`
- Modify: `frontend/tests/test_frontend_debug_chat.py`

- [ ] **Step 1: 写调试页面静态失败测试**

在 `tests/test_frontend_debug_chat.py` 增加：

```python
def test_debug_chat_has_persisted_approval_mode_and_sends_it():
    source = (FRONTEND / "pages" / "AgentDebugChat.tsx").read_text(encoding="utf-8")
    assert "allow_all" in source
    assert "user_approval" in source
    assert "approval_mode" in source
    assert "sessionStorage" in source
```

- [ ] **Step 2: 在调试页加入浏览器存储和下拉框**

在 `AgentDebugChat.tsx` 引入 `ApprovalMode` 和默认值；使用 Agent 级别独立 key：

```tsx
const approvalStorageKey = `agent-harness:debug-approval-mode:${agentId}`;
const [approvalMode, setApprovalMode] = useState<ApprovalMode>(() => {
  try {
    const value = sessionStorage.getItem(approvalStorageKey);
    return value === 'user_approval' ? 'user_approval' : DEFAULT_APPROVAL_MODE;
  } catch { return DEFAULT_APPROVAL_MODE; }
});
useEffect(() => {
  try { sessionStorage.setItem(approvalStorageKey, approvalMode); } catch { /* 不影响发送 */ }
}, [approvalStorageKey, approvalMode]);
```

调试请求 body 增加：

```tsx
body: JSON.stringify({
  config,
  workspace: workspaceChoice,
  approval_mode: approvalMode,
  messages: history.map(({ role, content }) => ({ role, content })),
})
```

在调试表单或 WorkspacePicker 旁边渲染与主对话相同的两个 option，并在 busy 时禁用。

- [ ] **Step 3: 让 WorkspacePicker 识别沙箱是否可用**

增加状态类型和加载请求：

```tsx
type SandboxStatus = { configured: boolean };
const [sandboxConfigured, setSandboxConfigured] = useState(false);

useEffect(() => {
  const controller = new AbortController();
  api<SandboxStatus>('/sandbox', { signal: controller.signal })
    .then((value) => { if (!controller.signal.aborted) setSandboxConfigured(value.configured); })
    .catch((error: Error) => { if (!controller.signal.aborted) onError(error.message); });
  return () => controller.abort();
}, [onError]);
```

执行方式下拉改为：

```tsx
<select aria-label="代码运行位置" value={choice.execution_mode}
  disabled={disabled || busy || loadingWorkspace}
  onChange={(event) => void apply({ ...choice, execution_mode: event.target.value as WorkspaceChoice['execution_mode'] })}>
  <option value="local">本机执行（Windows 使用 PowerShell）</option>
  <option value="sandbox" disabled={!sandboxConfigured}>Docker 沙箱{sandboxConfigured ? '' : '（未配置）'}</option>
</select>
```

当服务端返回不可用沙箱错误时，保留当前选择不执行，并显示错误；不要自动改成 local 后继续执行。

- [ ] **Step 4: 更新执行位置提示和前端测试**

将“本机代码需逐次确认”改成与权限模式一致的说明，例如“本机 PowerShell 不是安全沙箱；权限由下方执行权限控制”。运行：

```bash
python -m pytest tests/test_frontend_debug_chat.py -q
npm --prefix frontend run typecheck
```

预期：测试和类型检查通过。

---

### Task 7: 移除旧 Tool 静态信任入口并更新产品文案

**Files:**
- Modify: `backend/app/tool_plugins.py`
- Modify: `backend/app/routes/tools.py`
- Modify: `frontend/src/pages/PluginLibraryPage.tsx`
- Modify: `README.md`
- Modify: `tests/test_windows_local_tools.py`
- Modify: `tests/test_tool_failure_messages.py`

- [ ] **Step 1: 更新后端 Tool 列表语义测试**

将原先只断言简单只读 Tool 默认 `windows_local` 的测试扩展为：普通函数、复杂函数和旧 `TOOL + run` 在 Windows PowerShell 可用时均为 `windows_local`；PowerShell 不可用时才报告 `sandbox`/不可用。删除依赖 `.windows-sandbox` 文件改变运行环境的旧断言，改为验证工作区执行模式决定路径。

- [ ] **Step 2: 删除旧的静态限制接口**

从 `routes/tools.py` 移除 `/windows-trust` 的 PUT/DELETE 路由和本机 trust 专用校验；从 `LocalToolManager` 移除 `_sandbox_path`、`_sandbox_selected` 和 `set_windows_trust` 的运行时分支。删除工具时仍清理历史 `.windows-sandbox` 和 `.windows-trust` 文件，避免遗留文件堆积。

若需要兼容旧客户端，接口可保留并返回 410：

```python
raise HTTPException(
    status_code=410,
    detail="Tool 执行方式已由对话中的本机 PowerShell/Docker 沙箱 选择控制",
)
```

不得让旧接口继续改变实际执行环境。

- [ ] **Step 3: 更新 Tool 页面**

删除 `setWindowsTrust`、切换按钮和“仅静态验证简单只读函数”的说明。改成：

```tsx
<div className="plugin-security-warning">
  Windows 上本地 Python Tool 默认通过 PowerShell 子进程执行，PowerShell 不是安全沙箱；在对话中明确选择 Docker 沙箱 后才使用本地容器。执行权限可在对话框中选择“全部允许”或“用户审批”。
</div>
```

Tool 卡片展示 `Windows PowerShell · 默认本机执行` 或 `Docker 沙箱 · 由对话执行位置选择`，不再展示可启用/撤销的静态信任按钮。

- [ ] **Step 4: 更新插件库的本地 Docker 配置页**

在 `PluginLibraryPage.tsx` 将 `SandboxSettings` 类型和初始状态改成：

```ts
type SandboxSettings = {
  enabled: boolean;
  image: string;
  timeout_seconds: number;
  memory_mb: number;
  cpu_count: number;
  network_enabled: boolean;
  configured: boolean;
};
```

删除 API URL、Template ID、API Key、`key_configured` 和远程测试逻辑；表单只保留镜像名、启用、超时、内存、CPU、网络开关和“测试本机 Docker”。保存请求只发送这些字段：

```tsx
await api('/sandbox', {
  method: 'PUT',
  body: JSON.stringify({
    enabled: sandbox.enabled,
    image: sandbox.image,
    timeout_seconds: sandbox.timeout_seconds,
    memory_mb: sandbox.memory_mb,
    cpu_count: sandbox.cpu_count,
    network_enabled: sandbox.network_enabled,
  }),
});
```

页面文案明确“只使用本机 Docker Engine，不自动拉取镜像，不连接远程 Docker”。保存/测试失败时展示 Docker CLI、Engine 或本地镜像缺失的可操作提示。

- [ ] **Step 5: 更新 README 和失败信息**

在 README 的 Windows 本机执行、生成代码和开发说明中明确：

- Windows 默认 PowerShell 适用于所有本地 Python Tool、生成代码和 Skill 脚本；
- PowerShell 不是沙箱；
- Docker 沙箱必须由用户在对话运行位置中明确选择；
- Docker 沙箱使用本机 Docker Engine，不配置远程 Docker，不自动拉取镜像；
- 权限下拉默认“全部允许”，切换为“用户审批”后副作用工具逐次等待确认；
- Docker 沙箱未配置时不会把显式沙箱选择静默回退到本机。

- [ ] **Step 6: 运行 Tool 回归测试**

运行：

```bash
python -m pytest tests/test_windows_local_tools.py tests/test_tool_failure_messages.py tests/test_tool_plugins.py -q
```

预期：Windows 默认 PowerShell、显式沙箱 fail closed、非 Windows 既有行为均通过。

---

### Task 8: 全量验证与发布前检查

**Files:**
- `frontend/tests/shell.test.mjs`
- `README.md`

- [ ] **Step 1: 运行后端全量测试**

```bash
python -m pytest -q
```

预期：全部测试通过。若存在依赖 Windows 的 skip，只记录 skip 数量，不把 skip 当作通过 Windows 行为的证据。

- [ ] **Step 2: 运行前端类型检查和构建**

```bash
npm --prefix frontend run typecheck
npm --prefix frontend run build
```

预期：TypeScript 无错误，Vite 构建成功。

- [ ] **Step 3: 运行前端静态测试**

```bash
node frontend/tests/shell.test.mjs
```

预期：权限模式、默认值、请求字段、调试页和工作区文案断言通过。

- [ ] **Step 4: 在 Windows 做真实运行冒烟验证**

启动后端和前端，验证：

1. 未配置 Docker 沙箱 时，WorkspacePicker 默认显示本机 PowerShell，Docker 沙箱 不可选。
2. 启用一个本地 Python Tool，模型触发复杂 Tool，确认 PowerShell 子进程执行而非 `DockerSandboxUnavailable`。
3. 启用生成代码，确认默认本机 PowerShell 执行。
4. 权限为“全部允许”时执行副作用 Tool，确认没有 `approval_required` 事件和审批弹窗。
5. 切换为“用户审批”，确认同一个 Tool 暂停并显示审批框，批准后继续，拒绝后不执行。
6. 配置并明确选择 Docker 沙箱，确认生成代码和 Skill 脚本进入沙箱；本地 Tool 也按工作区模式进入沙箱。
7. 刷新浏览器并切换会话，确认每个会话保留自己的权限选择。

- [ ] **Step 5: 检查差异和敏感信息**

运行：

```bash
git diff --check
git status --short
```

确认没有凭据、临时源码、Docker Socket 挂载、沙箱 API Key 或测试数据库被加入变更；只报告实际运行结果，不在未执行验证前声称完成。
