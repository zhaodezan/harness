# 聊天工作区文件抽屉实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 在聊天右侧增加绑定当前会话工作区的文件抽屉，支持安全浏览、编辑、预览、多标签，并能从聊天链接打开工作区文件或 HTTP(S) 网页。

**Architecture:** 后端新增仅按 session ID 解析工作区的文件接口，所有路径由服务端限制在该会话本机工作区内；前端以独立抽屉组件管理文件标签和网页 iframe 标签。聊天消息目前按纯文本显示，新增安全的链接分段器，将相对工作区 Markdown 链接交给当前聊天处理器，将 HTTP(S) 链接交给抽屉，普通文本和现有图表块保持现状。

**Tech Stack:** FastAPI、Python pathlib、React 19、TypeScript、Vitest、现有 pytest/TestClient。

---

## 文件结构

- 新建 `backend/app/workspace_files.py`：纯后端文件系统服务；校验会话工作区、相对路径、文件类型/大小、读取修订标记和安全写入。
- 新建 `backend/app/routes/workspace_files.py`：将服务封装为列表、文本读写、媒体预览和下载 API。
- 修改 `backend/app/main.py`：注册工作区文件路由。
- 新建 `tests/test_workspace_files.py`：后端路径安全、读写、预览/下载和 HTTP 行为测试。
- 新建 `frontend/src/chatLinks.ts`：识别 Markdown 相对文件链接、Markdown HTTP(S) 链接及纯文本 HTTP(S) URL；不生成 HTML。
- 新建 `frontend/src/chatLinks.test.ts`：测试分段、URL 校验、相对文件路径和恶意链接过滤。
- 修改 `frontend/src/ChartMessageContent.tsx`：保留图表块处理，在普通文本段安全渲染链接并接收可选链接回调。
- 新建 `frontend/src/components/WorkspaceFileDrawer.tsx` 和 `WorkspaceFileDrawer.css`：抽屉、文件树、文本编辑器、文件预览、网页 iframe 和标签 UI。
- 新建 `frontend/src/components/workspaceFileTabs.ts` 与 `workspaceFileTabs.test.ts`：标签状态转换和未保存离开决策，供 React 组件使用并可单测。
- 修改 `frontend/src/App.tsx`：当前 session 下挂载抽屉；聊天链接调度到文件或网页标签；切换 session 时丢弃旧 session 标签。
- 修改 `frontend/src/styles.css`：只放聊天主区域和抽屉并排/窄屏适配所需的少量布局样式，组件细节留在独立 CSS。
- 修改 `frontend/package.json`：让 Vitest 默认执行全部 `*.test.ts` 测试，而不是只运行图表测试。

## 实施假设与边界

- 文件链接约定为 Markdown 链接，例如 `[结果](reports/result.md)`；路径必须是相对当前工作区的路径。绝对本机路径、`file:`、`javascript:`、`data:` 链接不作为工作区文件链接。
- 网页链接只接受 `http:` / `https:`。网页在抽屉的 `iframe` 中运行，使用 `sandbox="allow-scripts"`，不授予同源、顶层导航、弹窗、下载或本机文件访问能力。站点禁止嵌入时显示说明和“在系统浏览器打开”按钮。
- 目录列表每层最多返回 1,000 个条目；编辑文本上限 2 MiB；媒体内嵌预览上限 20 MiB。超限时保留文件信息并提供适当错误，不把大文件读入编辑器。
- 可编辑文本仅限明确的文本/代码扩展名和 UTF-8 内容；HTML/SVG 只能作为文本编辑，不以内嵌网页方式运行。内嵌媒体采用明确扩展名/MIME allowlist，未知内容只可下载，下载响应采用附件方式。
- Agent 配置页中的临时 Debug Chat 没有持久 session ID，不启用工作区文件抽屉；其消息仍显示安全链接，但外链走系统浏览器，不创建跨 session 文件操作。
- 不添加 Markdown、IDE 或浏览器依赖；链接渲染使用 React 元素，不使用 `dangerouslySetInnerHTML`。

### Task 1: 建立工作区文件服务和路径安全测试

**Files:**
- Create: `tests/test_workspace_files.py`
- Create: `backend/app/workspace_files.py`

- [x] **Step 1: 写服务层失败测试**

在 `tests/test_workspace_files.py` 添加以下核心行为测试；本任务先只创建测试并导入尚不存在的服务，确保失败发生在目标 API 缺失，而非测试环境问题：

