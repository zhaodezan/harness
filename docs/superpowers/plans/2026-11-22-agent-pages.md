# Dedicated Agent Pages Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the compact agent sidebar list and modal editor with dedicated agent-list and agent-configuration routes, and navigate to the chat route when an agent is selected for use.

**Architecture:** Add React Router and keep the existing shared conversation/session state in `App.tsx`. Extract the agent list and configuration form into route page components; the list’s **使用** action selects an agent and navigates to `/chat`, while **配置** navigates to a dedicated create/edit route. Extend FastAPI’s static frontend handler to serve the SPA for known UI paths while retaining 404 responses for unknown API paths.

**Tech Stack:** React 19, TypeScript, React Router, Vite, FastAPI, pytest.

---

## File map

- Modify `frontend/package.json` and `frontend/package-lock.json`: add React Router.
- Modify `frontend/src/main.tsx`: wrap the app in `BrowserRouter`.
- Create `frontend/src/api.ts`: share the existing JSON API helper between the shell and configuration page.
- Modify `frontend/src/App.tsx`: own shared agent/chat state, render route navigation and route elements, remove the in-sidebar agent list and modal configuration UI.
- Create `frontend/src/pages/AgentListPage.tsx`: render the standalone agent list and its **配置**/**使用** actions.
- Create `frontend/src/pages/AgentConfigPage.tsx`: own the existing create/edit form and setup flow as a full page.
- Modify `frontend/src/styles.css`: style the routed list/configuration pages and responsive navigation.
- Modify `backend/app/main.py`: serve the built SPA for valid client-side routes.
- Modify `tests/test_frontend.py`: test direct-route SPA serving and ensure unknown API paths remain 404.
- Create `tests/test_frontend_routes.py`: guard the frontend route/action wiring using the repository’s existing source-contract testing pattern; supplement it with a live smoke check.

The current full pytest baseline has two unrelated failures in `tests/test_installer_idempotent.py` (`install_commands()` rejects `frontend_modules_exists`). Do not change installer behavior as part of this feature; report whether those baseline failures remain after the implementation.

### Task 1: Support direct navigation to frontend routes

**Files:**
- Modify: `tests/test_frontend.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: Add failing FastAPI tests for supported UI routes and API 404 behavior**

Add these tests to `tests/test_frontend.py` (also import `pytest`):

```python
@pytest.mark.parametrize(
    "path",
    ["/chat", "/agents", "/agents/new", "/agents/example-id/config"],
)
def test_frontend_routes_serve_built_app(tmp_path, path):
    response = TestClient(create_app(tmp_path / "web.sqlite3")).get(path)

    assert response.status_code == 200
    assert '<div id="root"></div>' in response.text
    assert "前端尚未构建" not in response.text


def test_unknown_api_route_remains_404(tmp_path):
    response = TestClient(create_app(tmp_path / "web.sqlite3")).get("/api/not-a-route")

    assert response.status_code == 404
```

- [ ] **Step 2: Run those tests and verify the client routes fail before the implementation**

Run: `python -m pytest tests/test_frontend.py -q`
Expected: `/` test passes, the new client-route cases fail with 404, and the API 404 case passes.

- [ ] **Step 3: Add an explicit SPA fallback for supported frontend paths**

In `backend/app/main.py`, import `HTTPException` and add a catch-all route after all API routes and the assets mount. Return the same built `index.html` response used by `/` only for `chat`, `agents`, `agents/new`, and `agents/<id>/config`; raise `HTTPException(status_code=404)` for all other paths. This preserves API 404s and avoids returning the SPA for arbitrary unknown paths.

- [ ] **Step 4: Re-run the frontend-serving tests**

Run: `python -m pytest tests/test_frontend.py -q`
Expected: all six cases pass (root, four UI routes, unknown API route).

### Task 2: Add client routes and the dedicated agent-list page

**Files:**
- Modify: `frontend/package.json`
- Modify: `frontend/package-lock.json`
- Modify: `frontend/src/main.tsx`
- Create: `frontend/src/api.ts`
- Modify: `frontend/src/App.tsx`
- Create: `frontend/src/pages/AgentListPage.tsx`
- Create: `tests/test_frontend_routes.py`

- [ ] **Step 1: Install React Router and wrap the app in `BrowserRouter`**

Run: `npm --prefix frontend install react-router-dom`

In `frontend/src/main.tsx`, import `BrowserRouter` from `react-router-dom` and render `<App />` inside `<BrowserRouter>`. Keep `React.StrictMode` around the router.

- [ ] **Step 2: Extract the existing API helper into `frontend/src/api.ts`**

Move the current `api<T>(path, init)` implementation from `App.tsx` unchanged into `api.ts`, export it, and import it from both `App.tsx` and the configuration page in Task 3. Preserve JSON headers, response parsing, 204 handling, and API error messages.

- [ ] **Step 3: Add a route-wiring regression test and verify RED**

Create `tests/test_frontend_routes.py` with source-contract checks that `main.tsx` wraps `App` in `BrowserRouter`, the app declares `/chat`, `/agents`, `/agents/new`, and `/agents/:agentId/config`, and the list page renders separate **配置** and **使用** actions. Run `python -m pytest tests/test_frontend_routes.py -q` and confirm it fails because the new routes/page do not exist yet.

- [ ] **Step 4: Implement the standalone list page**

Create `AgentListPage.tsx` with typed props for `agents`, `activeAgentId`, `onUse(agentId)`, `onConfigure(agentId)`, and `onCreate()`. Render an empty state when no agents exist; otherwise render each agent’s name/model and distinct buttons. The **使用** button calls `onUse`; **配置** calls `onConfigure`; the create button calls `onCreate`.

- [ ] **Step 5: Add routing and navigation to `App.tsx`**

Use `Routes`, `Route`, `Navigate`, `useLocation`, and `useNavigate`. Make the sidebar **对话** and **智能体** items navigate to `/chat` and `/agents`, and derive their active class from the current location. Add routes for `/agents`, `/chat`, `/agents/new`, and `/agents/:agentId/config`. Remove the compact `agent-list` block from the sidebar. The **使用** callback must call the existing agent-selection state transition (clearing agent-scoped chat/session state), then navigate to `/chat`; the list’s **配置**/**新建** callbacks navigate to their respective configuration routes.

