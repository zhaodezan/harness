from fastapi.testclient import TestClient

from backend.app.main import create_app


def test_backup_rejects_non_list_plugin_permissions(tmp_path):
    with TestClient(create_app(tmp_path / "import.sqlite3", data_dir=tmp_path / "data")) as client:
        response = client.post("/api/data/import", json={
            "format_version": 1,
            "agents": [{"id": "a", "name": "A", "selected_mcp_tools": "mcp:server:*"}],
            "sessions": [], "messages": [],
        })
    assert response.status_code == 422
