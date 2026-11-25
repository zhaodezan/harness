# 统一聊天输入与计划执行实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to execute this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 统一普通聊天/调试聊天的 Enter 行为，并修复计划生成后执行上下文丢失、计划展示不完整的问题。

**Architecture:** 后端保留现有 SSE 事件协议，修复普通会话中 `execution_messages` 的构造顺序，使计划永远位于执行上下文中；调试会话补齐计划状态展示。前端用一个纯函数统一 textarea 的 Enter 处理，两个聊天界面继续使用各自的提交和流式逻辑。

**Tech Stack:** Python、FastAPI、httpx、pytest、React、TypeScript、Vite。

---

## 文件映射

- 新建 `frontend/src/chatKeyboard.ts`：统一聊天 textarea 的 Enter/Shift+Enter/IME 判定。
- 修改 `frontend/src/App.tsx`：使用共享键盘 helper，保持主聊天发送/停止逻辑。
- 修改 `frontend/src/pages/AgentDebugChat.tsx`：使用共享键盘 helper，显示调试计划。
- 修改 `backend/app/routes/sessions.py`：修复计划加入后又被重置的执行上下文。
- 修改 `backend/app/routes/debug.py`：补充稳定计划事件/状态语义并记录失败原因。
- 修改 `tests/test_plan_execute.py`、`tests/test_agent_debug.py`：验证计划上下文、计划事件和调试流。
- 新建 `tests/test_chat_keyboard.py`：验证前端 helper 的源码契约。

## Task 1: 失败测试——计划执行请求必须包含计划

**Files:** `tests/test_plan_execute.py`

- [ ] **Step 1: 扩展 provider mock 记录每次请求**

在现有 `provider` 中保存 JSON body；第一段非流式响应返回两步计划，后续流式响应返回每步结果。测试在响应结束后断言至少一个执行请求的 `messages` 包含 `执行计划：`、`检查问题` 和 `给出建议`。

```python
execution_bodies = []

async def provider(request):
    body = json.loads(await request.aread())
    calls.append(body)
    if body["stream"] is False:
        return httpx.Response(200, json={"choices": [{"message": {
            "content": "1. 检查问题\\n2. 给出建议"
        }}]})
    execution_bodies.append(body)
    return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"已完成"}}]}\n\ndata: [DONE]\n\n')
```

- [ ] **Step 2: 运行测试确认当前实现失败**

运行：`python -m pytest tests/test_plan_execute.py::test_plan_execute_keeps_plan_in_execution_context -q`

预期：失败，因为当前 `sessions.py` 在计划追加后重新从 `messages` 初始化执行上下文。

## Task 2: 修复普通会话的计划上下文

**Files:** `backend/app/routes/sessions.py`

- [ ] **Step 1: 调整知识库上下文和执行上下文顺序**

在 `event_stream` 中，先完成一次 `knowledge_context` 注入，再执行：

```python
execution_messages = list(messages)
execution_steps: list[str | None] = [None]
if session["mode"] == "plan_execute":
    # planning_messages 使用 messages，但不共享可变列表
    plan = await complete_chat(...)
    execution_steps = parse_plan_steps(plan)
    yield plan_event
    execution_messages.append({"role": "assistant", "content": f"执行计划：\n{plan}"})
```

删除计划追加之后第二次 `messages.append(knowledge_context)` 和 `execution_messages = list(messages)` 的重复初始化。保留工具、审批、重规划、持久化和最终 SSE 逻辑不变。

- [ ] **Step 2: 运行计划测试**

运行：`python -m pytest tests/test_plan_execute.py -q`

预期：计划内容保留、两步开始/完成事件数量仍为 2，所有测试通过。

- [ ] **Step 3: 加入计划边界测试**

增加测试：规划返回空内容时发送 `event: error` 且不会执行工具；规划返回超过 8 步时发送明确上限错误；规划请求 body 不含工具定义，执行请求可以包含授权工具定义。

- [ ] **Step 4: 验证边界**

运行：`python -m pytest tests/test_planning.py tests/test_plan_execute.py -q`

预期：通过。

## Task 3: 调试流的计划展示和事件一致性

**Files:** `backend/app/routes/debug.py`、`tests/test_agent_debug.py`

- [ ] **Step 1: 添加调试计划事件测试**

用 mock provider 返回两步计划和流式结果，POST `/api/agents/{id}/debug`，断言响应包含 `event: plan`、计划文本、每步 trace，以及执行请求 body 包含计划文本。

- [ ] **Step 2: 运行测试确认缺少断言的实现状态**

运行：`python -m pytest tests/test_agent_debug.py -q`

预期：新增计划执行上下文/事件测试至少有一项失败，作为实现检查点。

- [ ] **Step 3: 统一 debug 事件载荷**

