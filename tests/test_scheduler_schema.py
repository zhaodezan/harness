import sqlite3

from backend.app.db import database_connection, initialize_database


def test_schema_19_creates_task_and_run_tables_with_cascade(tmp_path):
    database = tmp_path / "scheduler.sqlite3"
    initialize_database(database)
    initialize_database(database)
    with sqlite3.connect(database) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        task_columns = {row[1] for row in connection.execute("PRAGMA table_info(scheduled_tasks)")}
        run_columns = {row[1] for row in connection.execute("PRAGMA table_info(scheduled_task_runs)")}
        versions = [row[0] for row in connection.execute("SELECT version FROM schema_migrations ORDER BY version")]
        foreign_keys = connection.execute("PRAGMA foreign_key_list(scheduled_task_runs)").fetchall()
    assert {"scheduled_tasks", "scheduled_task_runs"} <= tables
    assert {"script", "cron_expression", "timezone", "enabled", "confirmed_hash", "next_run_at"} <= task_columns
    assert {"status", "stdout", "stderr", "trigger", "exit_code"} <= run_columns
    assert 19 in versions
    assert any(row[2] == "scheduled_tasks" and row[6].upper() == "CASCADE" for row in foreign_keys)
