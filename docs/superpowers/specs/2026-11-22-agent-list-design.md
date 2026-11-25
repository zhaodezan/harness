# Agent List and Switching Design

**Status:** Approved by user on 2026-11-22  
**Scope:** Add a visible agent list and agent switching to the existing local web UI.

## Goal

Make saved agents discoverable and selectable from the UI without changing the existing agent persistence API or introducing a separate management page.

## Design

- Add an agent list to the left sidebar, adjacent to the existing “智能体” navigation entry and before the recent-session list.
- Render each loaded agent by name and visibly mark the active agent.
- Clicking an agent selects it, then clears the currently displayed session, messages, plan, and session-specific context before loading that agent’s sessions. This prevents chat content from one agent appearing under another.
- Keep the existing “智能体” entry and settings button behavior for editing the active agent. Keep the existing creation flow; do not add new CRUD workflows beyond selection/list rendering.
- Preserve existing behavior when the API returns no agents: the setup wizard remains available.

## Data flow and errors

- Reuse the existing `agents` state populated by `GET /api/agents`; do not add a new backend endpoint or persistence mechanism.
- Use the existing `agentId` state as the single source of truth for selection. The existing session-loading effect will load sessions for the newly selected agent.
- If the agents request fails, retain the current error notice behavior; do not render fabricated agent entries.

## Acceptance checks

1. With multiple agents returned by `GET /api/agents`, the sidebar lists all of them and identifies the active one.
2. Selecting a different agent updates the active-agent name and loads only that agent’s sessions.
3. The previous agent’s messages and session context are cleared during the switch.
4. With no saved agents, the existing setup wizard still appears and can create an agent.
5. Existing frontend typecheck/build and backend tests remain green.
