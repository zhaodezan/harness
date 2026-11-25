from pathlib import Path


FRONTEND = Path(__file__).parents[1] / "frontend" / "src"


def test_model_tab_has_model_crud_connection_test_and_updated_navigation():
    source = (FRONTEND / "pages" / "PluginLibraryPage.tsx").read_text(encoding="utf-8")
    app = (FRONTEND / "App.tsx").read_text(encoding="utf-8")
    assert "Model" in source
    assert "credential_configured" in source
    assert "last_tested_at" in source
    assert "test-connection" in source
    assert "/models/test-connection" in source
    assert "modelCandidateStatus" in source
    assert "passed" in source
    assert "modelApiKey" in source
    assert 'navigate(\'/plugins?tab=Model\')' in app
    assert "模型及插件库" in app
    assert 'onClick={() => navigate(\'/agents\')}' not in app
    assert "activeTab === 'Agent'" in source
    assert "第三方插件" in source and "Dify" in source and "DSH" in source


def test_model_edit_saves_connection_and_optional_key_atomically():
    source = (FRONTEND / "pages" / "PluginLibraryPage.tsx").read_text(encoding="utf-8")
    assert "api_key: modelApiKey.trim() || undefined" in source
    assert "'/credentials'" not in source


def test_agent_configuration_references_model_without_direct_credentials():
    source = (FRONTEND / "pages" / "AgentConfigPage.tsx").read_text(encoding="utf-8")
    debug = (FRONTEND / "pages" / "AgentDebugChat.tsx").read_text(encoding="utf-8")
    assert "model_config_id" in source
    assert "选择模型" in source
    assert "前往模型及插件库" in source
    assert "API 密钥<input" not in source
    assert "baseUrl" not in source
    assert "api_key" not in debug
    assert "model_config_id" in debug
