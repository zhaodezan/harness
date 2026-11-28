from pathlib import Path
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager


SCHEMA_VERSION = 27


def connect_database(database_path: str | Path) -> sqlite3.Connection:
    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


@contextmanager
def database_connection(database_path: str | Path) -> Iterator[sqlite3.Connection]:
    connection = connect_database(database_path)
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize_database(database_path: str | Path) -> None:
    with database_connection(database_path) as connection:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        applied = {
            row[0]
            for row in connection.execute("SELECT version FROM schema_migrations")
        }
        if 1 not in applied:
            connection.executescript(
                """
                BEGIN IMMEDIATE;
                CREATE TABLE agents (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    provider TEXT NOT NULL DEFAULT 'openai_compatible',
                    base_url TEXT NOT NULL DEFAULT '',
                    model TEXT NOT NULL DEFAULT '',
                    system_prompt TEXT NOT NULL DEFAULT '',
                    mode TEXT NOT NULL DEFAULT 'react'
                        CHECK (mode IN ('react', 'plan_execute')),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE sessions (
                    id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
                    title TEXT NOT NULL DEFAULT '新对话',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE messages (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    role TEXT NOT NULL CHECK (role IN ('system', 'user', 'assistant', 'tool')),
                    content TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX idx_sessions_agent_updated
                    ON sessions(agent_id, updated_at DESC);
                CREATE INDEX idx_messages_session_created
                    ON messages(session_id, created_at);
                INSERT INTO schema_migrations(version) VALUES (1);
                COMMIT;
                """
            )
            applied.add(1)

        if 2 not in applied:
            connection.executescript(
                """
                BEGIN IMMEDIATE;
                ALTER TABLE sessions ADD COLUMN parent_session_id TEXT
                    REFERENCES sessions(id) ON DELETE SET NULL;
                ALTER TABLE sessions ADD COLUMN branched_from_message_id TEXT;
                CREATE INDEX idx_sessions_parent ON sessions(parent_session_id);
                INSERT INTO schema_migrations(version) VALUES (2);
                COMMIT;
                """
            )
            applied.add(2)

        if 3 not in applied:
            connection.executescript(
                """
                BEGIN IMMEDIATE;
                CREATE TABLE run_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    occurred_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    run_id TEXT NOT NULL,
                    session_id TEXT,
                    level TEXT NOT NULL,
                    subsystem TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    duration_ms REAL,
                    summary TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX idx_run_events_time ON run_events(occurred_at DESC);
                CREATE INDEX idx_run_events_run ON run_events(run_id, id);
                CREATE INDEX idx_run_events_session ON run_events(session_id, occurred_at DESC);
                INSERT INTO schema_migrations(version) VALUES (3);
                COMMIT;
                """
            )
            applied.add(3)

        if 4 not in applied:
            connection.executescript(
                """
                BEGIN IMMEDIATE;
                CREATE TABLE memories (
                    id TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    scope TEXT NOT NULL CHECK (scope IN ('global', 'agent')),
                    agent_id TEXT REFERENCES agents(id) ON DELETE CASCADE,
                    source TEXT NOT NULL DEFAULT 'user_confirmed',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    CHECK ((scope = 'global' AND agent_id IS NULL) OR
                           (scope = 'agent' AND agent_id IS NOT NULL))
                );
                CREATE INDEX idx_memories_scope_agent ON memories(scope, agent_id, updated_at DESC);
                INSERT INTO schema_migrations(version) VALUES (4);
                COMMIT;
                """
            )
            applied.add(4)

        if 5 not in applied:
            connection.executescript(
                """
                BEGIN IMMEDIATE;
                CREATE TABLE prompt_versions (
                    id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX idx_prompt_versions_agent ON prompt_versions(agent_id, created_at DESC);
                INSERT INTO prompt_versions(id, agent_id, content)
                    SELECT lower(hex(randomblob(16))), id, system_prompt FROM agents;
                INSERT INTO schema_migrations(version) VALUES (5);
                COMMIT;
                """
            )
            applied.add(5)

        if 6 not in applied:
            connection.executescript(
                """
                BEGIN IMMEDIATE;
                ALTER TABLE agents ADD COLUMN context_window INTEGER NOT NULL DEFAULT 32768;
                CREATE TABLE context_summaries (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    run_id TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    summarized_message_count INTEGER NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX idx_context_summaries_session ON context_summaries(session_id, created_at DESC);
                INSERT INTO schema_migrations(version) VALUES (6);
                COMMIT;
                """
            )
            applied.add(6)

        if 7 not in applied:
            connection.executescript(
                """
                BEGIN IMMEDIATE;
                CREATE TABLE mcp_servers (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE,
                    transport TEXT NOT NULL CHECK (transport IN ('stdio', 'http')),
                    command TEXT NOT NULL DEFAULT '',
                    args_json TEXT NOT NULL DEFAULT '[]',
                    cwd TEXT NOT NULL DEFAULT '',
                    url TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                INSERT INTO schema_migrations(version) VALUES (7);
                COMMIT;
                """
            )
            applied.add(7)

        if 8 not in applied:
            connection.executescript(
                """
                BEGIN IMMEDIATE;
                ALTER TABLE agents ADD COLUMN skill_directory TEXT NOT NULL DEFAULT '';
                ALTER TABLE agents ADD COLUMN selected_skills_json TEXT NOT NULL DEFAULT '[]';
                INSERT INTO schema_migrations(version) VALUES (8);
                COMMIT;
                """
            )
            applied.add(8)

        if 9 not in applied:
            connection.executescript(
                """
                BEGIN IMMEDIATE;
                ALTER TABLE run_events ADD COLUMN input_tokens INTEGER;
                ALTER TABLE run_events ADD COLUMN output_tokens INTEGER;
                INSERT INTO schema_migrations(version) VALUES (9);
                COMMIT;
                """
            )
            applied.add(9)

        if 10 not in applied:
            connection.executescript(
                """
                BEGIN IMMEDIATE;
                ALTER TABLE agents ADD COLUMN prompt_variables_json TEXT NOT NULL DEFAULT '{}';
                INSERT INTO schema_migrations(version) VALUES (10);
                COMMIT;
                """
            )
            applied.add(10)

        if 11 not in applied:
            connection.executescript(
                """
                BEGIN IMMEDIATE;
                ALTER TABLE mcp_servers ADD COLUMN env_names_json TEXT NOT NULL DEFAULT '[]';
                INSERT INTO schema_migrations(version) VALUES (11);
                COMMIT;
                """
            )
            applied.add(11)

        if 12 not in applied:
            connection.executescript(
                """
                BEGIN IMMEDIATE;
                ALTER TABLE agents ADD COLUMN input_rate_per_million REAL;
                ALTER TABLE agents ADD COLUMN output_rate_per_million REAL;
                ALTER TABLE run_events ADD COLUMN estimated_cost REAL;
                ALTER TABLE run_events ADD COLUMN cost_currency TEXT;
                INSERT INTO schema_migrations(version) VALUES (12);
                COMMIT;
                """
            )
            applied.add(12)

        if 14 not in applied:
            connection.executescript(
                """
                BEGIN IMMEDIATE;
                ALTER TABLE agents ADD COLUMN selected_mcp_tools_json TEXT NOT NULL DEFAULT '[]';
                ALTER TABLE agents ADD COLUMN selected_local_tools_json TEXT NOT NULL DEFAULT '[]';
                INSERT INTO schema_migrations(version) VALUES (14);
                COMMIT;
                """
            )
            applied.add(14)

        if 15 not in applied:
            connection.executescript(
                """
                BEGIN IMMEDIATE;
                CREATE TABLE model_configs (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    base_url TEXT NOT NULL DEFAULT '',
                    model TEXT NOT NULL DEFAULT '',
                    legacy_agent_id TEXT UNIQUE REFERENCES agents(id) ON DELETE SET NULL,
                    credential_migrated INTEGER NOT NULL DEFAULT 0 CHECK (credential_migrated IN (0, 1)),
                    last_tested_at TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX idx_model_configs_name ON model_configs(name COLLATE NOCASE);
                ALTER TABLE agents ADD COLUMN model_config_id TEXT REFERENCES model_configs(id) ON DELETE RESTRICT;
                CREATE INDEX idx_agents_model_config ON agents(model_config_id);
                INSERT INTO schema_migrations(version) VALUES (15);
                COMMIT;
                """
            )
            applied.add(15)

        if 16 not in applied:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """CREATE TABLE IF NOT EXISTS model_configs (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    base_url TEXT NOT NULL DEFAULT '',
                    model TEXT NOT NULL DEFAULT '',
                    legacy_agent_id TEXT UNIQUE REFERENCES agents(id) ON DELETE SET NULL,
                    credential_migrated INTEGER NOT NULL DEFAULT 0 CHECK (credential_migrated IN (0, 1)),
                    last_tested_at TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )"""
            )
            agent_columns = {row[1] for row in connection.execute("PRAGMA table_info(agents)")}
            if "model_config_id" not in agent_columns:
                connection.execute(
                    "ALTER TABLE agents ADD COLUMN model_config_id TEXT REFERENCES model_configs(id) ON DELETE RESTRICT"
                )
            connection.execute("CREATE INDEX IF NOT EXISTS idx_model_configs_name ON model_configs(name COLLATE NOCASE)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_agents_model_config ON agents(model_config_id)")
            connection.execute("INSERT INTO schema_migrations(version) VALUES (16)")
            connection.commit()
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """CREATE TABLE IF NOT EXISTS knowledge_bases (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS knowledge_documents (
                id TEXT PRIMARY KEY,
                knowledge_base_id TEXT NOT NULL
                    REFERENCES knowledge_bases(id) ON DELETE CASCADE,
                filename TEXT NOT NULL,
                extension TEXT NOT NULL,
                stored_path TEXT NOT NULL DEFAULT '',
                extracted_text TEXT NOT NULL DEFAULT '',
                char_count INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL
                    CHECK (status IN ('processing', 'ready', 'failed')),
                error TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        agent_columns = {row[1] for row in connection.execute("PRAGMA table_info(agents)")}
        if "selected_knowledge_bases_json" not in agent_columns:
            connection.execute(
                "ALTER TABLE agents ADD COLUMN selected_knowledge_bases_json TEXT NOT NULL DEFAULT '[]'"
            )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_knowledge_documents_base "
            "ON knowledge_documents(knowledge_base_id, status, updated_at DESC)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_knowledge_bases_updated "
            "ON knowledge_bases(updated_at DESC)"
        )
        if 17 not in applied:
            connection.execute("INSERT INTO schema_migrations(version) VALUES (17)")
        connection.commit()
        if 18 not in applied:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """CREATE TABLE IF NOT EXISTS database_connections (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    db_type TEXT NOT NULL CHECK (db_type IN ('sqlite', 'mysql')),
                    sqlite_path TEXT NOT NULL DEFAULT '',
                    host TEXT NOT NULL DEFAULT '',
                    port INTEGER NOT NULL DEFAULT 3306,
                    database_name TEXT NOT NULL DEFAULT '',
                    username TEXT NOT NULL DEFAULT '',
                    ssl INTEGER NOT NULL DEFAULT 1 CHECK (ssl IN (0, 1)),
                    readonly INTEGER NOT NULL DEFAULT 1 CHECK (readonly IN (0, 1)),
                    last_tested_at TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )"""
            )
            connection_columns = {row[1] for row in connection.execute("PRAGMA table_info(database_connections)")}
            if "ssl" not in connection_columns:
                connection.execute("ALTER TABLE database_connections ADD COLUMN ssl INTEGER NOT NULL DEFAULT 1")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_database_connections_name "
                "ON database_connections(name COLLATE NOCASE)"
            )
            agent_columns = {row[1] for row in connection.execute("PRAGMA table_info(agents)")}
            if "selected_database_connections_json" not in agent_columns:
                connection.execute(
                    "ALTER TABLE agents ADD COLUMN selected_database_connections_json TEXT NOT NULL DEFAULT '[]'"
                )
            connection.execute("INSERT INTO schema_migrations(version) VALUES (18)")
            connection.commit()
            applied.add(18)

        if 19 not in applied:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """CREATE TABLE IF NOT EXISTS scheduled_tasks (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    script TEXT NOT NULL,
                    cron_expression TEXT NOT NULL,
                    timezone TEXT NOT NULL,
                    timeout_seconds INTEGER NOT NULL DEFAULT 300 CHECK (timeout_seconds BETWEEN 1 AND 3600),
                    enabled INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)),
                    confirmed_hash TEXT,
                    confirmed_at TEXT,
                    next_run_at TEXT,
                    last_run_at TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS scheduled_task_runs (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL REFERENCES scheduled_tasks(id) ON DELETE CASCADE,
                    trigger TEXT NOT NULL CHECK (trigger IN ('scheduled', 'manual')),
                    scheduled_at TEXT,
                    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    finished_at TEXT,
                    duration_ms REAL,
                    status TEXT NOT NULL CHECK (status IN ('running','succeeded','failed','timeout','skipped','interrupted')),
                    exit_code INTEGER,
                    stdout TEXT NOT NULL DEFAULT '',
                    stderr TEXT NOT NULL DEFAULT '',
                    truncated INTEGER NOT NULL DEFAULT 0 CHECK (truncated IN (0, 1)),
                    summary TEXT NOT NULL DEFAULT ''
                )"""
            )
            connection.execute("CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_due ON scheduled_tasks(enabled,next_run_at)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_scheduled_task_runs_task ON scheduled_task_runs(task_id,started_at DESC)")
            connection.execute("INSERT INTO schema_migrations(version) VALUES (19)")
            connection.commit()
            applied.add(19)

        if 20 not in applied:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """CREATE TABLE mcp_servers_new (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE,
                    transport TEXT NOT NULL CHECK (transport IN ('stdio', 'sse', 'streamable_http')),
                    command TEXT NOT NULL DEFAULT '',
                    args_json TEXT NOT NULL DEFAULT '[]',
                    cwd TEXT NOT NULL DEFAULT '',
                    url TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    env_names_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )"""
            )
            connection.execute(
                """INSERT INTO mcp_servers_new
                   (id,name,transport,command,args_json,cwd,url,enabled,env_names_json,created_at)
                   SELECT id,name,CASE WHEN transport='http' THEN 'streamable_http' ELSE transport END,
                          command,args_json,cwd,url,enabled,env_names_json,created_at
                   FROM mcp_servers"""
            )
            connection.execute("DROP TABLE mcp_servers")
            connection.execute("ALTER TABLE mcp_servers_new RENAME TO mcp_servers")
            connection.execute("INSERT INTO schema_migrations(version) VALUES (20)")
            connection.commit()
            applied.add(20)

        if 21 not in applied:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("ALTER TABLE agents ADD COLUMN selected_agents_json TEXT NOT NULL DEFAULT '[]'")
            connection.execute("ALTER TABLE agents ADD COLUMN sandbox_code_enabled INTEGER NOT NULL DEFAULT 0 CHECK (sandbox_code_enabled IN (0, 1))")
            connection.execute("INSERT INTO schema_migrations(version) VALUES (21)")
            connection.commit()
            applied.add(21)

        if 22 not in applied:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "CREATE TABLE app_settings (key TEXT PRIMARY KEY, value_json TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            )
            connection.execute("INSERT INTO schema_migrations(version) VALUES (22)")
            connection.commit()
            applied.add(22)

        if 23 not in applied:
            _allow_workspace_sessions(connection)

        if 24 not in applied:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("ALTER TABLE mcp_servers ADD COLUMN auto_connect INTEGER NOT NULL DEFAULT 0 CHECK (auto_connect IN (0, 1))")
            connection.execute("INSERT INTO schema_migrations(version) VALUES (24)")
            connection.commit()

        if 25 not in applied:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT 1 FROM schema_migrations WHERE version=25").fetchone() is None:
                connection.execute("CREATE TABLE workspace_groups (id TEXT PRIMARY KEY, name TEXT NOT NULL, path TEXT NOT NULL UNIQUE)")
                connection.execute("ALTER TABLE sessions ADD COLUMN workspace_group_id TEXT REFERENCES workspace_groups(id) ON DELETE SET NULL")
                connection.execute("ALTER TABLE sessions ADD COLUMN default_workspace TEXT")
                connection.execute("ALTER TABLE sessions ADD COLUMN execution_mode TEXT CHECK (execution_mode IN ('local', 'sandbox'))")
                connection.execute("INSERT INTO schema_migrations(version) VALUES (25)")
            connection.commit()

        if 26 not in applied:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT 1 FROM schema_migrations WHERE version=26").fetchone() is None:
                connection.execute("ALTER TABLE agents ADD COLUMN max_rounds INTEGER NOT NULL DEFAULT 500 CHECK (max_rounds = -1 OR max_rounds BETWEEN 1 AND 10000)")
                connection.execute("INSERT INTO schema_migrations(version) VALUES (26)")
            connection.commit()

        if 27 not in applied:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT 1 FROM schema_migrations WHERE version=27").fetchone() is None:
                connection.execute(
                    "ALTER TABLE agents ADD COLUMN model_binding_cleared INTEGER NOT NULL DEFAULT 0 "
                    "CHECK (model_binding_cleared IN (0, 1))"
                )
                connection.execute("INSERT INTO schema_migrations(version) VALUES (27)")
            connection.commit()


def _allow_workspace_sessions(connection: sqlite3.Connection) -> None:
    # Changing nullability requires a table rebuild. Disable cascading while the
    # original table is replaced, but keep all child tables pointing to sessions.
    connection.execute("PRAGMA foreign_keys = OFF")
    try:
        connection.execute("BEGIN IMMEDIATE")
        if connection.execute("SELECT 1 FROM schema_migrations WHERE version=23").fetchone() is None:
            connection.execute("""CREATE TABLE sessions_v23 (
                id TEXT PRIMARY KEY,
                agent_id TEXT REFERENCES agents(id) ON DELETE CASCADE,
                title TEXT NOT NULL DEFAULT '新对话',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                parent_session_id TEXT REFERENCES sessions(id) ON DELETE SET NULL,
                branched_from_message_id TEXT
            )""")
            connection.execute("""INSERT INTO sessions_v23
                SELECT id, agent_id, title, created_at, updated_at, parent_session_id, branched_from_message_id
                FROM sessions ORDER BY rowid""")
            connection.execute("DROP TABLE sessions")
            connection.execute("ALTER TABLE sessions_v23 RENAME TO sessions")
            connection.execute("CREATE INDEX idx_sessions_agent_updated ON sessions(agent_id, updated_at DESC)")
            connection.execute("CREATE INDEX idx_sessions_parent ON sessions(parent_session_id)")
            if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise sqlite3.IntegrityError("Session migration would leave an invalid foreign key")
            connection.execute("INSERT INTO schema_migrations(version) VALUES (23)")
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.execute("PRAGMA foreign_keys = ON")

