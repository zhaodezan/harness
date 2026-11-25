# Python Agent Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first usable vertical slice of the local Python Agent Harness: launchable local web app, persistent agent/session configuration, OpenAI-compatible chat, visible run events, and a polished React/TypeScript interface, then extend it through the approved specification in follow-on slices.

**Architecture:** A single-process FastAPI application serves a React/TypeScript frontend and a JSON/SSE API. Use standard-library SQLite access and explicit interfaces for provider/runtime/persistence; do not add an ORM, UI component library, or external service. This first slice establishes the running app and chat boundary; MCP, Skills, modes, memory, logs, and advanced session actions follow as independently testable slices.

**Tech Stack:** Python 3.13, FastAPI, Uvicorn, SQLite (`sqlite3`), `httpx` for OpenAI-compatible API calls, pytest/FastAPI TestClient; React, TypeScript, Vite, native CSS/SSE.

---

## Scope and delivery order

The requirements describe multiple independently testable areas. Implement in vertical slices without introducing microservices:

1. **Usable foundation:** application startup, API health, SPA shell, clean dependency boundaries.
2. **Persistent chat:** SQLite-backed agent settings/sessions/messages, model connection, ReAct tool loop interface and SSE events.
3. **Core harness controls:** tools/approvals, Skills, MCP transports, Plan-Execute, context/memory, logs, session branching/search.
4. **Finish:** launch helper, templates, security hardening, visual polish, docs and end-to-end checks.

This plan starts with slices 1 and 2; do not claim the full specification is implemented until slices 3 and 4 are complete.

## File structure

- `pyproject.toml` — Python package metadata and minimal dependencies/test configuration.
- `backend/app/main.py` — FastAPI application construction and route mounting.
- `backend/app/settings.py` — environment/config defaults and local paths.
- `backend/app/db.py` — SQLite initialization, connection and schema migration.
- `backend/app/models.py` — typed request/response data structures.
- `backend/app/routes/health.py` — health/readiness routes.
- `backend/app/routes/agents.py` — agent configuration CRUD.
- `backend/app/routes/sessions.py` — sessions/messages and chat orchestration endpoint.
- `backend/app/provider/openai_compatible.py` — provider adapter using `httpx`.
- `backend/app/runtime/engine.py` — model turn/stream boundary; future tool/mode integration point.
- `frontend/package.json`, `frontend/tsconfig.json`, `frontend/vite.config.ts`, `frontend/index.html` — frontend build configuration.
- `frontend/src/main.tsx`, `frontend/src/App.tsx`, `frontend/src/styles.css` — UI entry, shell and design system.
- `tests/test_health.py`, `tests/test_db.py`, `tests/test_agents.py`, `tests/test_chat.py` — isolated backend behavior tests.

## Task 1: Establish a testable Python application skeleton

**Files:** Create `pyproject.toml`, `backend/app/__init__.py`, `backend/app/main.py`, `backend/app/settings.py`, `backend/app/routes/__init__.py`, `backend/app/routes/health.py`, `tests/test_health.py`.

- [x] **Step 1: Write failing health endpoint test**

```python
from fastapi.testclient import TestClient
from backend.app.main import app


def test_health_returns_ok():
    response = TestClient(app).get('/api/health')
    assert response.status_code == 200
    assert response.json() == {'status': 'ok'}
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_health.py -q`
Expected: collection or assertion fails because `backend.app.main`/`/api/health` is not implemented. Fix test import setup if needed until failure is specifically missing app behavior.

- [ ] **Step 3: Implement the smallest app and route**

`backend/app/main.py` creates `FastAPI()` and includes the health router under `/api`; `backend/app/routes/health.py` returns the fixed JSON body above. `settings.py` exposes an app data directory from `AGENT_HARNESS_HOME`, defaulting to a per-user local directory without writing into the source tree.

- [ ] **Step 4: Verify GREEN**

Run: `python -m pytest tests/test_health.py -q`
Expected: `1 passed`.

## Task 2: Add SQLite persistence and migrations

**Files:** Create `backend/app/db.py`, `tests/test_db.py`; modify `backend/app/main.py`, `backend/app/settings.py`.

- [ ] **Step 1: Write failing schema initialization tests**

Test that initialization creates `agents`, `sessions`, `messages`, and `schema_migrations`; repeated initialization is safe; a new database uses the configured path. Use `tmp_path` and the real `sqlite3` module, not a mocked database.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_db.py -q`
Expected: failures because the database initializer/schema is absent.

- [ ] **Step 3: Implement minimal SQLite access**

Use `sqlite3.connect()` with foreign keys enabled, explicit schema version, transaction-wrapped migrations, and context-managed connections. Keep SQL and schema changes in `db.py`; do not introduce an ORM.

- [ ] **Step 4: Verify GREEN**

Run: `python -m pytest tests/test_db.py tests/test_health.py -q`
Expected: all tests pass, including repeat initialization.

## Task 3: Add agent and session APIs

**Files:** Create `backend/app/models.py`, `backend/app/routes/agents.py`, `backend/app/routes/sessions.py`, `tests/test_agents.py`; modify `backend/app/main.py`, `backend/app/db.py`.

- [ ] **Step 1: Write failing agent CRUD tests**

Test creating an Agent with name/provider/base URL/model/system prompt, listing it, updating it, and deleting it. Test invalid/missing names return 422. Use the real API and temporary SQLite database.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_agents.py -q`
Expected: route-not-found or equivalent failures.

