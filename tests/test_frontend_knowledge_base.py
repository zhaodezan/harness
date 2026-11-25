from pathlib import Path


FRONTEND = Path(__file__).parents[1] / "frontend" / "src"


def test_knowledge_base_page_supports_management_upload_and_debug_search():
    source = (FRONTEND / "pages" / "KnowledgeBasePage.tsx").read_text(encoding="utf-8")
    assert "/knowledge-bases" in source
    assert "FormData" in source
    for extension in (".md", ".txt", ".json", ".csv", ".pdf", ".docx"):
        assert extension in source
    assert "调试搜索" in source
    assert "加入对话" not in source
    assert "发送给模型" not in source
    assert "status" in source and "error" in source


def test_app_exposes_knowledge_base_route_and_separate_sidebar_entry():
    source = (FRONTEND / "App.tsx").read_text(encoding="utf-8")
    assert "KnowledgeBasePage" in source
    assert 'path="/knowledge-bases"' in source
    assert "知识库" in source
    assert "长期记忆" in source


def test_agent_config_loads_and_saves_selected_knowledge_bases():
    source = (FRONTEND / "pages" / "AgentConfigPage.tsx").read_text(encoding="utf-8")
    debug = (FRONTEND / "pages" / "AgentDebugChat.tsx").read_text(encoding="utf-8")
    assert "/knowledge-bases" in source
    assert "selected_knowledge_bases" in source
    assert "知识库" in source
    assert "selected_knowledge_bases" in debug
    assert "搜索知识库" not in debug
