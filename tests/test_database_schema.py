import sqlite3

from backend.app.db import initialize_database


def test_schema_18_creates_database_connections_and_agent_authorization(tmp_path):
    database = tmp_path / "database.sqlite3"
    initialize_database(database)
    initialize_database(database)
    with sqlite3.connect(database) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        columns = {row[1] for row in connection.execute("PRAGMA table_info(database_connections)")}
        agent_columns = {row[1] for row in connection.execute("PRAGMA table_info(agents)")}
        versions = [row[0] for row in connection.execute("SELECT version FROM schema_migrations ORDER BY version")]
    assert "database_connections" in tables
    assert {"id", "name", "db_type", "sqlite_path", "host", "port", "database_name", "username", "readonly", "last_tested_at"} <= columns
    assert "password" not in columns
    assert "selected_database_connections_json" in agent_columns
    assert "selected_agents_json" in agent_columns
    assert "sandbox_code_enabled" in agent_columns
    assert "app_settings" in tables
    assert 18 in versions


def test_schema_18_repairs_missing_authorization_column(tmp_path):
    database = tmp_path / "partial.sqlite3"
    initialize_database(database)
    with sqlite3.connect(database) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(agents)")}
        if "selected_database_connections_json" not in columns:
            connection.execute("ALTER TABLE agents ADD COLUMN selected_database_connections_json TEXT NOT NULL DEFAULT '[]'")
        connection.execute("ALTER TABLE agents DROP COLUMN selected_database_connections_json")
        connection.execute("DELETE FROM schema_migrations WHERE version=18")
    initialize_database(database)
    with sqlite3.connect(database) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(agents)")}
    assert "selected_database_connections_json" in columns
