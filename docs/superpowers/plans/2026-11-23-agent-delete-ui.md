# 本地智能体删除入口实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在“模型及插件库 → Agent”和“智能体列表页”增加安全、可反馈的本地智能体删除入口，并在删除当前智能体后正确更新选择状态和路由。

**Architecture:** 复用现有 `DELETE /api/agents/{agent_id}` 后端接口，不修改数据库结构。`App.tsx` 负责全局 Agent、当前选择、会话和路由协调；`PluginLibraryPage` 通过 `onAgentDeleted` 回调通知 App，再刷新自身 Agent 数据；`AgentListPage` 也通过回调把删除动作交给 `App.tsx`。两页共享一致的确认文案，删除失败使用现有错误提示机制。

**Tech Stack:** React 19、TypeScript、React Router、现有 `api()` 封装、FastAPI 既有 Agent 删除接口、pytest 源码契约测试、Vite。

---

## 文件结构与职责

- 修改 `frontend/src/pages/AgentListPage.tsx`：扩展页面 props，渲染危险删除按钮并触发删除回调。
- 修改 `frontend/src/pages/PluginLibraryPage.tsx`：为 Agent 卡片增加删除处理和危险操作按钮，并通过回调通知 App。
- 修改 `frontend/src/App.tsx`：实现共享删除请求、确认文案、全局列表/会话/当前 Agent 更新和路由切换，并把回调传给 Agent 列表页。
- 修改或新增 `tests/test_frontend_agent_list.py`：验证列表页删除入口、回调和影响提示。
- 修改或新增 `tests/test_frontend_plugin_library.py`：验证插件库 Agent 删除入口、API 请求和影响提示。
- 最终不修改 `backend/app/routes/agents.py`，因为 `DELETE /api/agents/{agent_id}` 已存在且外键级联行为已覆盖后端测试。

## Task 1: 先写两个页面的失败契约测试

**Files:**
- Modify: `tests/test_frontend_agent_list.py`
- Modify: `tests/test_frontend_plugin_library.py`

- [ ] **Step 1: 检查现有测试结构并补充最小失败断言**

在 Agent 列表测试中读取 `frontend/src/pages/AgentListPage.tsx`，增加以下行为断言：

```python

def test_agent_list_exposes_delete_action_and_cascade_warning():
    source = (FRONTEND / "pages" / "AgentListPage.tsx").read_text(encoding="utf-8")
    assert "onDelete" in source
    assert "删除" in source
    assert "会话" in source and "消息" in source
    assert "专属记忆" in source and "Prompt" in source
```

在插件库测试中读取 `frontend/src/pages/PluginLibraryPage.tsx`，增加：

```python

def test_plugin_library_agent_cards_expose_delete_action_and_delete_api():
    source = (FRONTEND / "pages" / "PluginLibraryPage.tsx").read_text(encoding="utf-8")
    assert "deleteAgent" in source
    assert "onAgentDeleted" in source
    assert "/agents/" in source
    assert "method: 'DELETE'" in source
    assert "会话" in source and "消息" in source
    assert "专属记忆" in source and "Prompt" in source
```

如果测试文件不存在，按当前项目测试风格创建文件并保留 `FRONTEND = Path(__file__).parents[1] / "frontend" / "src"`。

- [ ] **Step 2: 运行新增测试，确认它们因缺少删除入口而失败**

运行：

```bash
python -m pytest tests/test_frontend_agent_list.py tests/test_frontend_plugin_library.py -q
```

预期：新增断言失败，原因是当前 Agent 卡片没有删除入口，而不是测试收集或编码错误。

## Task 2: 实现 Agent 列表页删除按钮和回调

**Files:**
- Modify: `frontend/src/pages/AgentListPage.tsx`

- [ ] **Step 1: 扩展 props 类型**

在 `AgentListPageProps` 中加入：

```ts
onDelete: (agent: AgentListItem) => void;
```

在组件参数中接收 `onDelete`。

- [ ] **Step 2: 在每个卡片操作区增加危险删除按钮**

保留现有“配置”和“使用”按钮，在同一操作区追加：

```tsx
<button
  type="button"
  className="danger-button"
  onClick={() => onDelete(agent)}
>
  删除
</button>
```

按钮只负责触发回调，不在展示组件中直接调用 API，确保删除状态由 `App.tsx` 统一管理。

- [ ] **Step 3: 运行 Agent 列表契约测试**

运行：

```bash
python -m pytest tests/test_frontend_agent_list.py -q
```

预期：Agent 列表删除相关测试通过。

## Task 3: 实现插件库 Agent 删除操作

**Files:**
- Modify: `frontend/src/pages/PluginLibraryPage.tsx`
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: 增加删除状态和 App 回调 prop**

将插件库组件 props 扩展为：

```ts
type PluginLibraryPageProps = {
  onAgentDeleted: (agentId: string) => void;
};
```

组件接收 `onAgentDeleted`，并增加：

```ts
const [deletingAgentId, setDeletingAgentId] = useState('');
```

在 `deleteModel` 附近增加：