- [ ] **Step 6: Run route wiring checks and frontend typecheck**

Run: `python -m pytest tests/test_frontend_routes.py -q`
Expected: all route/action source checks pass.

Run: `npm --prefix frontend run typecheck`
Expected: exit code 0.

### Task 3: Convert the existing modal editor into a dedicated configuration page

**Files:**
- Create: `frontend/src/pages/AgentConfigPage.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/styles.css`
- Modify: `tests/test_frontend_routes.py`

- [ ] **Step 1: Add failing page-contract tests**

Extend `tests/test_frontend_routes.py` to assert that the configuration page has a full-page `agent-config-page` container, handles both create and edit routes, retains the model-connection test and **保存设置/创建智能体** actions, and exposes an invalid-agent not-found state. Run the focused test and verify the new assertions fail before adding the page.

- [ ] **Step 2: Move configuration form state and handlers into `AgentConfigPage.tsx`**

Move the current form’s controlled fields, setup diagnostics, template presets, model connection test, Skills discovery, prompt preview/history, credential update, and create/edit requests into the page component. For an existing `agentId`, fetch `/api/agents/{id}` and populate the form; for `/agents/new`, initialize the current defaults and preserve the two-step connection-test wizard. On successful save, call `onSaved(savedAgent)`; on load 404 render a clear not-found state with a link back to `/agents`; keep other API errors on the page.

- [ ] **Step 3: Wire save behavior and remove modal-only state from `App.tsx`**

The parent callback updates the shared `agents` array without changing the active chat agent, then navigates to `/agents`. Remove `showSettings`, `editingAgentId`, and the configuration-only form state/handlers from `App.tsx`. Keep unrelated Memory, MCP, Logs, approvals, import/export, and chat state intact. Preserve first-run behavior by routing `/` to `/agents/new` when agents have not loaded or when the loaded list is empty; otherwise route `/` to `/chat`.

- [ ] **Step 4: Add page styles**

In `styles.css`, add scoped styles for `.agent-list-page`, `.agent-config-page`, agent cards/actions, and page-level form layout. Reuse existing color tokens, `.settings-card` field styles where appropriate, focus-visible behavior, and mobile breakpoints. Do not use the modal backdrop for the configuration route.

- [ ] **Step 5: Verify the config-page contract and typecheck**

Run: `python -m pytest tests/test_frontend_routes.py -q`
Expected: all frontend route/page contract tests pass.

Run: `npm --prefix frontend run typecheck`
Expected: exit code 0.

### Task 4: Verify production routing and agent workflows

**Files:**
- No additional source files unless verification reveals a defect.

- [ ] **Step 1: Build the production frontend**

Run: `npm --prefix frontend run build`
Expected: TypeScript check succeeds and Vite completes the production build.

- [ ] **Step 2: Run focused backend and frontend tests**

Run: `python -m pytest tests/test_frontend.py tests/test_frontend_routes.py tests/test_frontend_styles.py -q`
Expected: all focused tests pass.

- [ ] **Step 3: Run the full repository suite**

Run: `python -m pytest -q`
Expected: report every failure. The known pre-feature baseline currently has 2 installer test failures; the new route/frontend tests must pass and must not increase that failure count.

- [ ] **Step 4: Smoke-test browser routes through both servers**

With backend `python run.py --no-browser` and frontend `npm --prefix frontend run dev -- --host 127.0.0.1` running, verify HTTP 200 for `/`, `/agents`, `/agents/new`, `/agents/<existing-id>/config`, and `/chat` on the backend and Vite server; verify `/api/not-a-route` remains 404. Open `http://127.0.0.1:5173/agents`, confirm each saved agent has separate **配置** and **使用** actions; verify **配置** opens the full page and **使用** selects the agent and navigates to `/chat`. Confirm refresh, browser back/forward, empty-agent first-run redirect, and invalid-ID state.

- [ ] **Step 5: Verify changed paths and report the remaining baseline failures**

Run: `git status --short` if the workspace is a Git repository; otherwise enumerate the modified files directly. Report frontend build, focused test, full-suite, and live route outcomes separately. Do not claim the full suite passes if the known installer failures remain.
