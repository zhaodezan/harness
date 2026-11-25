from pathlib import Path


FRONTEND = Path(__file__).parents[1] / "frontend" / "src"


def test_agent_config_uses_checkboxes_and_auto_saves_knowledge_selection():
    source = (FRONTEND / "pages" / "AgentConfigPage.tsx").read_text(encoding="utf-8")
    assert "selectedKnowledgeBases" in source
    assert "selected_knowledge_bases: selectedKnowledgeBases" in source
    assert "type=\"checkbox\"" in source
    assert "自动保存" in source


def test_debug_sends_authorized_kb_ids_but_has_no_manual_search_action():
    source = (FRONTEND / "pages" / "AgentDebugChat.tsx").read_text(encoding="utf-8")
    assert "selected_knowledge_bases" in source
    assert "知识库搜索" not in source
    assert "发送搜索结果" not in source
