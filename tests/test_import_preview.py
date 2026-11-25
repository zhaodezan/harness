from fastapi.testclient import TestClient

from backend.app.main import create_app


def test_import_preview_reports_counts_and_conflicts_without_writing(tmp_path):
    app = create_app(tmp_path / "preview.sqlite3")
    with TestClient(app) as client:
        created = client.post("/api/mcp/servers", json={"name": "files", "transport": "stdio", "command": "python"})
        assert created.status_code == 201
        payload = {
            "format_version": 1,
            "secrets_included": False,
            "agents": [{"id": "old-agent", "name": "New agent"}],
            "sessions": [{"id": "old-session", "agent_id": "old-agent", "title": "Saved"}],
            "messages": [{"session_id": "old-session", "role": "user", "content": "Question"}],
            "mcp_servers": [{"name": "files", "transport": "stdio", "command": "python", "args": []}],
        }
        preview = client.post("/api/data/preview", json=payload)
        assert preview.status_code == 200
        result = preview.json()
        assert result["agents"] == 1
        assert result["sessions"] == 1
        assert result["messages"] == 1
        assert result["mcp_name_conflicts"] == ["files"]
        assert len(client.get("/api/agents").json()) == 0
        assert len(client.get("/api/mcp/servers").json()) == 1
