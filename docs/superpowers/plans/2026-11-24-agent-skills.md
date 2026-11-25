# Agent 列表一致性与 Skills 自动加载实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to execute this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让模型及插件库 Agent Tab、`/agents` 和配置页使用同一列表组件与状态，并让勾选的 Skills 在普通聊天和调试聊天中自动可靠加载。

**Architecture:** App 持有 Agent 列表和操作回调，PluginLibraryPage 不再自行请求或维护 Agent 副本，Agent Tab 直接复用 AgentListPage。后端新增统一的 Skill 选择解析 helper，普通聊天和调试聊天共同使用；选中 Skill 的加载失败通过 trace/日志暴露，不再静默跳过。

**Tech Stack:** FastAPI、SQLite、pytest、React 19、TypeScript、React Router。

---

## 文件映射

- 修改 `frontend/src/App.tsx`：把共享 Agent 列表和操作回调传入插件库。
- 修改 `frontend/src/pages/PluginLibraryPage.tsx`：移除独立 Agent 数据副本，复用 AgentListPage。
- 修改 `frontend/src/pages/AgentListPage.tsx`：必要时抽取复用所需的 props 类型和稳定布局。
- 修改 `frontend/src/pages/AgentConfigPage.tsx`：保留选择状态并确认保存后回到标准列表。
- 修改 `backend/app/skills.py`：添加 selected Skill 解析 helper。
- 修改 `backend/app/routes/sessions.py`、`backend/app/routes/debug.py`：共同加载选中 Skill，并上报缺失/读取错误。
- 修改 `tests/test_frontend_model_library.py`、`tests/test_frontend_routes.py`、新建 `tests/test_frontend_agent_consistency.py`。
- 修改 `tests/test_skills_runtime.py`、新建 `tests/test_debug_skills_runtime.py`。

## Task 1: 失败测试——插件库 Agent Tab 必须复用标准列表

**Files:** `tests/test_frontend_agent_consistency.py`

- [ ] **Step 1: 添加源码契约**

要求 PluginLibraryPage 导入 `AgentListPage`，props 包含 `agents`、`activeAgentId`、`onUse`、`onConfigure`、`onCreate`、`onDelete`；禁止继续在插件库内部声明 `useState<Agent[]>` 并请求 `/agents` 作为第二份列表。

```python
def test_plugin_agent_tab_reuses_canonical_agent_list():
    source = (FRONTEND / "pages" / "PluginLibraryPage.tsx").read_text(encoding="utf-8")
    assert "AgentListPage" in source
    assert "activeAgentId" in source
    assert "onUse" in source
    assert "useState<Agent[]>" not in source
```

- [ ] **Step 2: 运行测试确认失败**

运行：`python -m pytest tests/test_frontend_agent_consistency.py -q`

预期：失败，因为当前插件库维护自己的 Agent 列表和卡片。

## Task 2: 接入共享 Agent 列表

**Files:** `frontend/src/App.tsx`、`frontend/src/pages/PluginLibraryPage.tsx`、`frontend/src/pages/AgentListPage.tsx`

- [ ] **Step 1: 扩展 PluginLibraryPage props**

将 props 改为：

```tsx
type PluginLibraryPageProps = {
  agents: AgentListItem[];
  activeAgentId: string;
  onUse: (agentId: string) => void;
  onConfigure: (agentId: string) => void;
  onCreate: () => void;
  onDelete: (agent: AgentListItem) => void;
};
```

从 `PluginLibraryPage` 删除 Agent 类型的本地 `useState`、`load` 中的 `/agents` 请求、删除 Agent handler 和旧 Agent 卡片渲染。Agent Tab 改为：

```tsx
{activeTab === 'Agent' && (
  <AgentListPage
    agents={agents}
    activeAgentId={activeAgentId}
    onUse={onUse}
    onConfigure={onConfigure}
    onCreate={onCreate}
    onDelete={onDelete}
  />
)}
```

保留 Model/Skill/MCP/Tool 的独立加载，不要让 Agent Tab 切换触发重复请求。

- [ ] **Step 2: 在 App route 传入共享 props**

修改 `/plugins` route：

```tsx
<Route
  path="/plugins"
  element={
    <PluginLibraryPage
      agents={agents}
      activeAgentId={agentId}
      onUse={useAgent}
      onConfigure={(id) => navigate(`/agents/${id}/config`)}
      onCreate={() => navigate('/agents/new')}
      onDelete={deleteAgent}
    />
  }
/>
```

保留 `loadAgents` 的唯一来源和 `saveAgentToList`/`handleAgentDeleted` 的共享更新逻辑。

- [ ] **Step 3: 统一返回路径和操作回调**

确认 `AgentConfigPage` 的 `leaveConfig` 只导航到 `/agents`；AgentListPage 的配置和新建入口使用同一回调；插件库的配置操作不创建另一种返回页。不要在插件库中复制 Agent 卡片文案或布局。

- [ ] **Step 4: 运行前端一致性测试**

运行：`python -m pytest tests/test_frontend_agent_consistency.py tests/test_frontend_routes.py tests/test_frontend_model_library.py -q`  
运行：`npm --prefix frontend run typecheck`

预期：通过。

## Task 3: 添加统一的选中 Skill 解析

**Files:** `backend/app/skills.py`、`tests/test_skills_runtime.py`

- [ ] **Step 1: 添加失败单元测试**

测试给定 managed/legacy Skill 列表和 `selected_names` 时，返回按选择顺序去重的 Skill、缺失名称列表；同名 managed Skill 优先于 legacy Skill。

