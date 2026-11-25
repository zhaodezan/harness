import sqlite3

from fastapi.testclient import TestClient

from backend.app.main import create_app
from model_test_utils import create_test_agent


def test_prompt_changes_create_version_history_and_can_be_restored(tmp_path):
    with TestClient(create_app(tmp_path / "prompts.sqlite3")) as client:
        agent = create_test_agent(client, {"name": "Writer", "system_prompt": "Version one"}).json()
        updated = client.patch(f"/api/agents/{agent['id']}", json={"system_prompt": "Version two"})
        assert updated.status_code == 200
        versions = client.get(f"/api/agents/{agent['id']}/prompts").json()
        assert [item["content"] for item in versions] == ["Version two", "Version one"]
        restored = client.post(f"/api/agents/{agent['id']}/prompts/{versions[1]['id']}/restore")
        assert restored.status_code == 200
        assert restored.json()["system_prompt"] == "Version one"
        assert len(client.get(f"/api/agents/{agent['id']}/prompts").json()) == 3
