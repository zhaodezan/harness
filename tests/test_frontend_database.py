from pathlib import Path


FRONTEND = Path(__file__).parents[1] / "frontend" / "src"


def test_database_page_supports_sqlite_mysql_credentials_and_management():
    source = (FRONTEND / "pages" / "DatabasePage.tsx").read_text(encoding="utf-8")
    assert "sqlite" in source and "mysql" in source
    assert "password_configured" in source
    assert "type=\"password\"" in source
    assert "test-connection" in source
    assert "schema" in source
    assert "database_path" not in source
    assert "删除连接" in source


def test_database_sidebar_and_route_are_independent():
    source = (FRONTEND / "App.tsx").read_text(encoding="utf-8")
    assert "DatabasePage" in source
    assert 'path="/databases"' in source
    assert "数据库" in source
    assert "知识库" in source and "长期记忆" in source


def test_agent_config_and_debug_pass_selected_database_connections():
    config = (FRONTEND / "pages" / "AgentConfigPage.tsx").read_text(encoding="utf-8")
    debug = (FRONTEND / "pages" / "AgentDebugChat.tsx").read_text(encoding="utf-8")
    assert "selected_database_connections" in config
    assert "selected_database_connections" in debug
    assert "/databases" in config