```python
import pytest
from fastapi import HTTPException
from backend.app.workspace_files import (
    list_workspace_entries,
    read_workspace_text,
    resolve_workspace_file,
    write_workspace_text,
)


def test_rejects_parent_traversal(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    with pytest.raises(HTTPException) as error:
        resolve_workspace_file(root, "../secret.txt")
    assert error.value.status_code == 422


def test_lists_and_reads_only_relative_workspace_files(tmp_path):
    root = tmp_path / "workspace"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "note.md").write_text("hello", encoding="utf-8")
    entries = list_workspace_entries(root, "")
    assert [(item["name"], item["kind"]) for item in entries] == [("docs", "directory")]
    content, revision = read_workspace_text(root, "docs/note.md")
    assert content == "hello"
    assert revision


def test_write_requires_current_revision_and_replaces_text(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    target = root / "note.txt"
    target.write_text("old", encoding="utf-8")
    _, revision = read_workspace_text(root, "note.txt")
    new_revision = write_workspace_text(root, "note.txt", "new", revision)
    assert target.read_text(encoding="utf-8") == "new"
    assert new_revision != revision
    with pytest.raises(HTTPException) as error:
        write_workspace_text(root, "note.txt", "stale", revision)
    assert error.value.status_code == 409
```

补充 pytest 参数化用例覆盖绝对路径、空路径作为文件、NUL、符号链接（平台支持时）、不存在文件及目录、不可编辑后缀、无效 UTF-8、2 MiB 编辑上限、列表 1,000 条上限。符号链接测试在 Windows 权限不允许创建时仅跳过该用例，不跳过其他安全测试。

- [x] **Step 2: 运行目标测试确认按预期失败**

Run: `pytest tests/test_workspace_files.py -q`
Expected: collection/import failure指出 `backend.app.workspace_files` 尚不存在。

- [x] **Step 3: 实现文件路径和目录服务**

在 `backend/app/workspace_files.py` 实现：

```python
EDITABLE_SUFFIXES = frozenset({
    ".txt", ".md", ".rst", ".json", ".yaml", ".yml", ".toml", ".ini", ".csv",
    ".py", ".js", ".jsx", ".ts", ".tsx", ".css", ".html", ".xml", ".sql",
    ".sh", ".ps1", ".bat", ".c", ".h", ".cpp", ".java", ".go", ".rs", ".rb",
})
MAX_EDIT_BYTES = 2 * 1024 * 1024
MAX_DIRECTORY_ENTRIES = 1000
```

提供并测试以下签名：`resolve_workspace_file(root: Path, relative_path: str, *, must_exist: bool = True) -> Path`、`list_workspace_entries(root: Path, relative_path: str) -> list[dict]`、`read_workspace_text(root: Path, relative_path: str) -> tuple[str, str]`、`write_workspace_text(root: Path, relative_path: str, content: str, expected_revision: str) -> str`。路径必须是工作区相对 POSIX 路径；拒绝绝对路径、盘符、`..`、NUL 和任一符号链接组件；将最终解析结果再次验证在 root 下。revision 使用文件内容的 SHA-256，保存前比较当前 revision，临时文件写入同目录后原子替换；对写入目标再次校验不为符号链接。目录项返回 `name/path/kind/size/modified_at`，按目录优先、名称不区分大小写排序，并仅返回本层最多 1,000 项。文件读写限制 UTF-8 和允许的后缀，所有拒绝以明确状态码的 `HTTPException` 表达。

- [x] **Step 4: 运行服务层测试确认通过**

Run: `pytest tests/test_workspace_files.py -q`
Expected: 新增服务层测试全部通过；不支持符号链接创建的平台只显示对应测试 skipped。

### Task 2: 增加会话限定的文件 API

**Files:**
- Create: `backend/app/routes/workspace_files.py`
- Modify: `backend/app/main.py`
- Modify: `tests/test_workspace_files.py`

- [x] **Step 1: 添加 API 失败测试**

用 `TestClient(create_app(...))` 测试先建立 session，再通过 `GET /api/sessions/{id}/workspace-files?path=` 列出文件；以 `/content?path=note.md` 读取 JSON `{content, revision}`；`PUT` 提交 `{path, content, expected_revision}` 保存；`GET /preview?path=photo.png` 只允许安全媒体类型；`GET /download?path=archive.bin` 返回附件。添加 session 不存在返回 404、sandbox 返回 409 且不触碰本机路径、工作区外/符号链接拒绝、旧 revision 返回 409、HTML/SVG preview 拒绝、未知二进制只可下载的断言。

- [x] **Step 2: 运行 API 测试确认路由缺失**

Run: `pytest tests/test_workspace_files.py -q`
Expected: 文件服务测试通过；API 部分失败于 404。

- [x] **Step 3: 实现会话工作区解析和路由**

