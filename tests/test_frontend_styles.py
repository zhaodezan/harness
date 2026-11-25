from pathlib import Path


def test_critical_frontend_styles_have_scoped_selectors():
    css = (Path(__file__).parents[1] / "frontend" / "src" / "styles.css").read_text(encoding="utf-8")
    for selector in (".app-shell", ".sidebar", ".main-panel", ".welcome", ".composer", ".trace-panel", ".plan-card", ".memory-card", ".mcp-card", ".logs-card", ".agent-list-page", ".agent-config-page", ".agent-card", ".agent-config-form"):
        assert selector in css
    assert "}{width:min(620px,100%)}" not in css
    assert "prefers-reduced-motion" in css
