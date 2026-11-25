# Agent List and Switching Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Display saved agents in the left sidebar and let the user switch the active agent without leaking the prior agent’s conversation into the new view.

**Architecture:** Reuse the `agents` array and `agentId` state already loaded by `frontend/src/App.tsx`. Add a compact selectable agent list in the sidebar, and a single selection handler that resets agent-scoped conversation state before `agentId` changes; the existing session-loading effect will then load the selected agent’s sessions. No backend or dependency changes are needed.

**Tech Stack:** React 19, TypeScript, Vite, existing CSS, pytest-based repository checks.

---

## File map

- Modify `frontend/src/App.tsx`: render the agent selector and implement the transition/reset behavior using existing state and endpoints.
- Modify `frontend/src/styles.css`: style the list, selected state, empty state, and narrow-sidebar presentation.
- No backend changes: `GET /api/agents` and the session APIs already supply the required data.
- No new test framework or dependencies: validate with TypeScript/build checks and interact with the running app, which is the repository’s current frontend verification pattern.

### Task 1: Add agent switching and sidebar list

**Files:**
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: Confirm the starting behavior**

Run: `python -c "import urllib.request; r=urllib.request.urlopen('http://127.0.0.1:8765/api/agents'); print(r.status, len(__import__('json').loads(r.read())))"`
Expected: `200` and an agent count greater than zero. If the development servers are not running, start the backend with `python run.py --no-browser` and frontend with `npm --prefix frontend run dev -- --host 127.0.0.1`.

- [ ] **Step 2: Add a single selection handler before the JSX return**

In `frontend/src/App.tsx`, add `selectAgent` near the existing `openAgentSettings` function. It should no-op for the current ID, abort any active response, clear data belonging to the old agent, and then set the new ID:

```tsx
  const selectAgent = (nextAgentId: string) => {
    if (nextAgentId === agentId) return;
    activeController?.abort();
    setSessions([]);
    setSessionId('');
    setMessages([]);
    setContextSummaries([]);
    setTrace([]);
    setPlan('');
    setBranchMessageId('');
    setDraft('');
    setShowTrace(false);
    setAgentId(nextAgentId);
  };
```

The existing `loadSessions(agentId)` effect will fetch sessions for the new ID. Its existing session-change effect will load messages and context summaries for the selected session.

- [ ] **Step 3: Render agents before the recent-session section**

In the sidebar JSX in `frontend/src/App.tsx`, immediately after the existing navigation buttons and before `.session-label`, render one button per agent. Each button must use `agent.id` as its React key, call `selectAgent(agent.id)`, set `aria-pressed={agent.id === agentId}`, and use an active class for the selected agent. Include a first-character mark and the full name; use `title={agent.name}` so the name remains accessible when the sidebar is narrow. When no agents exist, show a short empty-state message without removing the existing setup wizard.

Use this JSX structure, adapting indentation to the surrounding compact JSX:

```tsx
      <div className="side-label agent-list-label">智能体</div>
      <div className="agent-list">
        {agents.map((agent) => <button
          type="button"
          key={agent.id}
          title={agent.name}
          aria-pressed={agent.id === agentId}
          className={`agent-list-item ${agent.id === agentId ? 'selected' : ''}`}
          onClick={() => selectAgent(agent.id)}
        >
          <span className="agent-list-mark" aria-hidden="true">{agent.name.slice(0, 1) || 'A'}</span>
          <span className="agent-list-name">{agent.name}</span>
        </button>)}
        {!agents.length && <div className="empty-agents">尚未创建智能体</div>}
      </div>
```

Keep the existing “智能体” settings navigation behavior. Do not add an agent CRUD endpoint or a second creation flow.

- [ ] **Step 4: Typecheck the frontend**

Run: `npm --prefix frontend run typecheck`
Expected: exit code 0 and no TypeScript errors.

### Task 2: Style the selectable list and verify the end-to-end interaction

**Files:**
- Modify: `frontend/src/styles.css`

- [ ] **Step 1: Add scoped styles after the existing sidebar/session rules**

Add rules for the selector, with a visible selected state and a constrained scroll area so long lists do not push the recent-session list out of the sidebar:

```css
.agent-list-label{margin-top:18px;margin-bottom:7px}
.agent-list{max-height:150px;overflow:auto}
.agent-list-item{width:100%;min-height:32px;display:flex;align-items:center;gap:8px;padding:0 8px;border:0;border-radius:6px;background:transparent;color:#727a73;text-align:left;font-size:10px}
.agent-list-item:hover,.agent-list-item.selected{background:#f0f4f0;color:#586d5d}
.agent-list-item.selected{font-weight:600}
.agent-list-mark{width:19px;height:19px;flex:0 0 19px;display:grid;place-items:center;border-radius:6px;background:#edf1ed;color:#718574;font-size:9px}
.agent-list-item.selected .agent-list-mark{background:#dfe9e0;color:#536c57}
.agent-list-name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.empty-agents{padding:7px 8px;color:#abb1ab;font-size:9px}
@media(max-width:620px){.agent-list-label{font-size:0}.agent-list{max-height:120px}.agent-list-item{justify-content:center;padding:0}.agent-list-name,.empty-agents{display:none}}
```

- [ ] **Step 2: Run the frontend production build**

Run: `npm --prefix frontend run build`
Expected: exit code 0; Vite completes the production build.

- [ ] **Step 3: Run the existing backend/repository test suite**

Run: `python -m pytest -q`
Expected: all tests pass. The suite includes checks that the built frontend is served and that required CSS selectors remain present.

- [ ] **Step 4: Exercise the UI with the running services**

Open `http://127.0.0.1:5173/`. Confirm that each record returned by `GET /api/agents` appears in the sidebar, the active one is highlighted, and clicking another agent changes the top-bar name and replaces the recent-session list. If the prior agent had a conversation, switch while it is displayed and confirm its messages/plan disappear before the other agent’s conversation loads. Confirm the app remains usable at a narrow viewport (the agent marks remain visible and identify each choice via the button title).

Expected: selecting any listed agent updates the active agent and only displays that agent’s sessions/messages; the browser console has no React key or runtime errors.
