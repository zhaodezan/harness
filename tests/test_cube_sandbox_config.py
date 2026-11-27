import json

from fastapi.testclient import TestClient

from backend.app.db import database_connection
from backend.app.main import create_app


class Secrets:
    def __init__(self):
        self.values = {}

    def get(self, name):
        return self.values.get(name)

    def set(self, name, value):
        self.values[name] = value

    def delete(self, name):
        self.values.pop(name, None)


def test_sandbox_settings_expose_only_safe_local_docker_fields(tmp_path):
    database = tmp_path / "sandbox-settings.sqlite3"
    app = create_app(database, data_dir=tmp_path / "data", secret_store=Secrets())
    fields = {"enabled", "image", "timeout_seconds", "memory_mb", "cpu_count", "network_enabled", "configured"}

    with TestClient(app) as client:
        initial = client.get("/api/sandbox")
        assert initial.status_code == 200
        assert set(initial.json()) == fields
        assert initial.json()["configured"] is False
        response = client.put("/api/sandbox", json={
            "enabled": False,
            "image": "python:3.11-slim",
            "timeout_seconds": 45,
            "memory_mb": 1024,
            "cpu_count": 2,
            "network_enabled": False,
        })
        assert response.status_code == 200, response.text
        saved = response.json()
        assert set(saved) == fields
        assert saved["enabled"] is False
        assert saved["configured"] is False
        assert "api_key" not in response.text
        assert "CubeSandbox" not in response.text

    with database_connection(database) as connection:
        stored = connection.execute(
            "SELECT value_json FROM app_settings WHERE key='docker_sandbox'"
        ).fetchone()[0]
    assert json.loads(stored) == {
        "enabled": False,
        "image": "python:3.11-slim",
        "timeout_seconds": 45,
        "memory_mb": 1024,
        "cpu_count": 2,
        "network_enabled": False,
    }


def test_sandbox_put_rejects_old_remote_fields_and_does_not_convert_them(tmp_path):
    app = create_app(tmp_path / "settings.sqlite3", data_dir=tmp_path / "data", secret_store=Secrets())
    with TestClient(app) as client:
        response = client.put("/api/sandbox", json={
            "enabled": True,
            "api_url": "https://cube.example.test/cubeapi/v1",
            "template_id": "python-template",
            "api_key": "secret",
        })
        assert response.status_code == 422
        assert client.get("/api/sandbox").json()["configured"] is False


def test_enabling_sandbox_requires_local_cli_and_image_without_pull(tmp_path):
    app = create_app(tmp_path / "settings.sqlite3", data_dir=tmp_path / "data", secret_store=Secrets())
    app.state.sandbox_executor.is_configured = lambda: False
    with TestClient(app) as client:
        response = client.put("/api/sandbox", json={
            "enabled": True,
            "image": "python:3.11-slim",
            "timeout_seconds": 30,
            "memory_mb": 512,
            "cpu_count": 1,
            "network_enabled": False,
        })
    assert response.status_code in {409, 422}
    assert "Docker" in response.json()["detail"]
    with database_connection(tmp_path / "settings.sqlite3") as connection:
        assert connection.execute(
            "SELECT 1 FROM app_settings WHERE key='docker_sandbox'"
        ).fetchone() is None


def test_local_docker_test_endpoint_uses_saved_settings_and_reports_availability(tmp_path):
    app = create_app(tmp_path / 'settings.sqlite3', data_dir=tmp_path / 'data', secret_store=Secrets())
    class FakeDocker:
        available = True
        fail = False
        seen = []

        def is_configured(self, values):
            self.seen.append(dict(values))
            if self.fail:
                raise RuntimeError('Docker CLI failed')
            return self.available

    docker = FakeDocker()
    app.state.sandbox_executor = docker
    payload = {
        'enabled': True, 'image': 'python:3.11-slim', 'timeout_seconds': 45,
        'memory_mb': 1024, 'cpu_count': 2, 'network_enabled': False,
    }
    with TestClient(app) as client:
        assert client.post('/api/sandbox/test').status_code == 409
        assert client.put('/api/sandbox', json=payload).status_code == 200
        success = client.post('/api/sandbox/test')
        assert success.status_code == 200
        assert success.json()['ok'] is True
        assert '未运行容器' in success.json()['message']
        assert docker.seen[-1] == payload
        docker.available = False
        unavailable = client.post('/api/sandbox/test')
        assert unavailable.status_code == 409
        assert '镜像' in unavailable.json()['detail']
        docker.fail = True
        failure = client.post('/api/sandbox/test')
        assert failure.status_code == 409
        assert 'Docker' in failure.json()['detail']


def test_enabling_valid_local_docker_saves_and_reports_configured(tmp_path):
    app = create_app(tmp_path / "settings.sqlite3", data_dir=tmp_path / "data", secret_store=Secrets())
    app.state.sandbox_executor.is_configured = lambda: True
    with TestClient(app) as client:
        response = client.put("/api/sandbox", json={
            "enabled": True,
            "image": "python:3.11-slim",
            "timeout_seconds": 30,
            "memory_mb": 512,
            "cpu_count": 1,
            "network_enabled": False,
        })
        assert response.status_code == 200, response.text
        assert response.json()["configured"] is True
        assert client.get("/api/sandbox").json()["configured"] is True
