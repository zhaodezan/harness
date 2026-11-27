# Expandable Tool Traces Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show tool input, output, and errors on demand in current chat traces without storing the details.

**Architecture:** Both chat routes attach bounded previews to their tool-completion stream events. Normal chat matches these live previews to persisted summary rows by event ID; the shared React panel renders details on demand.

**Tech Stack:** FastAPI, SQLite, SSE, React, TypeScript, pytest, Node test runner.

---

### Task 1: Stream safe previews without persisting them

**Files:** `tests/test_chat_run_trace.py`, `backend/app/runtime/tool_trace.py`, `backend/app/events.py`, `backend/app/routes/sessions.py`, `backend/app/routes/debug.py`.

- [x] Extend the existing parametrized tool trace matrix (workspace, published, debug; success/failure) to check `completed['tool_details']` for matching input and the appropriate `output` or `error` preview. Check summary/log privacy separately. Add a preview test for credential redaction and the 4,000-character cap.
- [x] Run `python -m pytest tests/test_chat_run_trace.py -q`; expect missing `tool_details` and missing preview helper failures.
- [x] Implement `tool_trace_details(tool_name, arguments, outcome)` returning `tool_name`, `input: {text, truncated}`, and exactly one of `output` or `error` with the same preview shape. Redact named credential fields before serialization. Return inserted SQLite IDs from `record_event`, and use the returned ID plus the optional details only in the normal chat SSE payload. Give debug tool completions distinct stream-local IDs; attach previews to the completion trace frame.
- [x] Run `python -m pytest tests/test_chat_run_trace.py tests/test_logs.py tests/test_log_filters.py -q`; expect pass.

### Task 2: Render and preserve expandable rows

**Files:** `frontend/src/runTrace.ts`, `frontend/src/pages/AgentDebugChat.tsx`, `frontend/src/components/RunTracePanel.tsx`, `frontend/src/components/RunTracePanel.css`, `frontend/tests/runTrace.test.mjs`, `frontend/tests/runTracePanel.test.mjs`.

- [x] Test `mergeRunTraces` with identical tool summaries and distinct IDs: after saved terminal rows arrive, each completion retains only its matched live `tool_details`. Render the shared panel and verify details use a normally closed native disclosure, include tool name and previews, escape data, and omit the disclosure for summary-only rows.
- [x] From `frontend/`, run `node --test --test-concurrency=1 tests/runTrace*.test.mjs`; expect failures from missing overlay and disclosure.
- [x] Extend `RunTrace` with `id?: number` and `tool_details?: ToolTraceDetails`, overlay only for matching run/event IDs, and carry both fields through debug SSE parsing. Render native `details/summary` for rows with previews, with explicit input/output/error labels and truncation notice.
- [x] From `frontend/`, run `node --test --test-concurrency=1 tests/*.test.mjs` and `npm run build`; expect pass.
