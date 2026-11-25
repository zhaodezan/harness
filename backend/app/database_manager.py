import json
import re
import sqlite3
import ssl
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pymysql
import sqlglot
from sqlglot import exp

from backend.app.db import database_connection
from backend.app.tools import ToolDefinition


MAX_RESULT_ROWS = 100
MAX_RESULT_CHARS = 50_000
MAX_SCHEMA_TABLES = 100
MAX_SCHEMA_COLUMNS = 256
QUERY_TIMEOUT_SECONDS = 5
_FORBIDDEN_SQL_TOKENS = {
    "INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "ALTER", "DROP", "TRUNCATE",
    "ATTACH", "DETACH", "PRAGMA", "VACUUM", "GRANT", "REVOKE", "CALL", "EXEC",
    "EXECUTE", "SET", "USE", "LOCK", "UNLOCK", "LOAD", "INTO", "GET_LOCK", "RELEASE_LOCK",
    "SLEEP", "BENCHMARK", "LOAD_FILE", "IS_FREE_LOCK", "IS_USED_LOCK",
}


class DatabaseManagerError(ValueError):
    """Safe, user-displayable database operation error."""


def _sql_tokens(sql: str) -> list[str]:
    if not isinstance(sql, str) or not sql.strip() or len(sql) > 20_000:
        raise DatabaseManagerError("SQL 不能为空且不能超过 20,000 字符")
    tokens = []
    index = 0
    semicolons = 0
    while index < len(sql):
        char = sql[index]
        if char.isspace():
            index += 1
            continue
        if sql.startswith("--", index):
            end = sql.find("\n", index + 2)
            index = len(sql) if end < 0 else end + 1
            continue
        if sql.startswith("/*", index):
            end = sql.find("*/", index + 2)
            if end < 0:
                raise DatabaseManagerError("SQL 注释格式无效")
            index = end + 2
            continue
        if char in "'\"`":
            delimiter = char
            index += 1
            while index < len(sql):
                if sql[index] == "\\" and delimiter != "`":
                    index += 2
                    continue
                if sql[index] == delimiter:
                    if index + 1 < len(sql) and sql[index + 1] == delimiter:
                        index += 2
                        continue
                    index += 1
                    break
                index += 1
            else:
                raise DatabaseManagerError("SQL 引号未闭合")
            tokens.append("LITERAL")
            continue
        if char == ";":
            semicolons += 1
            if sql[index + 1:].strip():
                raise DatabaseManagerError("只允许执行一条 SQL 语句")
            index += 1
            continue
        match = re.match(r"[A-Za-z_][A-Za-z_0-9$]*", sql[index:])
        if match:
            tokens.append(match.group(0).upper())
            index += len(match.group(0))
            continue
        tokens.append(char)
        index += 1
    if semicolons > 1:
        raise DatabaseManagerError("只允许执行一条 SQL 语句")
    return tokens


def _validate_read_sql(sql: str, dialect: str = "sqlite") -> list[str]:
    tokens = _sql_tokens(sql)
    try:
        expressions = sqlglot.parse(sql, read=dialect)
    except sqlglot.errors.ParseError as error:
        raise DatabaseManagerError("SQL 语法无效") from error
    expressions = [expression for expression in expressions if expression is not None]
    if len(expressions) != 1:
        raise DatabaseManagerError("只允许执行一条 SQL 语句")
    expression = expressions[0]
    permitted = isinstance(expression, (exp.Query, exp.Show, exp.Describe))
    if not permitted or not tokens or tokens[0] not in {"SELECT", "WITH", "EXPLAIN", "SHOW", "DESCRIBE", "DESC"}:
        raise DatabaseManagerError("只读查询只允许 SELECT、WITH、EXPLAIN、SHOW 或 DESCRIBE")
    if _FORBIDDEN_SQL_TOKENS.intersection(tokens):
        raise DatabaseManagerError("只读查询包含禁止的写入或会话操作")
    for function in expression.find_all(exp.Anonymous):
        if function.name.upper() in {"GET_LOCK", "RELEASE_LOCK", "IS_FREE_LOCK", "IS_USED_LOCK", "SLEEP", "BENCHMARK", "LOAD_FILE"}:
            raise DatabaseManagerError("只读查询包含不允许的副作用函数")
    return tokens


