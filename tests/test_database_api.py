import sqlite3

from fastapi.testclient import TestClient

from backend.app.main import create_app


class Secrets:
    def __init__(self):
        self.values = {}
    def get(self, key):
        return self.values.get(key)
    def set(self, key, value):
        self.values[key] = value
    def delete(self, key):
        self.values.pop(key, None)


def test_database_api_saves_mysql_password_in_secret_store_only(tmp_path):
    secrets = Secrets()
    app = create_app(tmp_path / "app.sqlite3", data_dir=tmp_path / "data", secret_store=secrets)
    with TestClient(app) as client:
        created = client.post("/api/databases", json={
            "name": "Warehouse", "db_type": "mysql", "host": "db.example.test",
            "port": 3306, "database_name": "warehouse", "username": "reader", "password": "secret-password",
        })
        assert created.status_code == 201
        assert "secret-password" not in created.text
        connection_id = created.json()["id"]
        listed = client.get("/api/databases")
        assert listed.status_code == 200
        assert listed.json()[0]["password_configured"] is True
        assert "secret-password" not in listed.text
        assert secrets.get(f"database:{connection_id}:password") == "secret-password"


def test_sqlite_connection_requires_existing_non_app_database_file(tmp_path):
    app = create_app(tmp_path / "app.sqlite3", data_dir=tmp_path / "data")
    source = tmp_path / "source.sqlite3"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE entries(value TEXT)")
    with TestClient(app) as client:
        missing = client.post("/api/databases", json={
            "name": "Missing", "db_type": "sqlite", "sqlite_path": str(tmp_path / "missing.sqlite3"),
        })
        assert missing.status_code == 422
        created = client.post("/api/databases", json={
            "name": "Source", "db_type": "sqlite", "sqlite_path": str(source),
        })
        assert created.status_code == 201
        connection_id = created.json()["id"]
        tested = client.post(f"/api/databases/{connection_id}/test-connection")
        assert tested.status_code == 200
        schema = client.get(f"/api/databases/{connection_id}/schema")
        assert schema.json()["tables"][0]["name"] == "entries"
        deleted = client.delete(f"/api/databases/{connection_id}")
        assert deleted.status_code == 204
    assert source.exists()


def test_agent_reference_blocks_database_delete_and_password_is_removed_on_delete(tmp_path):
    secrets = Secrets()
    with TestClient(create_app(tmp_path / "app.sqlite3", data_dir=tmp_path / "data", secret_store=secrets)) as client:
        db = client.post("/api/databases", json={
            "name": "Warehouse", "db_type": "mysql", "host": "db.example.test",
            "database_name": "warehouse", "username": "reader", "password": "pw",
        }).json()
        with sqlite3.connect(client.app.state.database_path) as connection:
            connection.execute(
                "INSERT INTO agents(id,name,selected_database_connections_json) VALUES('agent-1','Reader',?)",
                (f'["{db["id"]}"]',),
            )
        blocked = client.delete(f"/api/databases/{db['id']}")
        assert blocked.status_code == 409
        assert "Reader" in blocked.text
        with sqlite3.connect(client.app.state.database_path) as connection:
            connection.execute("UPDATE agents SET selected_database_connections_json='[]' WHERE id='agent-1'")
        removed = client.delete(f"/api/databases/{db['id']}")
        assert removed.status_code == 204
        assert secrets.get(f"database:{db['id']}:password") is None


def test_database_api_rejects_invalid_connection_configuration(tmp_path):
    with TestClient(create_app(tmp_path / "app.sqlite3", data_dir=tmp_path / "data")) as client:
        assert client.post("/api/databases", json={"name": "Bad", "db_type": "postgres"}).status_code == 422
        assert client.post("/api/databases", json={
            "name": "Bad MySQL", "db_type": "mysql", "host": "", "database_name": "db", "username": "u",
        }).status_code == 422
