# 本地 Python 定时任务 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 提供用户可管理的通用本地 Python Cron 任务，支持确认版本、启停、手动运行、超时、输出限制和运行记录。

**Architecture:** SQLite 保存任务配置、脚本确认哈希和运行记录；纯 Python 调度器在 FastAPI lifespan 中启动，使用应用内部五段 Cron/时区计算器，不依赖系统 Cron。每次运行在独立 Python 子进程中执行，不经 shell，不补跑离线任务，不允许同一任务重叠。前端提供管理和运行记录页面；任务不作为 Agent Tool 暴露。

**Tech Stack:** Python 3.11+、FastAPI、SQLite、zoneinfo、subprocess、React/TypeScript、pytest。

---

## 文件结构与责任

- `backend/app/db.py`：schema 19，增加 `scheduled_tasks`、`scheduled_task_runs`。
- `backend/app/scheduler.py`：Cron 解析/下一次触发计算、调度循环、子进程执行和生命周期管理。
- `backend/app/routes/scheduled_tasks.py`：任务 CRUD、语法校验、启停确认、立即运行、运行记录 API。
- `backend/app/main.py`：lifespan 启动/停止 scheduler，注册路由/fallback。
- `backend/app/routes/data_transfer.py`：备份任务配置（不导出运行历史中的无限输出；脚本属于配置，按用户明确备份）。
- `frontend/src/pages/ScheduledTasksPage.tsx`：任务表单、列表、输出记录。
- `frontend/src/App.tsx`、`styles.css`：侧栏/路由/样式。
- `pyproject.toml`：如实现需要，增加 cron 解析依赖；优先使用内置解析器减少依赖。
- `tests/test_scheduler_schema.py`、`test_scheduler.py`、`test_scheduler_api.py`、`test_scheduler_data_transfer.py`、`test_frontend_scheduler.py`：测试。

## 实施步骤

### Task 1: schema 与 Cron 核心

**Files:** `backend/app/db.py`, `backend/app/scheduler.py`, `tests/test_scheduler_schema.py`, `tests/test_scheduler.py`

- [ ] 写失败测试：schema 19 表字段/索引和重复初始化；Cron 支持 `*`、数字、逗号、范围、步进五段；非法字段/时区/空表达式拒绝；下一次时间在指定 `ZoneInfo` 下正确；DST 不崩溃；任务同一 ID 不重叠。
- [ ] 运行专项测试确认失败。
- [ ] 增加 schema 19：任务表含 `id/name/script/cron_expression/timezone/enabled/confirmed_hash/confirmed_at/next_run_at/last_run_at/created_at/updated_at`；运行表含 task_id、trigger、scheduled_at、started_at、finished_at、status、exit_code、stdout、stderr、truncated、duration_ms；任务删除级联运行记录；所有输出字段有应用层上限。
- [ ] 实现纯 `CronSchedule`：五段字段解析、范围/步进匹配、按分钟计算下一次；`validate_timezone` 使用 `zoneinfo.ZoneInfo`；`script_hash` SHA-256；不调用 shell 或执行脚本。
- [ ] 运行 schema/Cron 单元测试。

### Task 2: 安全子进程执行与 Scheduler

**Files:** `backend/app/scheduler.py`, `tests/test_scheduler.py`

- [ ] 写失败测试：成功脚本捕获 stdout；失败脚本保存退出码/stderr；超时后状态 timeout；输出超过限制被截断；使用 `sys.executable -I`、临时工作目录、无 shell；任务停用/版本 hash 不匹配不运行；手动和调度共享入口；任务运行时第二次返回 skipped/overlap；后端停止将运行标记 interrupted；不补跑 past due 触发。
- [ ] 实现 `run_script(task, trigger)`：确认 enabled/hash（手动调用也要求 confirmed hash）、创建运行记录，`subprocess.Popen([sys.executable, '-I', script_path], shell=False, cwd=tempdir, env=minimal_env)`；脚本内容写入受控临时目录；限制 stdin、超时和 stdout/stderr bytes；finally 清理目录并更新运行记录。不把脚本输出写日志或聊天。
- [ ] 实现 `Scheduler.start/stop`：后台 asyncio task 每 10-30 秒检查到期任务；启动时将旧 `running` 标记 interrupted，next_run 从当前时间重算；使用 per-task lock；执行结束更新 next_run；暂停/编辑由 API 取消或无效化旧版本。
- [ ] 运行调度/子进程测试。

