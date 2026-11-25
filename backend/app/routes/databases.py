from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator, model_validator

from backend.app.db import database_connection
from backend.app.database_manager import DatabaseManagerError

router = APIRouter(prefix="/api/databases", tags=["databases"])


class DatabaseConnectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    db_type: str
    sqlite_path: str = ""
    host: str = ""
    port: int = Field(default=3306, ge=1, le=65535)
    database_name: str = ""
    username: str = ""
    password: str = Field(default="", max_length=4096, repr=False)
    readonly: bool = True
    ssl: bool = True

    @field_validator("name")
    @classmethod
    def validate_name(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("数据库连接名称不能为空")
        return value

    @model_validator(mode="after")
    def validate_connection(self):
        if self.db_type == "sqlite":
            path = Path(self.sqlite_path).expanduser()
            if not path.is_absolute() or path.is_symlink() or not path.is_file():
                raise ValueError("SQLite 必须选择已有的本机数据库文件，且不能使用符号链接")
        elif self.db_type == "mysql":
            for field_name in ("host", "database_name", "username"):
                if not getattr(self, field_name).strip():
                    raise ValueError("MySQL 主机、数据库名和用户名不能为空")
            if not self.password.strip():
                raise ValueError("新建 MySQL 连接必须提供密码")
        else:
            raise ValueError("首版只支持 SQLite 和 MySQL")
        return self


class DatabaseConnectionPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    sqlite_path: str | None = None
    host: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    database_name: str | None = None
    username: str | None = None
    readonly: bool | None = None
    ssl: bool | None = None


class CredentialPayload(BaseModel):
    password: str = Field(min_length=1, max_length=4096, repr=False)


def _row(connection, connection_id):
    return connection.execute(
        "SELECT id,name,db_type,sqlite_path,host,port,database_name,username,ssl,readonly,last_tested_at,created_at,updated_at "
        "FROM database_connections WHERE id=?", (connection_id,),
    ).fetchone()


def _response(row, request: Request) -> dict:
    result = dict(row)
    result["readonly"] = bool(result["readonly"])
    result["ssl"] = bool(result["ssl"])
    result["password_configured"] = bool(
        request.app.state.secret_store.get(f"database:{result['id']}:password")
    ) if result["db_type"] == "mysql" else False
    return result


def _refresh_tools(request: Request) -> None:
    request.app.state.database_manager.register_tools(request.app.state.tool_registry)


@router.get("")
def list_databases(request: Request):
    with database_connection(request.app.state.database_path) as connection:
        rows = connection.execute(
            "SELECT id,name,db_type,sqlite_path,host,port,database_name,username,ssl,readonly,last_tested_at,created_at,updated_at "
            "FROM database_connections ORDER BY name COLLATE NOCASE,id"
        ).fetchall()
    return [_response(row, request) for row in rows]


@router.post("", status_code=201)
def create_database(payload: DatabaseConnectionCreate, request: Request):
    connection_id = uuid4().hex
    sqlite_path = payload.sqlite_path
    if payload.db_type == "sqlite":
        resolved = Path(sqlite_path).expanduser().resolve(strict=True)
        if resolved == Path(request.app.state.database_path).resolve():
            raise HTTPException(status_code=422, detail="不能连接应用内部数据库")
        sqlite_path = str(resolved)
    try:
        with database_connection(request.app.state.database_path) as connection:
            connection.execute(
                "INSERT INTO database_connections(id,name,db_type,sqlite_path,host,port,database_name,username,ssl,readonly) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (connection_id, payload.name, payload.db_type, sqlite_path,
                 payload.host.strip(), payload.port, payload.database_name.strip(),
                 payload.username.strip(), int(payload.ssl), int(payload.readonly)),
            )
        if payload.db_type == "mysql":
            request.app.state.secret_store.set(f"database:{connection_id}:password", payload.password)
    except Exception:
        with database_connection(request.app.state.database_path) as connection:
            connection.execute("DELETE FROM database_connections WHERE id=?", (connection_id,))
        raise HTTPException(status_code=503, detail="无法安全保存数据库连接凭据或配置") from None
    _refresh_tools(request)
    with database_connection(request.app.state.database_path) as connection:
        row = _row(connection, connection_id)
    return _response(row, request)


@router.get("/{connection_id}")
def get_database(connection_id: str, request: Request):
    with database_connection(request.app.state.database_path) as connection:
        row = _row(connection, connection_id)
    if row is None:
        raise HTTPException(status_code=404, detail="数据库连接不存在")
    return _response(row, request)


