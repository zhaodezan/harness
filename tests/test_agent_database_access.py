def test_setup_agent_accepts_and_persists_database_authorization(tmp_path):
    import sqlite3
    from fastapi.testclient import TestClient
    from backend.app.main import create_app
    from tests.model_test_utils import create_test_model

    source = tmp_path / "source.sqlite3"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE items(value TEXT)")
    with TestClient(create_app(tmp_path / "setup.sqlite3", data_dir=tmp_path / "data")) as client:
        db = client.post("/api/databases", json={
            "name": "Items", "db_type": "sqlite", "sqlite_path": str(source),
        }).json()
        model_id = create_test_model(client)
        created = client.post("/api/setup/model", json={
            "name": "Setup Reader", "model_config_id": model_id,
            "selected_database_connections": [db["id"]],
        })
        assert created.status_code == 201
        agent = client.get(f"/api/agents/{created.json()['id']}").json()
    assert agent["selected_database_connections"] == [db["id"]]
