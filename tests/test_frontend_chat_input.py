from pathlib import Path


FRONTEND = Path(__file__).parents[1] / "frontend" / "src"


def test_both_chat_composers_share_enter_handler():
    app = (FRONTEND / "App.tsx").read_text(encoding="utf-8")
    debug = (FRONTEND / "pages" / "AgentDebugChat.tsx").read_text(encoding="utf-8")
    assert "submitChatOnEnter" in app
    assert "submitChatOnEnter" in debug
    assert "nativeEvent.isComposing" in (FRONTEND / "chatKeyboard.ts").read_text(encoding="utf-8")


def test_debug_chat_renders_plan_and_uses_shared_key_handler():
    debug = (FRONTEND / "pages" / "AgentDebugChat.tsx").read_text(encoding="utf-8")
    assert "plan: payload.plan" in debug
    assert "debug-plan-card" in debug
    assert "submitChatOnEnter" in debug
