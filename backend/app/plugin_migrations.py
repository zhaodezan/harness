import json
import shutil
import uuid
from pathlib import Path

from backend.app.db import database_connection
from backend.app.skills import discover_skills


def migrate_legacy_plugins(database_path: str | Path, store) -> list[str]:
    warnings: list[str] = []
    with database_connection(database_path) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS plugin_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        applied = connection.execute("SELECT 1 FROM plugin_migrations WHERE version = 1").fetchone()
        if applied:
            return warnings
        agents = connection.execute(
            "SELECT id,name,skill_directory,selected_skills_json FROM agents ORDER BY rowid"
        ).fetchall()
        servers = [row[0] for row in connection.execute("SELECT id FROM mcp_servers ORDER BY rowid")]
        existing_skills = {skill.name for skill in store.list_skills()}
        for agent in agents:
            selected = json.loads(agent["selected_skills_json"] or "[]")
            legacy_directory = agent["skill_directory"]
            if legacy_directory and not Path(legacy_directory).expanduser().is_dir():
                warnings.append(f"{agent['name']}：旧 Skills 目录无法访问，原路径已保留。")
            legacy = discover_skills(legacy_directory) if legacy_directory else []
            migrated_names = []
            for skill in legacy:
                if skill.name in existing_skills:
                    migrated_names.append(skill.name)
                    continue
                target = store.skills_dir / uuid.uuid4().hex
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.copytree(skill.skill_file.parent, target)
                except OSError:
                    warnings.append(f"{agent['name']}：Skill {skill.name} 无法复制到插件库，旧目录已保留。")
                    shutil.rmtree(target, ignore_errors=True)
                    continue
                existing_skills.add(skill.name)
                migrated_names.append(skill.name)
            selected_names = list(dict.fromkeys([*selected, *migrated_names]))
            if selected_names:
                connection.execute(
                    "UPDATE agents SET selected_skills_json = ? WHERE id = ?",
                    (json.dumps(selected_names), agent["id"]),
                )
            if servers:
                connection.execute(
                    "UPDATE agents SET selected_mcp_tools_json = ? WHERE id = ? AND selected_mcp_tools_json = '[]'",
                    (json.dumps([f"mcp:{server_id}:*" for server_id in servers]), agent["id"]),
                )
        connection.execute("INSERT INTO plugin_migrations(version) VALUES (1)")
    return warnings
