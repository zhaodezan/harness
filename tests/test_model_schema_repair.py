import sqlite3

from backend.app.db import initialize_database


def test_schema_16_repairs_partially_applied_schema_15(tmp_path):
    database = tmp_path / "partial.sqlite3"
    initialize_database(database)
    with sqlite3.connect(database) as connection:
        connection.execute("DELETE FROM schema_migrations WHERE version=16")
        connection.execute("DROP INDEX idx_agents_model_config")
        connection.execute("DROP TABLE model_configs")
        connection.execute("ALTER TABLE agents DROP COLUMN model_config_id")

    initialize_database(database)
    with sqlite3.connect(database) as connection:
        versions = [row[0] for row in connection.execute("SELECT version FROM schema_migrations ORDER BY version")]
        agent_columns = {row[1] for row in connection.execute("PRAGMA table_info(agents)")}
        model_table = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='model_configs'").fetchone()

    assert versions[-1] == 23
    assert "model_config_id" in agent_columns
    assert model_table is not None