- [ ] **Step 3: Implement CRUD and session endpoints**

Use FastAPI/Pydantic request models, parameterized SQL, generated UUIDs, timestamps in UTC, and bounded error messages. Never accept or return a secret as part of ordinary Agent JSON; store secret references separately until the credential-store slice is implemented. Add session creation/list/delete and message list endpoints with pagination.

- [ ] **Step 4: Verify GREEN**

Run: `python -m pytest tests/test_agents.py tests/test_db.py -q`
Expected: all agent and database tests pass.

## Task 4: Add OpenAI-compatible chat streaming

**Files:** Create `backend/app/provider/__init__.py`, `backend/app/provider/openai_compatible.py`, `backend/app/runtime/__init__.py`, `backend/app/runtime/engine.py`, `tests/test_chat.py`; modify `backend/app/routes/sessions.py`, `backend/app/db.py`.

- [ ] **Step 1: Write failing provider/runtime tests**

Test successful non-streaming provider response normalization, provider authentication/HTTP failure conversion, persistence of user/assistant messages, SSE event ordering, and cancellation/error event shape. Replace only the outbound HTTP transport in unit tests; do not call a real model/API.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_chat.py -q`
Expected: imports or assertions fail because provider/runtime behavior is absent.

- [ ] **Step 3: Implement the minimum adapter and chat turn**

Use one `httpx.AsyncClient` managed by FastAPI lifespan. Read the configured base URL/model/key from server-side config, issue requests to the OpenAI-compatible chat-completions endpoint, and stream normalized text/status/error SSE events. Persist complete user/assistant messages. Keep provider calls behind a small interface so later tools/modes do not depend on HTTP details. Redact credentials from all errors and logs.

- [ ] **Step 4: Verify GREEN**

Run: `python -m pytest tests/test_chat.py tests/test_agents.py tests/test_db.py tests/test_health.py -q`
Expected: all tests pass without network access.

## Task 5: Build the polished React/TypeScript chat and settings shell

**Files:** Create `frontend/package.json`, `frontend/tsconfig.json`, `frontend/vite.config.ts`, `frontend/index.html`, `frontend/src/main.tsx`, `frontend/src/App.tsx`, `frontend/src/styles.css`.

- [ ] **Step 1: Write/establish frontend checks first**

Add a minimal `npm test` script using the smallest test runner needed only if component tests are required; otherwise define `npm run typecheck` and `npm run build` as verification. Before writing UI, establish a failing typecheck/build from the missing entry points.

- [ ] **Step 2: Verify RED**

Run: `npm --prefix frontend run build`
Expected: fails because the app entry files are missing.

- [ ] **Step 3: Implement the initial UI**

Create a responsive, high-finish “高级简约” visual system using custom CSS and native browser APIs (no component library): conversational layout, conversation sidebar, message stream, composer, Agent/model setup form, run status and a collapsed execution-trace panel. Use clear Chinese microcopy, empty/loading/error states, accessible contrast/focus, and restrained motion. Wire chat submission to the SSE endpoint and render events without exposing secrets.

- [ ] **Step 4: Verify GREEN**

Run: `npm --prefix frontend run typecheck && npm --prefix frontend run build`
Expected: typecheck and production build pass.

## Task 6: Wire local startup and verify the vertical slice

**Files:** Create `run.py`, `README.md`, `.gitignore`; modify frontend build configuration and `backend/app/main.py` to serve the built SPA safely on loopback.

- [ ] **Step 1: Write startup smoke test**

Test the startup command's configuration validation and API health behavior without binding to a public interface; test that missing settings produce an actionable message.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_startup.py -q`
Expected: failure because the launcher/health readiness behavior is absent.

- [ ] **Step 3: Implement local launcher and docs**

Provide a single `python run.py` entry point, loopback-only binding, port-conflict diagnostics, browser auto-open, and clear prerequisite checks. Do not silently install or execute downloaded code. Document dependency installation and the supported environment.

- [ ] **Step 4: Verify GREEN and full slice**

Run: `python -m pytest -q`
Run: `npm --prefix frontend run typecheck && npm --prefix frontend run build`
Expected: backend tests pass and frontend typecheck/build pass. Start the app and verify health endpoint and browser UI manually; do not send any paid model request without user approval.

## Progress note

本计划初稿形成于实现开始前。之后项目已实现本机 FastAPI/React 应用、SQLite 数据、Agent/Prompt/变量、OpenAI 兼容聊天、ReAct/Plan-Execute、Python 与 MCP 工具及审批、Skills、长期记忆/上下文压缩、运行日志、会话管理、备份导入导出、首启向导/诊断、安装器和主要管理 UI。历史验证记录见会话；版本范围现已调整为暂停当前实现，后续未完成项统一纳入下一版本候选。

当前版本实现已按用户要求暂停。未完成项和后续增强均转列到需求说明书/README 的“下一版本候选”，本计划不再视为当前需要继续执行的任务。后续如重启实现，应先重新确认版本范围与优先级。

---

## Remaining specification slices

After this initial vertical slice, implement each area with the same test-first workflow: (a) session search and safe branches; (b) tool registry, risk policy and approvals; (c) Skills discovery; (d) MCP stdio/HTTP lifecycle; (e) Plan-Execute; (f) long-term memory and compaction; (g) persistent 30-day event/log search/export; (h) templates and visual QA. Multi-Agent, standalone published chat pages and custom page branding are explicitly next-version work.
