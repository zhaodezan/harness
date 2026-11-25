from fastapi.testclient import TestClient

from backend.app.db import database_connection, initialize_database
from backend.app.main import create_app


class MemorySecrets:
    def __init__(self, values=None, *, fail_set=False):
        self.values = dict(values or {})
        self.fail_set = fail_set

    def get(self, name): return self.values.get(name)
    def set(self, name, value):
        if self.fail_set: raise RuntimeError("credential vault unavailable")
        self.values[name] = value
    def delete(self, name): self.values.pop(name, None)


def insert_legacy_agent(database, *, agent_id="agent-1", base_url="https://old.test/v1", model="old-model"):
    with database_connection(database) as connection:
        connection.execute(
            "INSERT INTO agents(id,name,base_url,model) VALUES(?,?,?,?)",
            (agent_id, "Legacy", base_url, model),
        )


def test_startup_migrates_legacy_agent_configuration_and_secret_once(tmp_path):
    database = tmp_path / "legacy.sqlite3"
    initialize_database(database)
    insert_legacy_agent(database)
    secrets = MemorySecrets({"agent:agent-1:api_key": "legacy-secret"})
    app = create_app(database, data_dir=tmp_path / "data", secret_store=secrets)

    with TestClient(app):
        with database_connection(database) as connection:
            agent = connection.execute("SELECT model_config_id FROM agents WHERE id='agent-1'").fetchone()
            model = connection.execute(
                "SELECT id,name,base_url,model,credential_migrated FROM model_configs WHERE id=?",
                (agent["model_config_id"],),
            ).fetchone()
    assert model["name"] == "Legacy · old-model"
    assert model["base_url"] == "https://old.test/v1"
    assert model["model"] == "old-model"
    assert model["credential_migrated"] == 1
    assert secrets.get(f"model:{model['id']}:api_key") == "legacy-secret"
    assert secrets.get("agent:agent-1:api_key") is None

    with TestClient(app):
        with database_connection(database) as connection:
            assert connection.execute("SELECT COUNT(*) FROM model_configs").fetchone()[0] == 1


def test_startup_migration_failure_keeps_legacy_fields_and_key_and_warns(tmp_path):
    database = tmp_path / "legacy-failure.sqlite3"
    initialize_database(database)
    insert_legacy_agent(database)
    secrets = MemorySecrets({"agent:agent-1:api_key": "legacy-secret"}, fail_set=True)
    app = create_app(database, data_dir=tmp_path / "data", secret_store=secrets)

    with TestClient(app):
        with database_connection(database) as connection:
            agent = connection.execute(
                "SELECT base_url,model,model_config_id FROM agents WHERE id='agent-1'"
            ).fetchone()
    assert agent["base_url"] == "https://old.test/v1"
    assert agent["model"] == "old-model"
    assert agent["model_config_id"] is not None
    assert secrets.get("agent:agent-1:api_key") == "legacy-secret"
    assert app.state.model_migration_warnings
    with database_connection(database) as connection:
        assert connection.execute(
            "SELECT credential_migrated FROM model_configs WHERE id=?", (agent["model_config_id"],)
        ).fetchone()[0] == 0


def test_startup_migration_missing_legacy_endpoint_warns_without_losing_agent(tmp_path):
    database = tmp_path / "legacy-missing.sqlite3"
    initialize_database(database)
    insert_legacy_agent(database, base_url="", model="")
    app = create_app(database, data_dir=tmp_path / "data", secret_store=MemorySecrets())

    with TestClient(app):
        with database_connection(database) as connection:
            agent = connection.execute(
                "SELECT base_url,model,model_config_id FROM agents WHERE id='agent-1'"
            ).fetchone()
    assert agent["base_url"] == ""
    assert agent["model"] == ""
    assert agent["model_config_id"] is not None
    assert app.state.model_migration_warnings


def test_invalid_legacy_endpoint_keeps_old_secret_for_later_retry(tmp_path):
    database = tmp_path / "incomplete.sqlite3"
    initialize_database(database)
    insert_legacy_agent(database, base_url="", model="")
    secrets = MemorySecrets({"agent:agent-1:api_key": "legacy-secret"})

    with TestClient(create_app(database, data_dir=tmp_path / "data", secret_store=secrets)):
        with database_connection(database) as connection:
            model_id = connection.execute("SELECT model_config_id FROM agents WHERE id='agent-1'").fetchone()[0]
            migrated = connection.execute("SELECT credential_migrated FROM model_configs WHERE id=?", (model_id,)).fetchone()[0]

    assert migrated == 0
    assert secrets.get("agent:agent-1:api_key") == "legacy-secret"
    assert secrets.get(f"model:{model_id}:api_key") is None
