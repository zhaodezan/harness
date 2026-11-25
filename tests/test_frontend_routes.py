from pathlib import Path


FRONTEND = Path(__file__).parents[1] / "frontend" / "src"


def test_main_wraps_app_with_browser_router():
    source = (FRONTEND / "main.tsx").read_text(encoding="utf-8")

    assert "BrowserRouter" in source
    assert "<BrowserRouter>" in source
    assert "<App />" in source


def test_app_declares_agent_chat_and_configuration_routes():
    source = (FRONTEND / "App.tsx").read_text(encoding="utf-8")

    assert 'path="/chat"' in source
    assert 'path="/agents"' in source
    assert 'path="/agents/new"' in source
    assert 'path="/agents/:agentId/config"' in source
    assert 'path="/agents/:agentId/chat"' in source
    assert 'path="/" element={<Navigate to="/chat" replace' in source


def test_workspaces_use_their_own_session_search_and_options_endpoints():
    source = (FRONTEND / "App.tsx").read_text(encoding="utf-8")

    assert "'workspace=true'" in source
    assert "`agent_id=${encodeURIComponent(agentId)}`" in source
    assert '`/sessions?${sessionScope}`' in source
    assert '`/sessions/search?${sessionScope}&q=' in source
    assert "'/sessions/chat-options'" in source


def test_agent_config_page_preserves_create_edit_and_not_found_flows():
    source = (FRONTEND / "pages" / "AgentConfigPage.tsx").read_text(encoding="utf-8")

    assert "agent-config-page" in source
    assert "useParams" in source
    assert "model_config_id" in source
    assert "/setup/model" in source
    assert "/models" in source
    assert "onSaved" in source
    assert "agent-not-found" in source
