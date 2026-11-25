from fastapi.testclient import TestClient

from backend.app.db import database_connection
from backend.app.main import create_app


def test_scheduled_task_backup_restores_disabled_and_unconfirmed_without_history(tmp_path):
    app = create_app(tmp_path / "source.sqlite3", data_dir=tmp_path / "source-data")
    with TestClient(app) as client:
        created = client.post("/api/scheduled-tasks", json={
            "name": "Backup me", "script": "print('backup')", "cron_expression": "15 * * * *", "timezone": "UTC",
        }).json()
        task_id = created["id"]
        assert client.post(f"/api/scheduled-tasks/{task_id}/enable", json={
            "confirmed": True, "script_hash": created["script_hash"],
        }).status_code == 200
        with database_connection(app.state.database_path) as connection:
            connection.execute(
                "INSERT INTO scheduled_task_runs(id,task_id,trigger,status,summary) VALUES('history-1',?,'manual','succeeded','old run')",
                (task_id,),
            )
        exported = client.get("/api/data/export").json()
        assert exported["format_version"] == 2
        assert len(exported["scheduled_tasks"]) == 1
        assert exported["scheduled_tasks"][0]["script"] == "print('backup')"
        assert "scheduled_task_runs" not in exported
        restored = client.post("/api/data/import", json=exported)
        assert restored.status_code == 200, restored.text
        new_task_id = restored.json()["scheduled_tasks"][0]["id"]
        assert new_task_id != task_id
        task = client.get(f"/api/scheduled-tasks/{new_task_id}").json()
        assert task["name"] == "Backup me"
        assert task["script"] == "print('backup')"
        assert task["cron_expression"] == "15 * * * *"
        assert task["enabled"] is False
        assert task["confirmed_hash"] is None
        assert task["next_run_at"] is None
        assert client.get(f"/api/scheduled-tasks/{new_task_id}/runs").json()["items"] == []