确保 debug 的 `plan` 事件始终包含 `{ "plan": string, "revised": boolean }`（初次计划 `revised` 为 false），trace 事件包含 `status` 和 `summary`；解析失败通过 `event: error` 返回安全错误。保留现有审批流程。

- [ ] **Step 4: 运行 debug 回归**

运行：`python -m pytest tests/test_agent_debug.py -q`

预期：通过；不泄漏 API Key。

## Task 4: 统一 Enter/Shift+Enter 键盘行为

**Files:** 新建 `frontend/src/chatKeyboard.ts`、新建 `tests/test_chat_keyboard.py`

- [ ] **Step 1: 写纯函数契约测试**

增加源码契约，要求 helper 检查 `event.key === 'Enter'`、`event.shiftKey`、`event.nativeEvent.isComposing`，并在发送时调用 `preventDefault`。测试不依赖浏览器 DOM。

- [ ] **Step 2: 运行测试确认 helper 不存在**

运行：`python -m pytest tests/test_chat_keyboard.py -q`

预期：失败。

- [ ] **Step 3: 实现 helper**

创建以下实现，发送回调由调用方负责 `void`/Promise 生命周期：

```typescript
export type ChatKeyEvent = {
  key: string;
  shiftKey: boolean;
  preventDefault: () => void;
  nativeEvent: { isComposing?: boolean };
};

export function submitChatOnEnter(
  event: ChatKeyEvent,
  submit: () => void,
): void {
  if (
    event.key !== 'Enter' ||
    event.shiftKey ||
    event.nativeEvent.isComposing
  ) {
    return;
  }
  event.preventDefault();
  submit();
}
```

- [ ] **Step 4: 运行 helper 契约测试**

运行：`python -m pytest tests/test_chat_keyboard.py -q`

预期：通过。

## Task 5: 接入主聊天

**Files:** `frontend/src/App.tsx`

- [ ] **Step 1: 修改 send 签名保留表单备用提交**

将 `send(event: FormEvent)` 改为 `send()`，在 form 的 `onSubmit` 中使用：

```tsx
<form className="composer" onSubmit={(event) => {
  event.preventDefault();
  void send();
}}>
```

- [ ] **Step 2: 使用共享 helper**

导入 `submitChatOnEnter`，textarea 使用：

```tsx
onKeyDown={(event) => submitChatOnEnter(event, () => void send())}
```

保留 `!draft.trim()`、`busy`、AbortController 和停止按钮逻辑；Shift+Enter 不阻止默认行为，因此浏览器保留换行。

- [ ] **Step 3: 添加主聊天源码回归**

在新建的 `tests/test_frontend_chat_input.py` 中断言 App 导入 helper、textarea 使用 helper、form 仍使用 `preventDefault`，并保留 `busy` 防重入。

- [ ] **Step 4: 运行前端契约与 typecheck**

运行：`python -m pytest tests/test_frontend_chat_input.py tests/test_chat_keyboard.py -q`  
运行：`npm --prefix frontend run typecheck`

预期：通过。

## Task 6: 接入调试聊天并显示计划

**Files:** `frontend/src/pages/AgentDebugChat.tsx`

- [ ] **Step 1: 增加 `plan` state**

新增 `const [plan, setPlan] = useState('');`；收到 `plan` 事件时设置计划文本，若 payload `revised` 为 true 则保留同一计划卡并更新状态。

- [ ] **Step 2: 修改调试发送函数和 form**

将 `send(event)` 改为 `send()`，form 使用 `preventDefault` 后调用 `void send()`；textarea 使用共享 `submitChatOnEnter`。收到新消息前清空 plan，busy 时仍禁止发送。

- [ ] **Step 3: 渲染计划卡和步骤状态**

在调试消息列表上方，当 `plan` 非空时渲染 `.debug-plan-card`，显示“执行计划”和 `<pre>{plan}</pre>`；现有 status 文案继续显示最后的 trace/完成状态。

- [ ] **Step 4: 运行调试前端回归**

运行：`python -m pytest tests/test_frontend_debug_chat.py tests/test_frontend_chat_input.py -q`  
运行：`npm --prefix frontend run typecheck`

预期：通过。

## Task 7: 真实聊天验收

- [ ] **Step 1: 构建前端**

运行：`npm --prefix frontend run build`

预期：成功。

- [ ] **Step 2: 运行后端聊天回归**

运行：`python -m pytest tests/test_chat.py tests/test_plan_execute.py tests/test_planning.py tests/test_agent_debug.py -q`

预期：通过。

- [ ] **Step 3: 手工验证**

打开普通聊天和 Agent 调试聊天：输入多行文本确认 Shift+Enter 换行，单独按 Enter 发送；中文输入法组合输入确认不会提前发送。使用 `plan_execute` Agent，确认先看到计划、再看到逐步轨迹和最终结果；构造工具失败确认出现重规划，超过上限后停止。