@router.patch("/{connection_id}")
def update_database(connection_id: str, payload: DatabaseConnectionPatch, request: Request):
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=422, detail="请提供要更新的连接字段")
    with database_connection(request.app.state.database_path) as connection:
        current = _row(connection, connection_id)
        if current is None:
            raise HTTPException(status_code=404, detail="数据库连接不存在")
        if "name" in updates:
            updates["name"] = updates["name"].strip()
            if not updates["name"]:
                raise HTTPException(status_code=422, detail="连接名称不能为空")
        if current["db_type"] == "sqlite" and "sqlite_path" in updates:
            path = Path(updates["sqlite_path"]).expanduser()
            if not path.is_absolute() or path.is_symlink() or not path.is_file():
                raise HTTPException(status_code=422, detail="SQLite 必须选择已有的数据库文件")
            updates["sqlite_path"] = str(path.resolve())
            if Path(updates["sqlite_path"]) == Path(request.app.state.database_path).resolve():
                raise HTTPException(status_code=422, detail="不能连接应用内部数据库")
        if current["db_type"] == "mysql":
            for field in ("host", "database_name", "username"):
                if field in updates and not updates[field].strip():
                    raise HTTPException(status_code=422, detail="MySQL 主机、数据库名和用户名不能为空")
        assignments = [f"{key}=?" for key in updates]
        assignments.append("last_tested_at=NULL")
        assignments.append("updated_at=CURRENT_TIMESTAMP")
        connection.execute(
            f"UPDATE database_connections SET {','.join(assignments)} WHERE id=?",
            [*updates.values(), connection_id],
        )
        row = _row(connection, connection_id)
    _refresh_tools(request)
    return _response(row, request)


@router.put("/{connection_id}/credentials", status_code=204)
def set_credentials(connection_id: str, payload: CredentialPayload, request: Request):
    with database_connection(request.app.state.database_path) as connection:
        row = _row(connection, connection_id)
    if row is None:
        raise HTTPException(status_code=404, detail="数据库连接不存在")
    if row["db_type"] != "mysql":
        raise HTTPException(status_code=409, detail="SQLite 不使用密码凭据")
    try:
        request.app.state.secret_store.set(f"database:{connection_id}:password", payload.password)
    except Exception:
        raise HTTPException(status_code=503, detail="无法安全保存数据库密码") from None
    with database_connection(request.app.state.database_path) as connection:
        connection.execute("UPDATE database_connections SET last_tested_at=NULL WHERE id=?", (connection_id,))
    return Response(status_code=204)


@router.delete("/{connection_id}/credentials", status_code=204)
def delete_credentials(connection_id: str, request: Request):
    with database_connection(request.app.state.database_path) as connection:
        row = _row(connection, connection_id)
    if row is None:
        raise HTTPException(status_code=404, detail="数据库连接不存在")
    if row["db_type"] == "mysql":
        request.app.state.secret_store.delete(f"database:{connection_id}:password")
    with database_connection(request.app.state.database_path) as connection:
        connection.execute("UPDATE database_connections SET last_tested_at=NULL WHERE id=?", (connection_id,))
    return Response(status_code=204)


@router.post("/{connection_id}/test-connection")
def test_database(connection_id: str, request: Request):
    try:
        return request.app.state.database_manager.test_connection(connection_id)
    except DatabaseManagerError as error:
        raise HTTPException(status_code=502, detail=str(error)) from None


@router.get("/{connection_id}/schema")
def get_schema(connection_id: str, request: Request):
    try:
        return request.app.state.database_manager.schema(connection_id)
    except DatabaseManagerError as error:
        raise HTTPException(status_code=502, detail=str(error)) from None


@router.delete("/{connection_id}", status_code=204)
def delete_database(connection_id: str, request: Request):
    with database_connection(request.app.state.database_path) as connection:
        row = _row(connection, connection_id)
        if row is None:
            raise HTTPException(status_code=404, detail="数据库连接不存在")
        references = []
        for agent in connection.execute("SELECT name,selected_database_connections_json FROM agents").fetchall():
            try:
                if connection_id in __import__("json").loads(agent["selected_database_connections_json"] or "[]"):
                    references.append(agent["name"])
            except Exception:
                references.append(agent["name"])
        if references:
            raise HTTPException(status_code=409, detail="数据库连接仍被 Agent 使用：" + ", ".join(references))
        connection.execute("DELETE FROM database_connections WHERE id=?", (connection_id,))
    request.app.state.secret_store.delete(f"database:{connection_id}:password")
    _refresh_tools(request)
    return Response(status_code=204)
