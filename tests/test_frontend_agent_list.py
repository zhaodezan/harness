from pathlib import Path


FRONTEND_SOURCE = Path(__file__).parents[1] / "frontend" / "src" / "App.tsx"
LIST_PAGE_SOURCE = Path(__file__).parents[1] / "frontend" / "src" / "pages" / "AgentListPage.tsx"


def test_standalone_agent_list_has_separate_publish_and_configuration_actions():
    source = LIST_PAGE_SOURCE.read_text(encoding="utf-8")

    assert "agents.map((agent)" in source
    assert "onConfigure(agent.id)" in source
    assert '<Link ' in source
    assert 'to={`/agents/${encodeURIComponent(agent.id)}/chat`}' in source
    assert 'target="_blank"' in source
    assert 'rel="noopener noreferrer"' in source
    assert "配置" in source
    assert '>发布</Link>' in source


def test_agent_list_exposes_delete_action_and_cascade_warning():
    source = LIST_PAGE_SOURCE.read_text(encoding="utf-8")
    assert "onDelete" in source
    assert "删除" in source


def test_app_delete_confirmation_mentions_cascade_impact_and_routes_on_empty_list():
    source = FRONTEND_SOURCE.read_text(encoding="utf-8")
    assert "会话" in source and "消息" in source
    assert "专属记忆" in source and "Prompt" in source
    assert "'/agents/new'" in source