### Task 3: 定时任务 API

**Files:** `backend/app/routes/scheduled_tasks.py`, `backend/app/main.py`, `tests/test_scheduler_api.py`

- [ ] 写失败测试：新建默认 disabled；创建/编辑校验名称、脚本长度、Cron、时区和 Python AST；启用必须传当前脚本 hash/confirmed=true；编辑脚本清除确认并停用；暂停/启用；立即运行要求确认且不改 next_run；运行中重复调用 409；删除策略；列表不含完整输出，详情/运行记录分页且输出限长。
- [ ] 实现 `/api/scheduled-tasks` GET/POST/PATCH/DELETE、`/{id}/enable`、`/{id}/disable`、`/{id}/run`、`/{id}/runs`、`/validate`。错误不返回主机路径/环境变量秘密；脚本只存应用数据库，不能通过 URL 传递。
- [ ] 在 lifespan 创建 scheduler，退出时 await stop；路由调用同一 scheduler；任务 API 不注册到 ToolRegistry。将 `scheduled-tasks` 加入 frontend fallback。
- [ ] 运行 API 与现有 startup/recovery 测试。

### Task 4: 备份恢复

**Files:** `backend/app/routes/data_transfer.py`, `tests/test_scheduler_data_transfer.py`

- [ ] 写失败测试：导出任务配置、Cron、时区和脚本/确认 hash；不导出运行历史完整输出或内部临时路径；导入新 ID、重置 enabled=false/confirmed_hash=null/next_run=null；非法 Cron/时区/超长脚本拒绝；旧备份缺字段兼容。
- [ ] 扩展 ImportPayload/export/import/preview；脚本按配置备份属于明确功能，导入后必须重新确认启用；运行记录不恢复（或仅恢复有限摘要，首版选择不恢复）。
- [ ] 运行全部 data transfer 测试。

### Task 5: 前端任务页面与导航

**Files:** `frontend/src/pages/ScheduledTasksPage.tsx`, `frontend/src/App.tsx`, `frontend/src/styles.css`, `tests/test_frontend_scheduler.py`

- [ ] 写源码契约测试：侧栏“定时任务”、路由；名称/脚本/Cron/时区/超时输入；状态、下次运行、确认启用、暂停、立即运行、删除；输出/错误和截断标记；无 Agent 自动创建/启用控件。
- [ ] 实现页面：列表和新建/编辑表单；保存后显示 hash/确认状态；启用按钮二次确认并提交当前 hash；运行记录展开查看；错误和冲突信息清晰；脚本 textarea 明示“非沙箱，以本机后端账户权限执行”。
- [ ] App 增加导航 active 状态、路由和 fallback；CSS 复用 settings/plugin 风格并适配窄屏。
- [ ] 运行前端测试和 build。

### Task 6: 完整验收

- [ ] `python -m pytest -q --ignore=tests/test_installer_idempotent.py`。
- [ ] 前端专项测试与 `npm --prefix frontend run build`。
- [ ] smoke：创建暂停任务，确认后启用；手动运行成功/失败/超时；检查输出截断和历史；修改脚本后旧确认失效；暂停不触发；重启不补跑；同一任务不重叠。

## 执行约束

- 脚本是用户可信代码，子进程不是安全沙箱；界面和文档必须明确说明。
- 不经 shell 执行，不接收模型/Agent 创建、启用或触发任务。
- 不补跑离线期间任务；不允许同一任务并发。
- 输出和错误限长；不把任务输出写入聊天、模型上下文或普通日志。
- 当前工作区不是 Git 仓库，不执行提交。