```ts
const deleteAgent = async (agent: Agent) => {
  const warning = `删除本地智能体「${agent.name}」？此操作无法撤销，并会同时删除该智能体的会话、消息、专属记忆和 Prompt 版本。`;
  if (!window.confirm(warning)) return;
  setDeletingAgentId(agent.id);
  setError('');
  setMessage('');
  try {
    await api(`/agents/${agent.id}`, { method: 'DELETE' });
    onAgentDeleted(agent);
    setMessage(`本地智能体「${agent.name}」已删除。`);
    await load();
  } catch (reason) {
    setError((reason as Error).message);
  } finally {
    setDeletingAgentId('');
  }
};
```

- [ ] **Step 2: 在 Agent 卡片渲染删除按钮**

将 Agent 标签页的卡片操作区扩展为：

```tsx
<div className="agent-plugin-card-actions">
  <button type="button" onClick={() => navigate(`/agents/${agent.id}/config`)}>
    配置与调试 →
  </button>
  <button
    type="button"
    className="danger-button"
    disabled={deletingAgentId === agent.id}
    onClick={() => void deleteAgent(agent)}
  >
    {deletingAgentId === agent.id ? '删除中…' : '删除'}
  </button>
</div>
```

如当前 JSX 是单行结构，只需在既有 Agent 卡片内增加操作容器，不重构其他标签页。

- [ ] **Step 3: 运行插件库契约测试**

运行：

```bash
python -m pytest tests/test_frontend_plugin_library.py -q
```

预期：插件库 Agent 删除测试通过，已有 Model/Skill/Tool/MCP 测试继续通过。

## Task 4: 在 App 中协调 Agent 列表删除后的全局状态

**Files:**
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: 实现统一确认和删除处理函数**

在现有 Agent 操作函数附近增加：

```ts
const deleteAgent = async (agent: Agent) => {
  const warning = `删除本地智能体「${agent.name}」？此操作无法撤销，并会同时删除该智能体的会话、消息、专属记忆和 Prompt 版本。`;
  if (!window.confirm(warning)) return;
  setError('');
  try {
    await api(`/agents/${agent.id}`, { method: 'DELETE' });
    const remaining = agents.filter((item) => item.id !== agent.id);
    setAgents(remaining);
    if (agent.id === agentId) {
      const nextAgent = remaining[0];
      if (nextAgent) {
        setAgentId(nextAgent.id);
        navigate('/chat');
        await loadSessions(nextAgent.id);
      } else {
        setAgentId('');
        setSessions([]);
        navigate('/agents/new');
      }
    }
    setError('');
  } catch (reason) {
    setError((reason as Error).message);
  }
};
```

如果当前文件已有全局错误/消息状态或 Agent 刷新函数，复用已有状态，避免新增第二套通知机制。删除时使用当前闭包中的 `agents`、`agentId`、`navigate`、`loadSessions`。

- [ ] **Step 3: 将统一删除处理传给两个页面**

修改 `/agents` 和 `/plugins` 路由元素：

```tsx
<AgentListPage
  agents={agents}
  activeAgentId={agentId}
  onUse={useAgent}
  onConfigure={(id) => navigate(`/agents/${id}/config`)}
  onCreate={() => navigate('/agents/new')}
  onDelete={deleteAgent}
/>
<PluginLibraryPage onAgentDeleted={deleteAgent} />
```

插件库中的 `deleteAgent` 回调在 API 成功后触发 App 的统一状态处理；插件库自身随后继续调用 `load()` 刷新 Agent 卡片。

- [ ] **Step 4: 处理成功后的反馈与当前会话刷新**

确认删除当前 Agent 后：

- 剩余 Agent 存在时选择第一个 Agent 并加载其会话。
- 无剩余 Agent 时清空会话并导航到 `/agents/new`。
- 删除非当前 Agent 时不改变当前选择。
- 删除请求失败时不修改 Agent 列表或当前路由。

若 App 当前的 `loadSessions` 只接受当前 `agentId`，保持该接口，不引入新的全局状态抽象。

- [ ] **Step 4: 运行所有前端源码契约测试**

运行：

```bash
python -m pytest tests/test_frontend_agent_list.py tests/test_frontend_plugin_library.py tests/test_frontend_routes.py -q
```

预期：全部通过。

## Task 5: 前端样式和类型构建验证

**Files:**
- Modify: `frontend/src/styles.css`（仅当现有 `.danger-button` 或卡片操作区没有适配时）

- [ ] **Step 1: 复用现有危险按钮样式**

先确认 `styles.css` 中已有 `.danger-button`。若存在，只调整 Agent 插件卡片操作容器的布局；若不存在，增加与 Model/MCP 删除按钮一致的样式，不修改全局颜色系统。

- [ ] **Step 2: 运行 TypeScript 检查和生产构建**

运行：

```bash
cd frontend && npm run build
```

预期：`tsc --noEmit` 和 `vite build` 均以退出码 0 完成。

## Task 6: 最终回归验证

**Files:**
- None

- [ ] **Step 1: 运行删除相关测试**

```bash
python -m pytest tests/test_frontend_agent_list.py tests/test_frontend_plugin_library.py tests/test_agents.py -q
```

预期：全部通过，后端已有 Agent 删除接口测试继续通过。

- [ ] **Step 2: 运行完整后端回归**

```bash
python -m pytest -q --ignore=tests/test_installer_idempotent.py
```

预期：0 failures。

- [ ] **Step 3: 检查实现范围**

确认没有修改数据库 schema、没有新增删除模型/插件的行为、没有删除外部数据库文件或凭据，并确认两个页面的确认文案都说明会话、消息、专属记忆和 Prompt 版本会被删除。
