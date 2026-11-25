# Dedicated Agent List and Configuration Pages Design

**Status:** Approved by user on 2026-11-22  
**Supersedes:** `2026-11-22-agent-list-design.md` for agent navigation and list placement.  
**Scope:** Replace the compact sidebar agent list and modal editor with dedicated routed pages, and add a dedicated chat route.

## Goal

Separate agent discovery, agent configuration, and conversation into distinct pages while retaining the existing agent/session APIs and preserving the ability to switch the active agent.

## Navigation and routes

- `/agents`: dedicated agent list page. The sidebar “智能体” item navigates here. Each agent has a **配置** action and a **使用** action.
- `/agents/new`: dedicated new-agent configuration page.
- `/agents/:id/config`: dedicated configuration page for an existing agent.
- `/chat`: existing conversation interface.
- `/`: choose a useful entry based on persisted data: redirect to `/agents/new` when no agent exists; otherwise redirect to `/chat`.
- Sidebar includes explicit “对话” and “智能体” navigation. The previous compact in-sidebar agent list is removed.
- Browser back/forward and direct route loads must work.

## Interaction and data flow

- The list page loads agents from the existing `GET /api/agents` API.
- **配置** navigates to the corresponding configuration route. The existing configuration form and model connection test are preserved, but rendered as a full page instead of a modal.
- **使用** sets the selected `agentId`, clears old agent-scoped conversation state, and navigates to `/chat`. The chat page then loads only sessions and messages for that agent.
- Saving new or edited configuration returns to `/agents`; errors remain visible on the configuration page.
- If no agents exist, the first-run route directs to `/agents/new` and retains the existing setup wizard flow.
- Unknown agent IDs show a clear not-found state with a link back to `/agents`.
- No database schema or agent API changes are required.

## Routing and server behavior

- Use React Router rather than hand-rolled history state so route transitions and browser history are standard.
- Add the router dependency to `frontend/package.json` and its lockfile.
- Update the FastAPI frontend serving behavior to return the built `index.html` for non-API client-side routes, while preserving API 404s and the `/assets` static mount.
- The Vite development server should continue to use its SPA fallback and `/api` proxy.

## Scope boundaries

- Do not add agent deletion, duplication, sorting, filtering, or a new persistence mechanism.
- Do not change MCP dependencies or backend agent/session APIs.
- Keep the current visual language and responsive layout; the configuration form should remain usable on narrow screens.

## Acceptance checks

1. “智能体” opens `/agents`, where every saved agent has separate **配置** and **使用** actions.
2. **配置** opens a full-page editor for the selected agent; `/agents/new` opens the creation flow.
3. **使用** changes the active agent, navigates to `/chat`, and displays only that agent’s sessions/messages.
4. Refreshing or directly opening each supported route renders the correct page; browser back/forward restores prior pages.
5. `/` redirects to `/agents/new` when there are no agents and `/chat` otherwise.
6. Invalid agent configuration URLs show a not-found state without breaking the application.
7. API routes continue returning their existing responses and unknown `/api/...` requests are not swallowed by the SPA fallback.
8. Frontend typecheck/build and the full repository test suite pass.