```python
def test_resolve_selected_skills_reports_missing_names():
    selected, missing = resolve_selected_skills(
        ["summarize", "missing", "summarize"],
        managed_skills,
        legacy_skills,
    )
    assert [skill.name for skill in selected] == ["summarize"]
    assert missing == ["missing"]
```

- [ ] **Step 2: 运行测试确认 helper 不存在**

运行：`python -m pytest tests/test_skills_runtime.py::test_resolve_selected_skills_reports_missing_names -q`

预期：失败。

- [ ] **Step 3: 实现 `resolve_selected_skills`**

在 `skills.py` 增加：

```python
def resolve_selected_skills(
    selected_names: list[str],
    managed_skills: list[Skill],
    legacy_skills: list[Skill] | None = None,
) -> tuple[list[Skill], list[str]]:
    by_name: dict[str, Skill] = {skill.name: skill for skill in (legacy_skills or [])}
    by_name.update({skill.name: skill for skill in managed_skills})
    selected: list[Skill] = []
    missing: list[str] = []
    for name in dict.fromkeys(selected_names):
        skill = by_name.get(name)
        if skill is None:
            missing.append(name)
        else:
            selected.append(skill)
    return selected, missing
```

- [ ] **Step 4: 运行 Skills 单元测试**

运行：`python -m pytest tests/test_skills_runtime.py -q`

预期：通过。

## Task 4: 普通聊天自动加载选中 Skills 并报告问题

**Files:** `backend/app/routes/sessions.py`、`tests/test_skills_runtime.py`

- [ ] **Step 1: 保留现有选中内容测试并增加缺失测试**

导入一个 managed Skill，创建 Agent 选中它，发送消息并断言 provider 的 system message 含 Skill 正文。再创建 Agent 选中不存在的名称，断言 SSE trace 的 `skills_loaded` 事件状态为 warning、summary 包含名称，且请求仍能继续。

- [ ] **Step 2: 运行新增测试确认缺失提示失败**

运行：`python -m pytest tests/test_skills_runtime.py -q`

预期：现有 selected Skill 测试通过，缺失 Skill 的新断言失败，因为当前逻辑静默跳过。

- [ ] **Step 3: 使用统一 helper 构造选择结果**

在 `send_message` 中获取 `managed_skills` 和 `legacy_skills` 后调用 `resolve_selected_skills`。有明确 `selected_names` 时只使用解析结果；无明确选择且存在 legacy Skills 时继续使用当前 `select_relevant_skills` 自动匹配。加载正文时捕获 OSError/ValueError，将具体 Skill 名称加入 `skill_load_errors`，不把损坏正文放入模型上下文。

将 `skills_loaded` record/trace 改为包含：

```python
{
    "event_type": "skills_loaded",
    "status": "warning" if skill_load_errors or missing_skills else "completed",
    "summary": f"加载 {len(selected_skills) - len(skill_load_errors)} 个 Skills"
        + (f"；未加载：{', '.join(missing_skills + skill_load_errors)}"
           if missing_skills or skill_load_errors else ""),
}
```

- [ ] **Step 4: 运行普通聊天 Skills 回归**

运行：`python -m pytest tests/test_skills_runtime.py tests/test_chat.py tests/test_agent_plugin_access.py -q`

预期：通过，未选中的 Skill 正文不会发送给模型。

## Task 5: 调试聊天使用相同 Skill 选择规则

**Files:** `backend/app/routes/debug.py`、新建 `tests/test_debug_skills_runtime.py`

- [ ] **Step 1: 添加调试 Skill 测试**

创建两个 managed Skill，DebugRequest 只选其中一个；断言 provider 请求 system message 只包含选中正文。再发送不存在 Skill 名称，断言 debug SSE 的 trace 为 warning 并包含该名称，模型仍得到用户消息。

- [ ] **Step 2: 运行测试确认失败**

运行：`python -m pytest tests/test_debug_skills_runtime.py -q`

预期：缺失 Skill 提示测试失败；现有 debug 逻辑静默跳过读取异常。

- [ ] **Step 3: 改造 debug Skill 加载**

在 `debug_agent` 使用 `resolve_selected_skills(config.selected_skills, plugin_store.list_skills())`；逐项加载正文并记录读取失败。首次 trace 的 `skills_loaded` 载荷包含 `status`、`summary` 和 `missing_skills`；不把未选择 Skill 的说明加入 `messages`。保留 system prompt、知识库、工具授权和审批顺序。

- [ ] **Step 4: 运行调试回归**

运行：`python -m pytest tests/test_debug_skills_runtime.py tests/test_agent_debug.py -q`

预期：通过。

## Task 6: 前端交互和数据一致性验收

- [ ] **Step 1: 验证所有 Agent 入口**

运行：`python -m pytest tests/test_frontend_agent_consistency.py tests/test_frontend_routes.py tests/test_frontend_agent_config.py tests/test_frontend_model_library.py -q`

预期：通过。

- [ ] **Step 2: 构建前端**

运行：`npm --prefix frontend run build`

预期：成功。

- [ ] **Step 3: 手工验证路由往返**

打开 `/plugins?tab=Agent`、`/agents`、`/agents/{id}/config`：确认 Agent 卡片、空状态、配置/使用/删除按钮和文案一致；从插件库配置后返回 `/agents`，从 `/agents` 配置后也返回同一页面；新建/删除后两个入口立即反映同一列表。

- [ ] **Step 4: 手工验证 Skills**

导入两个 Skill，只勾选其中一个；在普通聊天和调试聊天中发送能触发该 Skill 的消息，检查运行轨迹和模型上下文；确认另一个 Skill 内容不出现，选中损坏/缺失名称时显示明确警告。
