import sqlite3
from pathlib import Path

import pytest

from backend.app.db import database_connection, initialize_database
from backend.app.database_manager import DatabaseManager, DatabaseManagerError


class TestSecrets:
    def get(self, name):
        return None

    def set(self, name, value):
        pass

    def delete(self, name):
        pass


def _make_manager(tmp_path):
    source = tmp_path / "source.sqlite3"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE users(id INTEGER PRIMARY KEY, name TEXT)")
        connection.executemany("INSERT INTO users(name) VALUES(?)", [("Ada",), ("Lin",)])
    app_db = tmp_path / "app.sqlite3"
    initialize_database(app_db)
    with database_connection(app_db) as connection:
        connection.execute(
            "INSERT INTO database_connections(id,name,db_type,sqlite_path) VALUES(?,?,?,?)",
            ("db-1", "Source", "sqlite", str(source)),
        )
    return DatabaseManager(app_db, TestSecrets()), source


def test_sqlite_manager_tests_schema_and_query_with_bounded_results(tmp_path):
    manager, _ = _make_manager(tmp_path)
    assert manager.test_connection("db-1")["ok"] is True
    schema = manager.schema("db-1")
    assert schema["tables"][0]["name"] == "users"
    result = manager.query("db-1", "SELECT id,name FROM users ORDER BY id")
    assert result["columns"] == ["id", "name"]
    assert result["rows"] == [[1, "Ada"], [2, "Lin"]]


def test_read_query_rejects_writes_and_multiple_statements(tmp_path):
    manager, _ = _make_manager(tmp_path)
    with pytest.raises(DatabaseManagerError):
        manager.query("db-1", "UPDATE users SET name='x'")
    with pytest.raises(DatabaseManagerError):
        manager.query("db-1", "SELECT 1; SELECT 2")
    with pytest.raises(DatabaseManagerError):
        manager.query("db-1", "PRAGMA writable_schema=ON")


def test_write_is_single_statement_and_requires_manager_approval_flag(tmp_path):
    manager, source = _make_manager(tmp_path)
    with database_connection(manager.database_path) as connection:
        connection.execute("UPDATE database_connections SET readonly=0 WHERE id='db-1'")
    with pytest.raises(DatabaseManagerError):
        manager.write("db-1", "UPDATE users SET name='x'", approved=False)
    result = manager.write("db-1", "UPDATE users SET name='x' WHERE id=1", approved=True)
    assert result["affected_rows"] == 1
    with sqlite3.connect(source) as connection:
        assert connection.execute("SELECT name FROM users WHERE id=1").fetchone()[0] == "x"


def test_sqlite_source_is_never_deleted_by_manager(tmp_path):
    manager, source = _make_manager(tmp_path)
    manager.close("db-1")
    assert Path(source).exists()


def test_mysql_query_applies_result_limit_wrapper_only_once(tmp_path, monkeypatch):
    app_db = tmp_path / "app.sqlite3"
    initialize_database(app_db)
    with database_connection(app_db) as connection:
        connection.execute(
            "INSERT INTO database_connections(id,name,db_type,host,database_name,username) VALUES(?,?,?,?,?,?)",
            ("mysql-1", "MySQL", "mysql", "db.example", "app", "reader"),
        )
    manager = DatabaseManager(app_db, TestSecrets())

    class Cursor:
        description = [("value",)]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, sql):
            self.sql = sql

        def fetchmany(self, limit):
            return [(1,)]

    class Connection:
        def __init__(self):
            self.cursor_instance = Cursor()

        def cursor(self, *args):
            return self.cursor_instance

        def close(self):
            pass

    fake = Connection()
    monkeypatch.setattr(manager, "_connect", lambda config, readonly: fake)
    result = manager.query("mysql-1", "SELECT value FROM records")
    assert result["rows"] == [[1]]
    assert fake.cursor_instance.sql.count("AS __agent_result") == 1


def test_schema_caps_large_sqlite_metadata(tmp_path):
    manager, source = _make_manager(tmp_path)
    with sqlite3.connect(source) as connection:
        for index in range(105):
            connection.execute(f"CREATE TABLE table_{index}(value TEXT)")
    schema = manager.schema("db-1")
    assert len(schema["tables"]) == 100
    assert schema["truncated"] is True


def test_sqlite_query_wraps_cte_and_enforces_server_side_row_limit(tmp_path):
    manager, source = _make_manager(tmp_path)
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE many_rows(value INTEGER)")
        connection.executemany("INSERT INTO many_rows(value) VALUES(?)", [(value,) for value in range(150)])
    result = manager.query(
        "db-1",
        "WITH values_cte AS (SELECT value FROM many_rows) SELECT value FROM values_cte ORDER BY value",
    )
    assert result["row_count"] == 100
    assert result["truncated"] is True