def _validate_write_sql(sql: str, dialect: str = "sqlite") -> list[str]:
    tokens = _sql_tokens(sql)
    try:
        expressions = sqlglot.parse(sql, read=dialect)
    except sqlglot.errors.ParseError as error:
        raise DatabaseManagerError("SQL 语法无效") from error
    expressions = [expression for expression in expressions if expression is not None]
    if len(expressions) != 1:
        raise DatabaseManagerError("只允许执行一条 SQL 语句")
    expression = expressions[0]
    allowed_types = (exp.Insert, exp.Update, exp.Delete, exp.Create, exp.Alter, exp.Drop, exp.TruncateTable)
    if not isinstance(expression, allowed_types) or not tokens:
        raise DatabaseManagerError("变更操作只允许 INSERT、UPDATE、DELETE 或 DDL 语句")
    if any(token in {"ATTACH", "DETACH", "PRAGMA", "VACUUM", "GRANT", "REVOKE", "CALL", "EXEC", "EXECUTE", "USE", "LOAD"} for token in tokens):
        raise DatabaseManagerError("SQL 包含不支持的管理或会话操作")
    return tokens


def _safe_rows(columns: list[str], rows: list[tuple[Any, ...]], *, truncated: bool) -> dict:
    if len(columns) > 256:
        columns = columns[:256]
        rows = [tuple(row[:256]) for row in rows]
        truncated = True
    columns = [str(column)[:256] for column in columns]
    values = []
    serialized_chars = len(json.dumps(columns, ensure_ascii=False))
    for row in rows[:MAX_RESULT_ROWS]:
        clean = []
        for value in row:
            if isinstance(value, bytes):
                value = value[:8000].decode("utf-8", errors="replace")
                if len(value) == 8000:
                    value += "…[字段截断]"
            elif isinstance(value, str) and len(value) > 8000:
                value = value[:8000] + "…[字段截断]"
            elif value is not None and not isinstance(value, (int, float, bool)):
                value = str(value)[:8000]
            clean.append(value)
        encoded = json.dumps(clean, ensure_ascii=False, default=str)
        if serialized_chars + len(encoded) > MAX_RESULT_CHARS:
            truncated = True
            break
        values.append(clean)
        serialized_chars += len(encoded)
    return {
        "columns": columns,
        "rows": values,
        "row_count": len(values),
        "truncated": truncated or len(rows) > len(values),
    }


