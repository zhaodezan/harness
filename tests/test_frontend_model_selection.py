from pathlib import Path


FRONTEND = Path(__file__).parents[1] / "frontend" / "src"


def test_agent_config_selects_model_and_never_collects_provider_secret():
    source = (FRONTEND / "pages" / "AgentConfigPage.tsx").read_text(encoding="utf-8")
    debug = (FRONTEND / "pages" / "AgentDebugChat.tsx").read_text(encoding="utf-8")
    assert "/models" in source
    assert "model_config_id" in source
    assert "<select" in source
    assert "baseUrl" not in source
    assert "apiKey" not in source
    assert "model_config_id" in debug
    assert "api_key" not in debug
    assert ">{item.name}</option>" in source
    assert ">{item.name} · {item.model}</option>" not in source


def test_agent_config_guides_to_model_library_if_none_exists():
    source = (FRONTEND / "pages" / "AgentConfigPage.tsx").read_text(encoding="utf-8")
    assert "/plugins?tab=Model" in source
    assert "先添加并测试模型连接" in source
