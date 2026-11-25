from pathlib import Path


FRONTEND = Path(__file__).parents[1] / "frontend" / "src"


def test_agent_config_autosaves_and_selects_all_plugin_types():
    source = (FRONTEND / "pages" / "AgentConfigPage.tsx").read_text(encoding="utf-8")
    assert "setTimeout" in source
    assert "PATCH" in source
    assert "已自动保存" in source
    assert "重试保存" in source
    assert "selected_skills" in source
    assert "selected_mcp_tools" in source
    assert "selected_local_tools" in source
    assert "agent-config-columns" in source
    assert "cost-rates" not in source


def test_agent_config_can_authorize_other_agents_and_enable_sandboxed_python():
    source = (FRONTEND / "pages" / "AgentConfigPage.tsx").read_text(encoding="utf-8")
    assert "selected_agents" in source
    assert "可委派的智能体" in source
    assert "sandbox_code_enabled" in source
    assert "沙箱" in source


def test_plugin_library_exposes_cube_sandbox_controls_and_fail_closed_copy():
    source = (FRONTEND / "pages" / "PluginLibraryPage.tsx").read_text(encoding="utf-8")
    assert "CubeSandbox" in source
    assert "保存沙箱配置" in source
    assert "测试连接" in source
    assert "API Key 只存储在系统凭据库" in source
    assert "不会回退到本机" in source
    assert "内网/元数据地址" in source


def test_debug_chat_uses_current_unsaved_config_object():
    source = (FRONTEND / "pages" / "AgentConfigPage.tsx").read_text(encoding="utf-8")
    assert "<AgentDebugChat" in source
    assert "model_config_id: modelConfigId" in source
    assert "system_prompt: systemPrompt" in source
