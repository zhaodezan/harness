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


def test_sandbox_settings_keep_key_only_in_secret_store_and_never_return_it(tmp_path):
    database = tmp_path / "sandbox-settings.sqlite3"
    secrets = Secrets()
    app = create_app(database, data_dir=tmp_path / "data", secret_store=secrets)

    with TestClient(app) as client:
        initial = client.get("/api/sandbox").json()
        assert initial["configured"] is False
        response = client.put("/api/sandbox", json={
            "enabled": True,
            "api_url": "https://cube.example.test/cubeapi/v1",
            "template_id": "python-template",
            "timeout_seconds": 45,
            "allow_internet_access": True,
            "api_key": "secret-cube-key",
        })
        assert response.status_code == 200, response.text
        saved = response.json()
        assert saved["configured"] is True
        assert saved["key_configured"] is True
        assert "secret-cube-key" not in response.text
        assert client.get("/api/sandbox").json()["key_configured"] is True

    assert secrets.get("cubesandbox:api_key") == "secret-cube-key"
    with database_connection(database) as connection:
        stored = connection.execute("SELECT value_json FROM app_settings WHERE key='cubesandbox'").fetchone()[0]
    assert "secret-cube-key" not in stored
    assert json.loads(stored)["api_url"] == "https://cube.example.test/cubeapi/v1"


def test_sandbox_settings_reject_plain_http_and_do_not_overwrite_current_config(tmp_path):
    app = create_app(tmp_path / "settings.sqlite3", data_dir=tmp_path / "data", secret_store=Secrets())
    with TestClient(app) as client:
        response = client.put("/api/sandbox", json={
            "enabled": True,
            "api_url": "http://cube.example.test/cubeapi/v1",
            "template_id": "python-template",
            "timeout_seconds": 30,
            "allow_internet_access": True,
            "api_key": "secret",
        })
        assert response.status_code == 422
        assert client.get("/api/sandbox").json()["configured"] is False
