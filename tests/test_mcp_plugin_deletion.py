from fastapi.testclient import TestClient

from backend.app.main import create_app
from model_test_utils import create_test_agent


def test_cannot_delete_mcp_server_selected_by_agent(tmp_path):
    with TestClient(create_app(tmp_path / "mcp.sqlite3", data_dir=tmp_path / "data")) as client:
        server = client.post("/api/mcp/servers", json={
            "name": "local", "transport": "stdio", "command": "python",
        }).json()
        create_test_agent(client, {
            "name": "Uses MCP", "selected_mcp_tools": [f"mcp:{server['id']}:*"],
        })
        response = client.delete(f"/api/mcp/servers/{server['id']}")
        remaining = client.get("/api/mcp/servers").json()

    assert response.status_code == 409
    assert len(remaining) == 1
