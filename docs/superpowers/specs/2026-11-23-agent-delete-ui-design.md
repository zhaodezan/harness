# 本地智能体删除入口设计

## 1. 目标与范围

为本地智能体增加明确、可发现的删除入口，覆盖两个现有页面：

- 模型及插件库 → Agent（本地智能体）
- 智能体列表页

两处入口都复用现有后端接口 `DELETE /api/agents/{agent_id}`，不新增数据库表或删除 API。此次变更只涉及前端交互、页面状态同步和契约测试。

## 2. 删除语义

后端删除 Agent 时，现有外键级联规则会一并删除：

- 该 Agent 的会话和消息
- Agent 专属长期记忆
- Prompt 版本

删除模型配置不在本次范围内。删除确认框必须明确告知上述影响，并说明操作不可撤销。

## 3. 页面行为

### 3.1 模型及插件库

在每个 Agent 卡片操作区增加“删除”按钮。点击后：

1. 弹出浏览器确认框。
2. 用户取消时不发起请求。
3. 用户确认时调用 `DELETE /api/agents/{id}`。
4. 成功后重新加载模型、Agent、Skill、Tool、MCP 数据，保持现有页面标签和筛选状态。
5. 删除失败时保留页面数据并显示 API 返回的错误信息。

确认文案应包含 Agent 名称，以及会话、消息、专属记忆和 Prompt 版本将被删除。

### 3.2 智能体列表页

在每个 Agent 卡片操作区增加同样的“删除”按钮，使用与插件库一致的确认文案和 API。

删除成功后：

- 从本地列表状态移除已删除项。
- 如果删除的是当前使用的 Agent，优先切换到剩余列表中的第一个 Agent。
- 如果没有剩余 Agent，跳转到 `/agents/new`。
- 删除非当前 Agent 时保持当前 Agent 不变。
- 删除失败时保留当前列表和当前选择，并显示错误反馈。

页面需要避免将删除操作误认为“配置”或“使用”，按钮使用危险操作样式并提供明确文字。

## 4. 组件接口调整

`AgentListPage` 增加删除回调，例如：

```ts
onDelete: (agent: AgentListItem) => void;
```

具体删除请求和全局 Agent/session 状态协调继续由 `App.tsx` 负责，因为它拥有当前 Agent、会话列表和路由导航状态。

`PluginLibraryPage` 在自身已有 Agent 数据加载上下文中增加删除处理函数；删除成功后复用现有 `load()` 刷新列表。

## 5. 错误处理

- API 返回非 2xx 时，复用现有 `api()` 错误解析机制。
- 不吞掉删除失败；在页面的错误提示区域显示后端错误。
- 删除过程中可暂时禁用对应按钮，避免重复请求；请求结束后恢复。
- 不在前端自行删除数据库文件、凭据或其他资源。

## 6. 测试策略

增加或更新前端源码契约测试，验证：

- 插件库 Agent 区域存在删除操作。
- 智能体列表页存在删除操作。
- 两处均调用 Agent 删除 API 或通过明确的删除回调触发该 API。
- 确认文案包含级联删除影响。
- 当前 Agent 删除后存在剩余 Agent 和无剩余 Agent 两种路由/选择处理逻辑。

验证命令：

```bash
python -m pytest tests/test_frontend_agent_list.py tests/test_frontend_plugin_library.py -q
cd frontend && npm run build
python -m pytest -q --ignore=tests/test_installer_idempotent.py
```

## 7. 不在本次范围内

- 修改后端删除接口或数据库 schema。
- 删除 Model、Skill、Tool、MCP Server 的行为调整。
- 增加软删除、回收站或撤销功能。
- 修改确认框为新的 UI 组件。
- 删除当前 Agent 后自动迁移其会话到其他 Agent。
