# 数据库连接与 Agent 查询 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 支持本地 SQLite 与 MySQL 连接管理，并让 Agent 在明确授权后通过受限数据库工具查询；变更操作必须逐次审批。

**Architecture:** 应用 SQLite 保存非秘密连接配置，密码写入系统凭据库。`DatabaseManager` 为 SQLite/MySQL 提供统一的测试、结构读取、只读查询和变更执行接口；每个连接注册独立的 schema/query/write Tool，Agent 只选择连接 ID，运行时同时依赖 Tool 过滤和执行层校验。只读 SQL 仅允许单语句查询，写 SQL 使用 side-effect 风险等级复用现有审批。

**Tech Stack:** Python 3.11+、FastAPI、SQLite、PyMySQL、React/TypeScript、pytest/httpx。

---

## 文件结构与责任

- `backend/app/db.py`：schema 18，增加 `database_connections` 和 Agent 授权列。
- `backend/app/database_manager.py`：连接配置、凭据读取、SQL 安全校验、SQLite/MySQL 适配及动态 Tool 注册。
- `backend/app/routes/databases.py`：连接 CRUD、密码凭据、测试连接、结构查看 API。
- `backend/app/models.py`：Agent 数据模型增加 `selected_database_connections`。
- `backend/app/routes/agents.py`、`setup.py`、`sessions.py`、`debug.py`：保存授权并把授权传给 Tool 过滤。
- `backend/app/tools.py`：支持数据库 plugin ID 的过滤。
- `backend/app/main.py`：初始化 DatabaseManager、注册 router、启动时加载数据库 Tools。
- `backend/app/routes/data_transfer.py`：备份/恢复数据库非秘密配置和授权。
- `frontend/src/pages/DatabasePage.tsx`：数据库连接管理页。
- `frontend/src/pages/AgentConfigPage.tsx`、`AgentDebugChat.tsx`、`App.tsx`、`styles.css`：Agent 授权、导航和界面。
- `pyproject.toml`：增加 `pymysql` 运行依赖。
- `tests/test_database_schema.py`、`test_database_manager.py`、`test_database_api.py`、`test_agent_database_access.py`、`test_database_data_transfer.py`、`test_frontend_database.py`：覆盖新行为。

## 实施步骤

### Task 1: schema 与数据模型

**Files:** `backend/app/db.py`, `backend/app/models.py`, `tests/test_database_schema.py`

- [ ] 写失败测试：初始化后存在 `database_connections`，字段包括 `id/name/db_type/sqlite_path/host/port/database_name/username/readonly/last_tested_at/created_at/updated_at`；密码不在表中；Agent 有 `selected_database_connections_json`；重复初始化和删除列后重启修复幂等。
- [ ] 运行 `python -m pytest tests/test_database_schema.py -q`，确认因缺少 schema 18 失败。
- [ ] 增加 schema 18：`db_type` 仅允许 `sqlite/mysql`，默认 readonly=1；连接配置字段不存密码；Agent 授权默认 `[]`，增加索引。部分迁移用 `PRAGMA table_info`，表和索引用 `IF NOT EXISTS`。
- [ ] 在 `AgentCreate/AgentPatch/AgentResponse` 增加 `selected_database_connections`，默认空列表，最多 100 个 ID；运行 schema 与现有 DB 测试。

### Task 2: DatabaseManager 与安全 SQL 执行

**Files:** `backend/app/database_manager.py`, `pyproject.toml`, `tests/test_database_manager.py`

- [ ] 写失败测试：SQLite 临时库可测试/读表结构/查询；MySQL 适配通过 fake PyMySQL connection 测试参数传递；未知连接、错误凭据、超时返回脱敏错误；密码不出现在返回值；多语句、空语句、`ATTACH/DETACH/PRAGMA writable`、非查询 SQL 被只读入口拒绝；结果行数/字符限制生效；写入口接受 INSERT/UPDATE/DELETE/DDL 但不接受多语句。
- [ ] 运行测试确认模块不存在或行为失败。
- [ ] 实现 `DatabaseManager`：密码键为 `database:<id>:password`；SQLite 使用 `sqlite3.connect(f"file:{path}?mode=ro", uri=True)` 且拒绝应用数据库路径、非文件和符号链接；MySQL 使用 PyMySQL、连接超时和参数化执行。只读入口只允许单条 SELECT/WITH(无写关键字)/EXPLAIN，结构由适配器固定 SQL 提供；写入口只执行单条语句并由 Tool 的审批层控制。统一输出 `{columns, rows, row_count, truncated}`，行数最多 100、序列化最多 50,000 字符，异常移除密码/DSN。
- [ ] 实现 `register_tools()`：每个连接注册 `database:<id>:schema`、`:query`、`:write` plugin ID；schema/query 风险 `read_only`，write 风险 `side_effect`。handler 每次调用重新查询连接并再次检查连接存在与工具参数，不能从模型参数接收 host/path/password。
- [ ] 运行 manager 测试并补齐 SQLite 真库测试。

### Task 3: 数据库管理 API

