from fastapi.testclient import TestClient

from backend.app.main import create_app
from model_test_utils import create_test_agent


def test_restore_prompt_returns_full_agent_plugin_configuration(tmp_path):
    with TestClient(create_app(tmp_path / "prompts.sqlite3", data_dir=tmp_path / "data")) as client:
        agent = create_test_agent(client, {
            "name": "Prompt", "system_prompt": "old", "selected_mcp_tools": ["mcp:s:t"],
            "selected_local_tools": ["local-1"], "prompt_variables": {"name": "Ada"},
        }).json()
        versions = client.get(f"/api/agents/{agent['id']}/prompts").json()
        client.patch(f"/api/agents/{agent['id']}", json={"system_prompt": "new"})
        restored = client.post(f"/api/agents/{agent['id']}/prompts/{versions[0]['id']}/restore")

    assert restored.status_code == 200
    assert restored.json()["selected_mcp_tools"] == ["mcp:s:t"]
    assert restored.json()["selected_local_tools"] == ["local-1"]
    assert restored.json()["prompt_variables"] == {"name": "Ada"}
