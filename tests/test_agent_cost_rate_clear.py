from fastapi.testclient import TestClient

from backend.app.main import create_app
from model_test_utils import create_test_agent


def test_agent_update_can_clear_legacy_cost_rates(tmp_path):
    with TestClient(create_app(tmp_path / "rates.sqlite3", data_dir=tmp_path / "data")) as client:
        agent = create_test_agent(client, {"name": "Rated", "input_rate_per_million": 1.0, "output_rate_per_million": 2.0}).json()
        updated = client.patch(f"/api/agents/{agent['id']}", json={
            "system_prompt": "Updated", "input_rate_per_million": None, "output_rate_per_million": None,
        })
    assert updated.status_code == 200
    assert updated.json()["input_rate_per_million"] is None
    assert updated.json()["output_rate_per_million"] is None
