import sys

from fastapi.testclient import TestClient

from backend.app.main import create_app


SCRIPT = "print('scheduled hello')"


def test_new_task_is_disabled_until_script_hash_is_confirmed(tmp_path):
    with TestClient(create_app(tmp_path / "scheduler.sqlite3", data_dir=tmp_path / "data")) as client:
        created = client.post("/api/scheduled-tasks", json={
            "name": "Hourly", "script": SCRIPT, "cron_expression": "0 * * * *", "timezone": "Asia/Shanghai",
        })
        assert created.status_code == 201
        item = created.json()
        assert item["enabled"] is False
        assert item["confirmed_hash"] is None
        assert item["next_run_at"] is None
        assert item["script_hash"]


def test_enable_requires_current_hash_and_script_edit_resets_approval(tmp_path):
    with TestClient(create_app(tmp_path / "scheduler.sqlite3", data_dir=tmp_path / "data")) as client:
        created = client.post("/api/scheduled-tasks", json={
            "name": "Hourly", "script": SCRIPT, "cron_expression": "0 * * * *", "timezone": "UTC",
        }).json()
        task_id = created["id"]
        assert client.post(f"/api/scheduled-tasks/{task_id}/enable", json={"confirmed": True, "script_hash": "0" * 64}).status_code == 409
        enabled = client.post(f"/api/scheduled-tasks/{task_id}/enable", json={"confirmed": True, "script_hash": created["script_hash"]})
        assert enabled.status_code == 200
        assert enabled.json()["enabled"] is True
        changed = client.patch(f"/api/scheduled-tasks/{task_id}", json={"script": "print('new version')"})
        assert changed.status_code == 200
        assert changed.json()["enabled"] is False
        assert changed.json()["confirmed_hash"] is None
        assert changed.json()["next_run_at"] is None


def test_invalid_schedule_script_timezone_and_timeout_are_rejected(tmp_path):
    with TestClient(create_app(tmp_path / "scheduler.sqlite3", data_dir=tmp_path / "data")) as client:
        invalid_cron = client.post("/api/scheduled-tasks", json={
            "name": "Bad", "script": SCRIPT, "cron_expression": "61 * * * *", "timezone": "UTC",
        })
        invalid_script = client.post("/api/scheduled-tasks", json={
            "name": "Bad", "script": "def invalid(:", "cron_expression": "* * * * *", "timezone": "UTC",
        })
        invalid_zone = client.post("/api/scheduled-tasks", json={
            "name": "Bad", "script": SCRIPT, "cron_expression": "* * * * *", "timezone": "Mars/Base",
        })
        invalid_timeout = client.post("/api/scheduled-tasks", json={
            "name": "Bad", "script": SCRIPT, "cron_expression": "* * * * *", "timezone": "UTC", "timeout_seconds": 99999,
        })
    assert invalid_cron.status_code == 422
    assert invalid_script.status_code == 422
    assert invalid_zone.status_code == 422
    assert invalid_timeout.status_code == 422


def test_manual_run_requires_confirmation_and_records_output_and_exit_status(tmp_path):
    with TestClient(create_app(tmp_path / "scheduler.sqlite3", data_dir=tmp_path / "data")) as client:
        created = client.post("/api/scheduled-tasks", json={
            "name": "Manual", "script": SCRIPT, "cron_expression": "* * * * *", "timezone": "UTC",
        }).json()
        task_id = created["id"]
        missing = client.post(f"/api/scheduled-tasks/{task_id}/run", json={"confirmed": False, "script_hash": created["script_hash"]})
        assert missing.status_code == 409
        response = client.post(f"/api/scheduled-tasks/{task_id}/run", json={"confirmed": True, "script_hash": created["script_hash"]})
        assert response.status_code == 202
        run_id = response.json()["run_id"]
        for _ in range(100):
            runs = client.get(f"/api/scheduled-tasks/{task_id}/runs").json()["items"]
            if runs and runs[0]["status"] != "running":
                break
            import time; time.sleep(0.02)
        assert runs[0]["id"] == run_id
        assert runs[0]["status"] == "succeeded"
        assert "scheduled hello" in runs[0]["stdout"]
        assert runs[0]["exit_code"] == 0
        detail = client.get(f"/api/scheduled-tasks/{task_id}").json()
        assert detail["enabled"] is False
        assert detail["next_run_at"] is None


def test_delete_running_task_is_blocked_and_delete_removes_history(tmp_path):
    with TestClient(create_app(tmp_path / "scheduler.sqlite3", data_dir=tmp_path / "data")) as client:
        created = client.post("/api/scheduled-tasks", json={
            "name": "Busy", "script": "import time; time.sleep(2)", "cron_expression": "* * * * *", "timezone": "UTC",
        }).json()
        task_id = created["id"]
        run = client.post(f"/api/scheduled-tasks/{task_id}/run", json={"confirmed": True, "script_hash": created["script_hash"]})
        assert run.status_code == 202
        assert client.delete(f"/api/scheduled-tasks/{task_id}").status_code == 409
        assert client.post(f"/api/scheduled-tasks/{task_id}/disable").status_code == 200
        import time; time.sleep(2.2)
        assert client.delete(f"/api/scheduled-tasks/{task_id}").status_code == 204
        assert client.get(f"/api/scheduled-tasks/{task_id}").status_code == 404
