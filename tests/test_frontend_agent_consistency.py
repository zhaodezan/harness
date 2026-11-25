from pathlib import Path


FRONTEND = Path(__file__).parents[1] / "frontend" / "src"


def test_plugin_agent_tab_reuses_canonical_agent_list():
    source = (FRONTEND / "pages" / "PluginLibraryPage.tsx").read_text(encoding="utf-8")
    assert "activeTab === 'Agent' && <AgentListPage" in source
    assert "onUse" not in source
    assert "useState<Agent[]>" not in source
