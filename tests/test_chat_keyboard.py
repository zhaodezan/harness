from pathlib import Path


FRONTEND = Path(__file__).parents[1] / "frontend" / "src"


def test_chat_keyboard_helper_sends_enter_but_preserves_shift_enter_and_composition():
    source = (FRONTEND / "chatKeyboard.ts").read_text(encoding="utf-8")
    assert "event.key !== 'Enter'" in source
    assert "event.shiftKey" in source
    assert "event.nativeEvent.isComposing" in source
    assert "event.preventDefault()" in source
    assert "submit()" in source