在 `backend/app/routes/workspace_files.py` 建立 `APIRouter(prefix="/api", tags=["workspace-files"])`。路由约定：

```text
GET /sessions/{session_id}/workspace-files?path=<relative-dir>
GET /sessions/{session_id}/workspace-files/content?path=<relative-file>
PUT /sessions/{session_id}/workspace-files/content
GET /sessions/{session_id}/workspace-files/preview?path=<relative-file>
GET /sessions/{session_id}/workspace-files/download?path=<relative-file>
```

每个 endpoint 从数据库确认 session 存在、读取已持久化 workspace/execution mode；只对 `local` 模式调用现有 `session_workspace(...)` 取得根路径，拒绝 `sandbox`，不接受 root 路径参数。响应不返回根目录绝对路径。列表响应包含相对路径和基本 metadata。preview 只允许明确的 PNG/JPEG/GIF/WebP/PDF/MP3/WAV/MP4/WebM allowlist，限制 20 MiB，设置准确媒体类型、`X-Content-Type-Options: nosniff`、`Content-Disposition: inline` 和 same-origin resource policy；不得内嵌 HTML/SVG。download 使用 `application/octet-stream`、`Content-Disposition: attachment`、`nosniff`，路径仍受相同限制。保存 JSON 使用 Pydantic 限长并返回更新 revision。异常不回显本机绝对路径。

在 `backend/app/main.py` 引入并注册 `workspace_files_router`。保持与 `workspaces_router` 相同的 API 前缀/路由注册风格。

- [x] **Step 4: 运行 API 和现有工作区回归测试**

Run: `pytest tests/test_workspace_files.py tests/test_session_workspaces.py tests/test_workspace_chat.py -q`
Expected: 所有新增测试和既有工作区测试通过。

### Task 3: 实现安全的聊天链接分段器

**Files:**
- Create: `frontend/src/chatLinks.ts`
- Create: `frontend/src/chatLinks.test.ts`
- Modify: `frontend/package.json`

- [x] **Step 1: 写链接解析失败测试**

测试 `parseChatLinks(text)` 对以下输入产生 `text` / `workspace-file` / `web` 段：普通文本和换行、`[报告](reports/result.md)`、`[网站](https://example.com/a?q=1)`、纯文本 `https://example.com`、连续标点、重复相同链接。拒绝 `[逃逸](../secret)`、绝对路径、`file:///C:/secret`、`javascript:alert(1)`、`data:text/html,...`；恶意链接以纯文本显示。测试 `normalizeWorkspaceLinkPath` 统一反斜杠但拒绝空路径、`.`/`..` 逃逸、盘符和 NUL。

- [x] **Step 2: 扩展 Vitest 脚本并确认测试失败**

将 `frontend/package.json` 中 `test` 改为 `vitest run`，让 Vitest 自动收集全部测试。运行 `npm test -- --run src/chatLinks.test.ts`。
Expected: 测试可被发现，但因模块/函数缺失而失败。

- [x] **Step 3: 实现纯文本安全分段器**

在 `chatLinks.ts` 定义：

```ts
export type ChatLinkSegment =
  | { type: 'text'; value: string }
  | { type: 'workspace-file'; label: string; path: string }
  | { type: 'web'; label: string; href: string };

export function normalizeWorkspaceLinkPath(path: string): string | null;
export function parseChatLinks(text: string): ChatLinkSegment[];
```

仅识别完整 Markdown inline link 与纯文本 HTTP(S) URL；拒绝不受支持 scheme，不做 HTML 解码/执行，不把未识别 Markdown 语法变成 DOM。通过 URL 构造器规范化 Web URL，协议限制为 http/https。相对文件 path 必须标准化成工作区相对 POSIX 格式且不得含 `..` 或盘符。链接可见标签按纯字符串渲染。

- [x] **Step 4: 运行前端链接测试**

Run: `npm test -- --run src/chatLinks.test.ts`
Expected: 所有链接解析/拒绝用例通过。

### Task 4: 将安全链接呈现在聊天消息中

**Files:**
- Modify: `frontend/src/ChartMessageContent.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/pages/AgentDebugChat.tsx`
- Modify: `frontend/src/chatLinks.test.ts`

- [x] **Step 1: 写消息链接渲染契约测试**

给 `MessageContent` 增加可选的 `onWorkspaceFile(path: string)`、`onWebLink(href: string)` 回调。纯解析器测试确认文本 label 可保留，但渲染层要另确认：web anchor 始终使用 `target="_blank"`、`rel="noopener noreferrer"` 作为无抽屉回调时的安全 fallback；有回调时 click handler `preventDefault()` 后把规范化 URL交给 owner。工作区 link 无 handler 时保持纯文本，避免无 session 页面假链接。

