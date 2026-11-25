import sqlite3

from backend.app.db import initialize_database


def test_schema_17_creates_knowledge_tables_and_agent_selection(tmp_path):
    database = tmp_path / "knowledge.sqlite3"

    initialize_database(database)
    initialize_database(database)

    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        agent_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(agents)")
        }
        migration_versions = [
            row[0]
            for row in connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            )
        ]
        document_foreign_keys = connection.execute(
            "PRAGMA foreign_key_list(knowledge_documents)"
        ).fetchall()

    assert {"knowledge_bases", "knowledge_documents"} <= tables
    assert "selected_knowledge_bases_json" in agent_columns
    assert migration_versions[-1] == 23
    assert any(
        row[2] == "knowledge_bases" and row[6].upper() == "CASCADE"
        for row in document_foreign_keys
    )


def test_schema_17_repairs_missing_agent_selection_column(tmp_path):
    database = tmp_path / "partial.sqlite3"
    initialize_database(database)

    with sqlite3.connect(database) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(agents)")}
        if "selected_knowledge_bases_json" not in columns:
            connection.execute(
                "ALTER TABLE agents ADD COLUMN selected_knowledge_bases_json TEXT NOT NULL DEFAULT '[]'"
            )
        connection.execute("DELETE FROM schema_migrations WHERE version = 17")
        connection.execute(
            "ALTER TABLE agents DROP COLUMN selected_knowledge_bases_json"
        )

    initialize_database(database)

    with sqlite3.connect(database) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(agents)")}
        migration = connection.execute(
            "SELECT version FROM schema_migrations WHERE version = 17"
        ).fetchone()

    assert "selected_knowledge_bases_json" in columns
    assert migration == (17,)