class DatabaseManager:
    def __init__(self, database_path, secret_store):
        self.database_path = Path(database_path).resolve()
        self.secret_store = secret_store

    def _config(self, connection_id: str) -> dict:
        with database_connection(self.database_path) as connection:
            row = connection.execute(
                "SELECT * FROM database_connections WHERE id=?", (connection_id,)
            ).fetchone()
        if row is None:
            raise DatabaseManagerError("数据库连接不存在")
        return dict(row)

    def _sqlite_path(self, config: dict, *, readonly: bool) -> Path:
        raw = config["sqlite_path"]
        path = Path(raw).expanduser()
        if not path.is_absolute():
            raise DatabaseManagerError("SQLite 文件路径必须是绝对路径")
        if path.is_symlink():
            raise DatabaseManagerError("不允许使用符号链接作为 SQLite 数据库")
        try:
            resolved = path.resolve(strict=True)
        except OSError as error:
            raise DatabaseManagerError("SQLite 数据库文件不存在或不可访问") from error
        if resolved == self.database_path or not resolved.is_file():
            raise DatabaseManagerError("不能连接应用内部数据库；请选择已有的 SQLite 数据文件")
        if readonly and not config["readonly"]:
            # A read-only operation remains read-only even for a writable connection.
            pass
        return resolved

    def _connect(self, config: dict, *, readonly: bool):
        if config["db_type"] == "sqlite":
            path = self._sqlite_path(config, readonly=readonly)
            uri = f"file:{quote(str(path).replace(chr(92), '/'), safe='/:')}?mode={'ro' if readonly else 'rw'}"
            connection = sqlite3.connect(uri, uri=True, timeout=QUERY_TIMEOUT_SECONDS)
            connection.row_factory = sqlite3.Row
            if readonly:
                connection.execute("PRAGMA query_only=ON")
                deadline = time.monotonic() + QUERY_TIMEOUT_SECONDS
                connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
            return connection
        if config["db_type"] != "mysql":
            raise DatabaseManagerError("数据库类型不支持")
        password = self.secret_store.get(f"database:{config['id']}:password")
        if not password:
            raise DatabaseManagerError("请先保存 MySQL 密码")
        try:
            connection = pymysql.connect(
                host=config["host"], port=int(config["port"]), user=config["username"],
                password=password, database=config["database_name"], charset="utf8mb4",
                connect_timeout=5, read_timeout=QUERY_TIMEOUT_SECONDS,
                write_timeout=QUERY_TIMEOUT_SECONDS, autocommit=False,
                init_command=f"SET SESSION MAX_EXECUTION_TIME={QUERY_TIMEOUT_SECONDS * 1000}",
                ssl=ssl.create_default_context() if config.get("ssl") else None,
            )
            if readonly:
                with connection.cursor() as cursor:
                    cursor.execute("START TRANSACTION READ ONLY")
            return connection
        except Exception as error:
            raise DatabaseManagerError("MySQL 连接失败，请检查地址、数据库、权限和凭据") from error

    def test_connection(self, connection_id: str) -> dict:
        config = self._config(connection_id)
        connection = self._connect(config, readonly=True)
        try:
            if config["db_type"] == "sqlite":
                connection.execute("SELECT 1").fetchone()
            else:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    cursor.fetchone()
            with database_connection(self.database_path) as app_db:
                app_db.execute(
                    "UPDATE database_connections SET last_tested_at=CURRENT_TIMESTAMP WHERE id=?",
                    (connection_id,),
                )
            return {"ok": True}
        except DatabaseManagerError:
            raise
        except Exception as error:
            raise DatabaseManagerError("数据库连接测试失败") from error
        finally:
            connection.close()

    def schema(self, connection_id: str) -> dict:
        config = self._config(connection_id)
        connection = self._connect(config, readonly=True)
        try:
            if config["db_type"] == "sqlite":
                tables = connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name LIMIT ?",
                    (MAX_SCHEMA_TABLES + 1,),
                ).fetchall()
                result = []
                for table in tables[:MAX_SCHEMA_TABLES]:
                    name = table["name"]
                    quoted_name = "'" + name.replace("'", "''") + "'"
                    columns = connection.execute(f"PRAGMA table_info({quoted_name})").fetchall()
                    result.append({
                        "name": name,
                        "columns": [{"name": col["name"], "type": col["type"]} for col in columns[:MAX_SCHEMA_COLUMNS]],
                        "columns_truncated": len(columns) > MAX_SCHEMA_COLUMNS,
                    })
                return {"tables": result, "truncated": len(tables) > MAX_SCHEMA_TABLES}
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
                    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_TYPE = 'BASE TABLE' "
                    "ORDER BY TABLE_NAME LIMIT %s",
                    (MAX_SCHEMA_TABLES + 1,),
                )
                table_names = [row[0] for row in cursor.fetchall()]
                result = []
                for name in table_names[:MAX_SCHEMA_TABLES]:
                    cursor.execute(
                        "SELECT COLUMN_NAME, COLUMN_TYPE FROM INFORMATION_SCHEMA.COLUMNS "
                        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s "
                        "ORDER BY ORDINAL_POSITION LIMIT %s",
                        (name, MAX_SCHEMA_COLUMNS + 1),
                    )
                    columns = cursor.fetchall()
                    result.append({
                        "name": name,
                        "columns": [{"name": row[0], "type": row[1]} for row in columns[:MAX_SCHEMA_COLUMNS]],
                        "columns_truncated": len(columns) > MAX_SCHEMA_COLUMNS,
                    })
                return {"tables": result, "truncated": len(table_names) > MAX_SCHEMA_TABLES}
        finally:
            connection.close()

    def query(self, connection_id: str, sql: str) -> dict:
        config = self._config(connection_id)
        dialect = "mysql" if config["db_type"] == "mysql" else "sqlite"
        tokens = _validate_read_sql(sql, dialect)
        if tokens and tokens[0] in {"SELECT", "WITH"}:
            query_sql = sql.strip().removesuffix(";").rstrip()
            sql = f"SELECT * FROM ({query_sql}) AS __agent_result LIMIT {MAX_RESULT_ROWS + 1}"
        connection = self._connect(config, readonly=True)
        try:
            if config["db_type"] == "sqlite":
                cursor = connection.execute(sql)
                rows = cursor.fetchmany(MAX_RESULT_ROWS + 1)
                return _safe_rows([col[0] for col in cursor.description or []], [tuple(row) for row in rows], truncated=len(rows) > MAX_RESULT_ROWS)
            with connection.cursor(pymysql.cursors.SSCursor) as cursor:
                cursor.execute(sql)
                rows = cursor.fetchmany(MAX_RESULT_ROWS + 1) if cursor.description else []
                return _safe_rows([col[0] for col in cursor.description or []], rows, truncated=len(rows) > MAX_RESULT_ROWS)
        except DatabaseManagerError:
            raise
        except Exception as error:
            raise DatabaseManagerError("数据库查询失败或超时") from error
        finally:
            connection.close()

    def write(self, connection_id: str, sql: str, *, approved: bool) -> dict:
        config = self._config(connection_id)
        _validate_write_sql(sql, "mysql" if config["db_type"] == "mysql" else "sqlite")
        if config["readonly"]:
            raise DatabaseManagerError("此连接配置为只读；请编辑连接并明确启用变更操作")
        if not approved:
            raise DatabaseManagerError("数据库变更必须先获得用户审批")
        connection = self._connect(config, readonly=False)
        try:
            if config["db_type"] == "sqlite":
                cursor = connection.execute(sql)
                affected = cursor.rowcount
            else:
                with connection.cursor() as cursor:
                    cursor.execute(sql)
                    affected = cursor.rowcount
            connection.commit()
            return {"affected_rows": max(0, affected)}
        except Exception as error:
            connection.rollback()
            raise DatabaseManagerError("数据库变更失败") from error
        finally:
            connection.close()

    def is_tool_authorized(
        self, agent_id: str, plugin_id: str, *, draft_connection_ids: list[str] | None = None
    ) -> bool:
        parts = plugin_id.split(":")
        if len(parts) != 3 or parts[0] != "database":
            return False
        connection_id = parts[1]
        if draft_connection_ids is None:
            with database_connection(self.database_path) as connection:
                agent = connection.execute(
                    "SELECT selected_database_connections_json FROM agents WHERE id=?", (agent_id,)
                ).fetchone()
            if agent is None:
                return False
            try:
                granted_ids = json.loads(agent["selected_database_connections_json"] or "[]")
            except (TypeError, json.JSONDecodeError):
                return False
        else:
            granted_ids = draft_connection_ids
        if connection_id not in granted_ids:
            return False
        with database_connection(self.database_path) as connection:
            exists = connection.execute(
                "SELECT 1 FROM database_connections WHERE id=?", (connection_id,)
            ).fetchone()
        return exists is not None

    def close(self, connection_id: str) -> None:
        # Connections are opened per operation and always closed in the operation finally block.
        return None

    def register_tools(self, registry, connection_ids: list[str] | None = None) -> None:
        allowed_ids = set(connection_ids or [])
        with database_connection(self.database_path) as connection:
            rows = connection.execute("SELECT id,name,readonly FROM database_connections").fetchall()
        registry.unregister_prefix("database_")
        for row in rows:
            if allowed_ids and row["id"] not in allowed_ids:
                continue
            connection_id = row["id"]
            name = row["name"]
            registry.register(ToolDefinition(
                name=f"database_{connection_id}_schema",
                description=f"只读查看数据库连接「{name}」的表和字段结构。",
                input_schema={"type": "object", "properties": {}, "additionalProperties": False},
                risk="read_only", plugin_id=f"database:{connection_id}:schema",
                handler=lambda _args, cid=connection_id: self.schema(cid),
            ))
            registry.register(ToolDefinition(
                name=f"database_{connection_id}_query",
                description=f"对数据库连接「{name}」执行一条只读 SQL 查询。只允许 SELECT/WITH/EXPLAIN 等只读语句。",
                input_schema={"type": "object", "properties": {"sql": {"type": "string", "maxLength": 20000}}, "required": ["sql"], "additionalProperties": False},
                risk="read_only", plugin_id=f"database:{connection_id}:query",
                handler=lambda args, cid=connection_id: self.query(cid, args["sql"]),
            ))
            if not row["readonly"]:
                registry.register(ToolDefinition(
                    name=f"database_{connection_id}_write",
                    description=f"对数据库连接「{name}」执行一条变更 SQL；必须逐次取得用户审批。",
                    input_schema={"type": "object", "properties": {"sql": {"type": "string", "maxLength": 20000}}, "required": ["sql"], "additionalProperties": False},
                    risk="side_effect", plugin_id=f"database:{connection_id}:write",
                    handler=lambda args, cid=connection_id: self.write(cid, args["sql"], approved=True),
                ))