**Files:** `backend/app/routes/databases.py`, `backend/app/main.py`, `tests/test_database_api.py`

- [ ] 写失败测试：SQLite/MySQL 配置创建、列表不含密码、编辑会清除测试时间、凭据设置/删除、测试连接成功；删除引用连接返回 409；删除只删应用配置不删 SQLite 源文件；结构接口返回表/字段；非法 db_type、路径、端口、空名称拒绝。
- [ ] 运行测试确认路由不存在。
- [ ] 实现 `/api/databases` CRUD、`/{id}/credentials`、`/{id}/test-connection`、`/{id}/schema`。SQLite 只接受现有文件路径，MySQL 主机/库名/用户名存非秘密字段；测试成功写 `last_tested_at`，修改目标或密码清空测试时间。删除前扫描 Agent JSON 引用，清理凭据但不删除源数据库。
- [ ] 在 `create_app` 设置/初始化 `application.state.database_manager`，加载现有连接 Tools，注册 database router；CRUD 后刷新对应动态 Tools。
- [ ] 运行数据库 API 和现有模型/工具测试。

### Task 4: Agent 授权与运行时工具过滤

**Files:** `backend/app/routes/agents.py`, `setup.py`, `sessions.py`, `debug.py`, `tools.py`, tests

- [ ] 写失败测试：Agent 可保存多个存在的连接 ID，未知 ID 422；未授权 Agent 的模型请求不包含数据库 Tools；授权后只出现该连接的 schema/query/write；删除引用被阻止；撤销授权后直接执行旧工具名也失败；debug 草稿授权可用但不改变持久 Agent。
- [ ] 扩展 `_AGENT_SELECT`、JSON 编解码和 `_validate_database_connections`；setup 也验证并保存授权；将授权列表传给 `filter_tools_for_agent`，其余 MCP/local 过滤保持不变。
- [ ] `sessions.py` 和 `debug.py` 查询 Agent/草稿授权，调用 `filter_tools_for_agent(..., selected_database_connections)`；调用 handler 前再次验证 plugin ID 与当前授权。write Tool 的风险自动进入已有 approval_required 流程，审批描述包含连接名称但不含密码或完整结果。
- [ ] 运行 `python -m pytest tests/test_agent_database_access.py tests/test_agent_model_binding.py tests/test_provider_tools.py tests/test_approval_roundtrip.py -q`。

### Task 5: 备份恢复

**Files:** `backend/app/routes/data_transfer.py`, `tests/test_database_data_transfer.py`, existing transfer tests

- [ ] 写失败测试：export 含数据库非秘密配置和 Agent 授权，不含 password/凭据键；import 重生成连接/Agent ID 并映射授权；恢复连接未测试；SQLite 路径可导入但不自动访问；未知授权 ID 和非法类型 422；旧备份没有字段时默认为空。
- [ ] 扩展 `ImportPayload`、export/import/preview：导入数据库连接先建 ID map，再创建 Agent；不接受备份中的 password 字段，若存在直接忽略并 warning；不把外部数据库内容写入备份。导入后动态刷新 Tools。
- [ ] 运行所有 data transfer 测试。

### Task 6: 前端数据库页与 Agent 配置

**Files:** `frontend/src/pages/DatabasePage.tsx`, `AgentConfigPage.tsx`, `AgentDebugChat.tsx`, `App.tsx`, `styles.css`, frontend tests

- [ ] 写源码契约测试：侧栏和 `/databases` 路由；SQLite/MySQL 表单；密码为 password input；列表不显示密码；测试/编辑/删除；Agent checkbox 授权和自动保存；debug 请求携带 ID；无未授权查询按钮。
- [ ] 实现数据库页面：类型切换、文件选择/路径输入、MySQL 字段、密码只写入 credentials endpoint、测试状态、结构查看、删除冲突展示。使用现有 `api` helper，不把密码放进 state 的列表对象。
- [ ] Agent 配置加载 `/databases` 摘要，显示连接名称/类型/只读状态，加入 PATCH/setup payload 和 debug config；没有连接时提供数据库页面入口。
- [ ] App 侧栏增加“数据库”、路由 fallback 增加 `databases`；保持知识库/长期记忆独立。运行前端测试和 `npm --prefix frontend run build`。

### Task 7: 数据库验收

- [ ] 运行 `python -m pytest -q --ignore=tests/test_installer_idempotent.py`。
- [ ] 运行数据库/前端专项测试与构建。
- [ ] smoke：创建 SQLite 连接、测试、查看结构；授权 Agent 后捕获模型请求确认只出现授权连接 Tools；查询结果进入工具上下文；write 工具触发审批；撤销/删除保护有效。

## 执行约束

- 密码只进系统凭据库；不进 JSON、备份、日志、SSE 或异常。
- Agent 默认只能查询；任何变更必须 side-effect approval；数据库账户本身仍负责最终权限。
- 不使用 shell 执行 SQL，不接受模型提供的连接地址、文件路径或密码。
- 当前工作区不是 Git 仓库，不执行提交。
