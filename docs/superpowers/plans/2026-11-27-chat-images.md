# Chat Images Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Display generated Skill images inline in normal and debug chat without serving arbitrary local paths.

**Architecture:** Read the successful local Skill CLI's structured `delivery` entries, accept bounded raster files that resolve within the current workspace, and snapshot them behind opaque same-origin URLs. Persist file references in the assistant message for normal chat; debug previews are temporary. Both views consume an `images` stream event and use one renderer.

**Tech Stack:** FastAPI, SQLite message metadata, SSE, React, TypeScript, pytest, Node test runner.

---

### 1. Server contract

- [ ] In `tests/test_chat_images.py`, use a mock provider and local Skill executor to return a PNG path under the selected workspace plus rejected non-image and out-of-workspace paths. Assert the SSE `images` event, authorized HTTP fetch, history and branch behavior, and that debug does not persist messages.
- [ ] Observe tests fail before implementing. Create `backend/app/chat_images.py`, mount its routes from `main.py`, and attach images only to successful Skill CLI results in `routes/sessions.py` and `routes/debug.py`. Persist references under `metadata_json.images` in normal chat.
- [ ] Verify the returned format is restricted, routes never accept filesystem paths, and invalid attachment IDs or unrelated messages cannot retrieve the image.

### 2. Inline display

- [ ] In `frontend/tests/chatImages.test.mjs`, render a shared component with an allowed local URL and an external URL; assert the allowed image is present and clickable, and the external one is not rendered.
- [ ] Observe the test fails and implement `frontend/src/components/ChatImages.tsx`, wiring `images` into `App.tsx` and `pages/AgentDebugChat.tsx`.
- [ ] Run Python regressions, frontend tests and production build; no real image API call is needed.
