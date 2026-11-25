from pathlib import Path


FRONTEND = Path(__file__).parents[1] / "frontend" / "src"


def test_plugin_library_exposes_four_local_plugin_tabs():
    source = (FRONTEND / "pages" / "PluginLibraryPage.tsx").read_text(encoding="utf-8")
    app = (FRONTEND / "App.tsx").read_text(encoding="utf-8")

    assert "Agent" in source
    assert "Skill" in source
    assert "MCP" in source
    assert "Tool" in source
    assert 'path="/plugins"' in app
    assert "插件库" in app
    assert "marketplace" not in source.lower()




def test_third_party_tab_contains_dify_and_dsh_placeholders_without_install_actions():
    source = (FRONTEND / "pages" / "PluginLibraryPage.tsx").read_text(encoding="utf-8")
    app = (FRONTEND / "App.tsx").read_text(encoding="utf-8")
    assert "第三方插件" in source
    assert "Dify" in source
    assert "DSH" in source
    assert "暂未实现" in source
    assert "工具与连接" not in app
    third_party = source.split("activeTab === '第三方插件'", 1)[1]
    assert "install" not in third_party.lower()
    assert "fetch(" not in third_party

    source = (FRONTEND / "api.ts").read_text(encoding="utf-8")



def test_plugin_library_agent_cards_expose_delete_action_and_delete_api():
    source = (FRONTEND / "pages" / "PluginLibraryPage.tsx").read_text(encoding="utf-8")
    app = (FRONTEND / "App.tsx").read_text(encoding="utf-8")
    assert "AgentListPage" in source
    assert "onDelete" in source
    assert "deleteAgent" in app
    assert "onDelete={deleteAgent}" in app
    assert "/agents/${agent.id}" in app
    assert "method: 'DELETE'" in app
    assert "会话" in app and "消息" in app
    assert "专属记忆" in app and "Prompt" in app
