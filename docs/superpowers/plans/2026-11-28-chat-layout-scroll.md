# 聊天抽屉布局与滚动调整实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 让右侧文件抽屉通过布局扩展而非覆盖聊天，移除聊天顶部设置齿轮但保留左侧账户设置，并让对话消息区默认跟随底部滚动。

**Architecture:** 使用固定视口高度的 `app-shell`，让侧栏自身滚动；`main-panel` 内部以 flex 并排承载 route 与文件抽屉，不用绝对定位或 margin 假造空间。聊天消息区设置独立滚动容器，使用纯函数判断是否应跟随底部，并在加载/消息变化时只于用户接近底部的情况下滚动。

**Tech Stack:** React 19、TypeScript、CSS、Vitest。

---

### Task 1: 去除聊天顶部设置入口，保持账户设置入口

**Files:**
- Modify: `frontend/src/App.tsx`
- Test: `frontend/src/ChatLayout.test.ts`（或通过已有静态/UI 测试约定验证）

- [x] **Step 1: 写失败测试**

为聊天 chrome 提取纯判定/渲染 helper 不合适；本次无 React Testing Library 依赖。先在 `frontend/src/chatScroll.test.ts` 添加 `shouldFollowChatBottom` 测试，测试距离底部 80px 时 follow、距离 180px 时不 follow、scrollHeight 小于 clientHeight 时 follow。

- [x] **Step 2: 运行并确认测试失败**

Run: `npm test -- --run src/chatScroll.test.ts`
Expected: 找不到 `chatScroll` 模块。

- [x] **Step 3: 移除聊天 topbar 齿轮并保留账户齿轮**

从 `App.tsx` chat topbar 中删除仅在 `!agentId` 下显示的 `aria-label="打开模型库"` 齿轮按钮；保留 `.sidebar-bottom` 的账户设置齿轮及其点击行为。文件抽屉按钮仍在 topbar。

- [x] **Step 4: 实现并验证滚动策略 helper**

新建 `frontend/src/chatScroll.ts`：

```ts
export function shouldFollowChatBottom(scrollHeight: number, scrollTop: number, clientHeight: number, threshold = 120): boolean {
  return scrollHeight - scrollTop - clientHeight <= threshold;
}
```

Run: `npm test -- --run src/chatScroll.test.ts`
Expected: 新增测试通过。

### Task 2: 抽屉加入正常布局流并实现消息区滚动跟随

**Files:**
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/styles.css`
- Modify: `frontend/src/components/WorkspaceFileDrawer.css`
- Modify: `frontend/src/chatScroll.ts`
- Test: `frontend/src/chatScroll.test.ts`

- [x] **Step 1: 在 App 里新增 chat message scroll ref 和底部锚点**

为 `.conversation` 添加滚动 ref 与 scroll handler；会话变化时重置“跟随底部”状态，消息更新时仅在首屏/用户原本接近底部时将 scrollTop 置底。用户滚离底部阅读历史时不抢滚动，回到底部后恢复跟随。

- [x] **Step 2: 移除绝对定位及假的右侧 margin**

在 `styles.css` 固定 `.app-shell` 为 `height:100vh` 并限制溢出，允许 `.sidebar` 自身纵向滚动；`.main-panel` 使用 `height:100%; min-height:0; display:flex; flex-direction:row; overflow:hidden`。Routes wrapper 具备 `min-width:0; min-height:0; flex:1`，抽屉作为 flex sibling 参加布局。删除抽屉 absolute 定位及 conversation/composer/trace/footer 人工宽度、边距和 padding-right 规则。桌面和窄屏抽屉都参与布局并缩窄聊天区。

- [x] **Step 3: 调整消息区高度与可见滚动条**

在 `.conversation` 使用 `min-height:0; flex:1; overflow-y:auto; overscroll-behavior:contain`。固定 app-shell 与 main-panel 高度，避免消息增长或侧栏内容把整个页面撑高；聊天区域纵向滚动时不推动 composer/footer 出视口。

- [x] **Step 4: 运行完整前端验证**

Run: `npm test && npm run typecheck && npm run build`
Expected: 全部前端测试通过、类型检查通过、Vite build 成功。

### Task 3: 运行应用并验收滚动和布局

**Files:**
- No additional files.

- [x] **Step 1: 手工验证桌面与窄屏布局**

打开 `/chat`，确认 topbar 无设置齿轮，文件按钮可开关抽屉，账户区齿轮仍存在；抽屉打开后聊天和 composer 宽度缩小但均可见、无覆盖。窄屏确认抽屉不遮盖聊天消息或输入框。

- [x] **Step 2: 手工验证滚动行为**

在有历史会话的浏览器中确认消息区能在内部滚动，长消息不扩大整个页面；单元测试覆盖滚动距离阈值的跟随与暂停行为。

- [x] **Step 3: 检查变更格式**

Run: `git diff --check`
Expected: 无 whitespace errors。不得切换或创建分支；按用户要求在唯一的 `main` 分支工作。
