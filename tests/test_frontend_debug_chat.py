from pathlib import Path


FRONTEND = Path(__file__).parents[1] / "frontend" / "src"


def test_debug_chat_posts_latest_config_snapshot_and_uses_in_memory_messages():
    source = (FRONTEND / "pages" / "AgentDebugChat.tsx").read_text(encoding="utf-8")
    assert "agentId" in source
    assert "config" in source
    assert "messages" in source
    assert "fetch(`/api/agents/${agentId}/debug`" in source
    assert "approval_required" in source


def test_debug_chat_keeps_trace_with_its_reply_without_sending_it_to_model():
    source = (FRONTEND / "pages" / "AgentDebugChat.tsx").read_text(encoding="utf-8")
    assert '<RunTracePanel events={message.trace ?? []}>' in source
    assert 'messages: history.map(({ role, content }) => ({ role, content }))' in source


def test_config_page_uses_two_columns_and_has_autosave_status():
    source = (FRONTEND / "pages" / "AgentConfigPage.tsx").read_text(encoding="utf-8")
    styles = (FRONTEND / "styles.css").read_text(encoding="utf-8")
    assert "saveStatus" in source
    assert "selected_local_tools" in source
    assert "selected_mcp_tools" in source
    assert "cost-rates" not in source
    assert "agent-config-columns" in source
    assert "agent-config-columns" in styles