- [x] **Step 2: 修改图表块文本渲染**

将 `ChartMessageContent.tsx` 中普通 `message-text` span 的字符串输出拆成 `parseChatLinks` 片段映射；链接标签使用 React `<a>` 或按钮节点，绝不注入 HTML。保留原 `parseChartBlocks`、图表排序和 plain text 的 `white-space: pre-wrap`。将 props 回调设为可选。`App.tsx` 挂载正式 session chat 时传入抽屉回调；`AgentDebugChat.tsx` 不启用 workspace file callback，普通 HTTP(S) 链接通过外部浏览器安全打开。

- [x] **Step 3: 运行前端测试与类型检查**

Run: `cd frontend && npm test -- --run src/chartBlocks.test.ts src/chatLinks.test.ts && npm run typecheck`
Expected: 图表回归和链接测试通过，TypeScript 无错误。

### Task 5: 实现抽屉标签状态与工作区文件视图

**Files:**
- Create: `frontend/src/components/workspaceFileTabs.ts`
- Create: `frontend/src/components/workspaceFileTabs.test.ts`
- Create: `frontend/src/components/WorkspaceFileDrawer.tsx`
- Create: `frontend/src/components/WorkspaceFileDrawer.css`
- Modify: `frontend/src/api.ts`（若现有 `api()` JSON helper 不支持 blob/arrayBuffer，新增独立 `apiBlob()`；勿改变既有 API 调用语义。）

- [x] **Step 1: 为标签和未保存转换写失败测试**

实现前先为 reducer 契约写 Vitest：文件标签以 `{kind:'file', path, name, content, revision, dirty, previewKind}` 表示；网页标签以 `{kind:'web', href, title}` 表示。测试 open-existing 聚焦已有标签、open-new 添加标签、close-active 选择相邻标签、`markDirty`、`replaceSavedContent` 清理 dirty、session reset 清空全部。测试 `resolveDirtyDecision(choice)` 对 save/discard/cancel 的状态转换；cancel 不关闭标签/抽屉，save 只有在保存成功后才关闭。

- [x] **Step 2: 确认标签测试按预期失败**

Run: `cd frontend && npm test -- --run src/components/workspaceFileTabs.test.ts`
Expected: 模块不存在导致失败。

- [x] **Step 3: 实现标签 reducer 和 API 调用封装**

在 `workspaceFileTabs.ts` 导出上述类型与纯 reducer，避免在 Drawer 中分散处理 session race 和 active tab 边界。文件 API 所有路径通过 `URLSearchParams` 编码。列表/文本 API 继续使用 `api<T>()`；媒体 preview/download 使用 fetch blob，检查非 2xx 响应并及时 revoke object URL。加载时使用 `AbortController`；session ID 或 workspace path 变化时 abort 旧请求、清空旧 session 标签，避免迟到响应污染新会话。

- [x] **Step 4: 实现抽屉、文件树、编辑器和预览组件**

`WorkspaceFileDrawer` props 至少包含 `sessionId: string`、`open: boolean`、`executionMode: 'local' | 'sandbox'`、`onClose()`。本机模式加载目录列表；sandbox 显示设计文档规定的空状态并不请求文件 API。目录点击展开/折叠，文件点击建立标签。文本编辑器使用 `<textarea>`，支持保存按钮、Ctrl/Cmd+S、dirty 状态、revision 冲突提示；保存错误时保留草稿。PNG/JPEG/GIF/WebP、PDF、允许的媒体 blob 使用对应预览，unknown binary 展示 metadata 与下载按钮；HTML/SVG 永远不在 iframe 中。文件树显示加载/空目录/权限错误状态。

网页标签 `<iframe>` 仅接受已解析的 HTTP(S)，设置 `sandbox="allow-scripts"`、标题、referrer policy 为 `no-referrer`；不增加 `allow-same-origin`、`allow-top-navigation`、`allow-popups`。iframe load 不足以可靠判定跨域 CSP 拒绝，因此给所有网页标签固定显示“若页面无法嵌入，请在浏览器打开”操作；该操作仅由用户点击触发 `window.open(href, '_blank', 'noopener,noreferrer')`。不得把远程网页响应代理进本地应用。

- [x] **Step 5: 运行抽屉状态单元测试及前端类型检查**

Run: `cd frontend && npm test -- --run src/components/workspaceFileTabs.test.ts src/chatLinks.test.ts && npm run typecheck`
Expected: 标签 reducer 和链接状态测试通过，TypeScript 无错误。

