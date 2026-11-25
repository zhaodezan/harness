def test_imported_database_connection_requires_sqlite_path_reconfirmation(tmp_path):
    import sqlite3
    from fastapi.testclient import TestClient
    from backend.app.main import create_app

    source = tmp_path / "external.sqlite3"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE private_data(value TEXT)")
    with TestClient(create_app(tmp_path / "backup.sqlite3", data_dir=tmp_path / "data")) as client:
        payload = {
            "format_version": 2, "models": [], "agents": [], "sessions": [], "messages": [],
            "database_connections": [{
                "id": "old-db", "name": "Restored", "db_type": "sqlite", "sqlite_path": str(source),
                "host": "", "port": 3306, "database_name": "", "username": "", "ssl": True, "readonly": True,
            }],
        }
        imported = client.post("/api/data/import", json=payload)
        assert imported.status_code == 200
        connection_id = imported.json()["database_connections"][0]["id"]
        restored = client.get(f"/api/databases/{connection_id}").json()
        assert restored["sqlite_path"] == ""
        assert restored["last_tested_at"] is None
