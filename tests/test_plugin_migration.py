import json

from fastapi.testclient import TestClient

from backend.app.db import database_connection, initialize_database
from backend.app.main import create_app


def test_startup_migrates_existing_skill_directory_and_mcp_access(tmp_path):
    database = tmp_path / "legacy.sqlite3"
    data_dir = tmp_path / "data"
    old_skills = tmp_path / "old-skills"
    (old_skills / "summarize").mkdir(parents=True)
    (old_skills / "summarize" / "SKILL.md").write_text(
        "---\nname: summarize\ndescription: Summarize\n---\nKeep key points.", encoding="utf-8"
    )
    initialize_database(database)
    with database_connection(database) as connection:
        connection.execute(
            "INSERT INTO agents(id,name,skill_directory,selected_skills_json) VALUES(?,?,?,?)",
            ("legacy-agent", "Legacy", str(old_skills), "[]"),
        )
        connection.execute(
            "INSERT INTO mcp_servers(id,name,transport,command) VALUES(?,?,?,?)",
            ("server-1", "files", "stdio", "python"),
        )

    with TestClient(create_app(database, data_dir=data_dir)) as client:
        agent = client.get("/api/agents/legacy-agent").json()
        managed_skills = client.get("/api/skills").json()
        with database_connection(database) as connection:
            migration = connection.execute("SELECT 1 FROM plugin_migrations WHERE version = 1").fetchone()

    assert agent["selected_skills"] == ["summarize"]
    assert agent["selected_mcp_tools"] == ["mcp:server-1:*"]
    assert [skill["name"] for skill in managed_skills] == ["summarize"]
    assert migration is not None


def test_unavailable_legacy_skills_directory_is_reported_and_preserved(tmp_path):
    database = tmp_path / "unavailable.sqlite3"
    data_dir = tmp_path / "data"
    missing_directory = tmp_path / "missing-skills"
    initialize_database(database)
    with database_connection(database) as connection:
        connection.execute(
            "INSERT INTO agents(id,name,skill_directory,selected_skills_json) VALUES(?,?,?,?)",
            ("agent", "Old Agent", str(missing_directory), "[]"),
        )

    with TestClient(create_app(database, data_dir=data_dir)) as client:
        status = client.get("/api/skills/migration-status").json()
        agent = client.get("/api/agents/agent").json()

    assert any("Old Agent" in warning for warning in status["warnings"])
    assert agent["skill_directory"] == str(missing_directory)
