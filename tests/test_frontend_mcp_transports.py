from pathlib import Path


FRONTEND = Path(__file__).parents[1] / "frontend" / "src"


def test_mcp_frontend_exposes_local_sse_and_streamable_http():
    source = (FRONTEND / "pages" / "PluginLibraryPage.tsx").read_text(encoding="utf-8")
    app = (FRONTEND / "App.tsx").read_text(encoding="utf-8")
    combined = source + app
    assert "streamable_http" in combined
    assert "<option value=\"sse\">SSE</option>" in combined
    assert "Streamable HTTP" in combined
    assert "mcpTransport === 'stdio'" in combined