### Task 6: 集成聊天页面、并排布局和会话隔离

**Files:**
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/styles.css`
- Modify: `frontend/src/components/WorkspaceFileDrawer.css`
- Modify: `frontend/src/ChartMessageContent.tsx`

- [x] **Step 1: 将抽屉挂到正式聊天页**

在 `ScopedApp` 的 chat 页面作用域持有 `fileDrawerOpen` 状态。聊天 topbar 加“文件”按钮，不添加任何 sidebar/nav 项。将聊天内容与抽屉包在同一 `chat-workspace-layout` 中；drawer 打开时聊天栏变窄、drawer 固定合理宽度，空间不足时抽屉在可用主面板内占满宽度。按钮带 `aria-expanded` / `aria-controls`，drawer 有标题、关闭按钮、Escape 关闭和合理 focus 状态。

- [x] **Step 2: 同步当前会话工作区状态**

`WorkspacePicker` 已通过 `onChoice` 报告 group/mode，但抽屉要拿当前 session root。修改 `frontend/src/components/WorkspacePicker.tsx` 可选增加 `onWorkspace(view)` 回调，将其已加载的 `WorkspaceView` 及 apply 后的结果上报；`App.tsx` 将当前 `{sessionId, path, execution_mode}` 存入 state，session change 时立即清空 path 并关闭旧标签；workspace path/mode change 时关闭旧文件标签。不要用客户端路径作为后端 root 参数，路径仅用于 UI 显示/切换检测，后端每个请求仍按 session 查根目录。

- [x] **Step 3: 接线聊天链接回调**

Markdown 相对文件 link 调 `openWorkspaceFile(path)`，若当前没有 session/local workspace 则显示提示，不发请求；否则打开抽屉并把文件放入对应标签。HTTP(S) link 调 `openWebLink(href)`，打开抽屉并添加/聚焦网页标签。用户从聊天点击链接时不在抽屉已打开状态下导航离开聊天。抽屉关闭时若有 dirty 文件弹确认对话框；提供保存/放弃/取消，save failure 保持 drawer 打开和内容不变。避免使用原生 confirm 嵌套多个 dirty tabs；一次只处理当前 dirty tab，取消则保留所有状态。

- [x] **Step 4: 实现响应式布局和可访问样式**

在 `styles.css` 为 chat 主体加 `min-width:0`、flex/grid shrink 规则；抽屉自身样式放 `WorkspaceFileDrawer.css`。宽屏下聊天和抽屉并列，窄屏下主聊天导航保持原样、抽屉在 `.main-panel` 内全宽显示；确保编辑器/文件树可单独滚动、焦点轮廓可见、按钮有 aria label，尊重 `prefers-reduced-motion`。

- [x] **Step 5: 运行前端全部测试、类型检查和 build**

Run: `cd frontend && npm test && npm run typecheck && npm run build`
Expected: Vitest 全部通过，TypeScript 通过，Vite 产物构建成功。

### Task 7: 端到端回归与安全验证

**Files:**
- Modify: `tests/test_workspace_files.py`（补边界回归）
- Modify: `frontend/src/chatLinks.test.ts` 和 `frontend/src/components/workspaceFileTabs.test.ts`（补竞态回归）

- [x] **Step 1: 后端全量相关测试**

Run: `pytest tests/test_workspace_files.py tests/test_session_workspaces.py tests/test_workspace_chat.py tests/test_chat_images.py -q`
Expected: 全部通过；特别确认 sandbox 不访问本机路径、下载响应 `nosniff`、HTML/SVG 不可预览、越界/符号链接不会读写。

- [x] **Step 2: 全量前端自动化检查**

Run: `cd frontend && npm test && npm run typecheck && npm run build`
Expected: 全部通过。

- [x] **Step 3: 手工验收核心流程**

在应用内验证：创建本机会话并选工作目录；从聊天按钮打开抽屉，展开目录；编辑 `.md` 后保存并从磁盘确认内容；打开 PNG/PDF 和不可预览的二进制；开多个标签并切换；dirty 时关闭/切换会话分别选择取消、放弃和保存；点击 `[文件](relative/path)` 和 HTTPS 链接；sandbox 会话显示无文件状态；窄屏抽屉不遮挡主导航。确认网页 iframe 不能导航宿主、弹窗或访问本机文件，链接仍有显式外部浏览器回退。

- [x] **Step 4: 审查最终 diff 和工作区状态**

Run: `git diff --check && git status --short`
Expected: 无 whitespace 错误；只报告本任务改动和开始时已有的工作区改动。不要覆盖或清理开始时已有的用户改动；不在未经请求时提交或推送。
