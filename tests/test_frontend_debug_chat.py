from pathlib import Path


FRONTEND = Path(__file__).parents[1] / "frontend" / "src"


def test_debug_chat_posts_latest_config_snapshot_and_uses_in_memory_messages():
    source = (FRONTEND / "pages" / "AgentDebugChat.tsx").read_text(encoding="utf-8")
    assert "agentId" in source
    assert "config" in source
    assert "messages" in source
    assert "fetch(`/api/agents/${agentId}/debug`" in source
    assert "approval_required" in source


def test_debug_chat_has_persisted_approval_mode_and_sends_it():
    source = (FRONTEND / "pages" / "AgentDebugChat.tsx").read_text(encoding="utf-8")
    assert "allow_all" in source
    assert "user_approval" in source
    assert "approval_mode: approvalMode" in source
    assert "agent-harness:debug-approval-mode:${agentId}" in source
    assert "window.sessionStorage" in source
    assert "normalizeApprovalMode" in source
    assert 'aria-label="工具执行权限"' in source
    assert "disabled={busy}" in source
    assert "本机 PowerShell 不是安全沙箱；权限由执行权限选择控制" in source
    assert "window.confirm" in source


def test_debug_chat_keeps_trace_with_its_reply_without_sending_it_to_model():
    source = (FRONTEND / "pages" / "AgentDebugChat.tsx").read_text(encoding="utf-8")
    assert '<RunTracePanel events={message.trace ?? []}>' in source
    assert 'messages: history.map(({ role, content }) => ({ role, content }))' in source


def test_workspace_picker_fails_closed_when_local_docker_is_unavailable():
    source = (FRONTEND / "components" / "WorkspacePicker.tsx").read_text(encoding="utf-8")
    styles = (FRONTEND / "components" / "WorkspacePicker.css").read_text(encoding="utf-8")
    assert "api<SandboxStatus>('/sandbox'" in source
    assert "setSandboxConfigured(false)" in source
    assert "controller.signal.aborted" in source
    assert '本机 PowerShell' in source
    assert '本地 Docker 沙箱' in source
    assert 'disabled={sandboxStatusLoading || !sandboxConfigured}' in source
    assert 'const previousChoice = choice' in source
    assert 'setChoice(previousChoice)' in source
    assert 'onChoice(previousChoice)' in source
    assert 'applyInFlight.current' in source
    assert 'CubeSandbox' not in source
    assert '远程' not in source
    assert 'focus-visible' in styles


def test_workspace_picker_handles_nullable_path_and_session_load_failure():
    source = (FRONTEND / "components" / "WorkspacePicker.tsx").read_text(encoding="utf-8")
    app = (FRONTEND / "App.tsx").read_text(encoding="utf-8")
    assert "path: string | null" in source
    assert "workspace?.path ?" in source
    assert "workspaceReady" in source
    assert "onReady(sessionId, false)" in source
    assert "onReady(sessionId, true)" in source
    assert "setWorkspaceLoadError(message)" in source
    assert "controller.signal.aborted || !isCurrent()" in source
    assert "setWorkspaceRetry((n) => n + 1)" in source
    assert "disabled={disabled || busy || !workspaceReady}" in source
    assert "workspaceReady" in app
    assert "workspaceStatus.sessionId === sessionId" in app
    assert "workspaceStatus.epoch === epoch" in app
    assert "!workspaceReady" in app
    assert "workspaceReady ? workspaceChoice : { group_id: null, execution_mode: 'local' }" in app
    assert "重试加载历史" in app
    assert "[canChat, sessionId, historyRetry]" in app
    assert "<WorkspacePicker sessionId={sessionId} disabled={busy || creatingSession}" in app


def test_config_page_uses_two_columns_and_has_autosave_status():
    source = (FRONTEND / "pages" / "AgentConfigPage.tsx").read_text(encoding="utf-8")
    styles = (FRONTEND / "styles.css").read_text(encoding="utf-8")
    assert "saveStatus" in source
    assert "selected_local_tools" in source
    assert "selected_mcp_tools" in source
    assert "cost-rates" not in source
    assert "agent-config-columns" in source
    assert "agent-config-columns" in styles
