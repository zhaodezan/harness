import sqlite3

from backend.app.db import initialize_database


def test_initialize_database_creates_core_tables(tmp_path):
    database_path = tmp_path / "harness.sqlite3"

    initialize_database(database_path)

    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }

    assert {"agents", "sessions", "messages", "schema_migrations"} <= tables


def test_initialize_database_is_safe_to_repeat(tmp_path):
    database_path = tmp_path / "harness.sqlite3"

    initialize_database(database_path)
    initialize_database(database_path)

    with sqlite3.connect(database_path) as connection:
        migrations = connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()

    assert migrations == [(1,), (2,), (3,), (4,), (5,), (6,), (7,), (8,), (9,), (10,), (11,), (12,), (14,), (15,), (16,), (17,), (18,), (19,), (20,), (21,), (22,), (23,), (24,), (25,), (26,)]
