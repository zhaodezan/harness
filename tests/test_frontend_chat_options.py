from pathlib import Path

SRC = Path(__file__).parents[1] / "frontend" / "src"


def test_chat_composer_adds_removable_capabilities_and_model_control_before_send():
    app = (SRC / "App.tsx").read_text(encoding="utf-8")
    controls = (SRC / "ChatComposerOptions.tsx").read_text(encoding="utf-8")
    assert "<ChatCapabilitiesPicker" in app
    assert app.index("<ChatModelPicker") < app.index("aria-label={busy ? '停止运行' : '发送'}")
    assert 'type="checkbox"' in controls
    assert 'aria-label={`移除 ${item.name}`}' in controls
    assert "恢复 Agent 默认模型" in controls
    assert "model?.name" in controls


def test_send_snapshots_scoped_composer_preferences_before_opening_session():
    app = (SRC / "App.tsx").read_text(encoding="utf-8")
    send = app.split("const send = async () => {", 1)[1].split("const importDataFile", 1)[0]
    assert "composerByChat[composerKey]" in app
    assert send.index("const sendOptions") < send.index("await api<Session>")
    assert "...sendOptions" in send
    assert "sessionStorage" in app
