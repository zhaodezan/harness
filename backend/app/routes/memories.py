from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field, model_validator

from backend.app.db import database_connection

router = APIRouter(prefix="/api/memories", tags=["memory"])


class MemoryCreate(BaseModel):
    content: str = Field(min_length=1, max_length=2000)
    scope: str = "global"
    agent_id: str | None = None
    source: str = "user_confirmed"

    @model_validator(mode="after")
    def validate_scope(self):
        if self.scope not in {"global", "agent"}:
            raise ValueError("记忆范围必须是 global 或 agent")
        if (self.scope == "agent") != (self.agent_id is not None):
            raise ValueError("Agent 专属记忆必须指定 Agent， 全局记忆不能指定 Agent")
        if self.source != "user_confirmed":
            raise ValueError("记忆必须由用户明确确认后才能保存")
        return self


class MemoryPatch(BaseModel):
    content: str = Field(min_length=1, max_length=2000)


@router.post("", status_code=201)
def create_memory(payload: MemoryCreate, request: Request):
    memory_id = uuid4().hex
    with database_connection(request.app.state.database_path) as connection:
        if payload.agent_id:
            exists = connection.execute("SELECT 1 FROM agents WHERE id = ?", (payload.agent_id,)).fetchone()
            if exists is None:
                raise HTTPException(status_code=404, detail="Agent 不存在")
        connection.execute(
            "INSERT INTO memories(id, content, scope, agent_id, source) VALUES (?, ?, ?, ?, ?)",
            (memory_id, payload.content.strip(), payload.scope, payload.agent_id, payload.source),
        )
        row = connection.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
    return dict(row)


def _memory_scope(scope: Literal["global", "agent"] | None, agent_id: str | None):
    if scope == "global" and agent_id is None:
        return "scope = 'global'", ()
    if scope == "agent" and agent_id:
        return "scope = 'agent' AND agent_id = ?", (agent_id,)
    if scope is not None:
        raise HTTPException(status_code=422, detail="全局记忆不能指定 Agent，专属记忆必须指定 Agent")
    return "", ()


@router.get("")
def list_memories(
    request: Request,
    agent_id: str | None = None,
    scope: Literal["global", "agent"] | None = None,
    q: str = Query(default="", max_length=200),
    limit: int = Query(default=200, ge=1, le=1000),
):
    clause, scope_values = _memory_scope(scope, agent_id)
    clauses = [clause] if clause else []
    values: list[object] = list(scope_values)
    if scope is None and agent_id:
        clauses.append("(scope = 'global' OR agent_id = ?)")
        values.append(agent_id)
    if q.strip():
        clauses.append("instr(lower(content), lower(?)) > 0")
        values.append(q.strip())
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with database_connection(request.app.state.database_path) as connection:
        rows = connection.execute(
            "SELECT * FROM memories" + where + " ORDER BY updated_at DESC LIMIT ?",
            (*values, limit),
        ).fetchall()
    return [dict(row) for row in rows]


@router.patch("/{memory_id}")
def update_memory(memory_id: str, payload: MemoryPatch, request: Request):
    with database_connection(request.app.state.database_path) as connection:
        cursor = connection.execute(
            "UPDATE memories SET content = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (payload.content.strip(), memory_id),
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="记忆不存在")
        row = connection.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
    return dict(row)


@router.delete("/{memory_id}", status_code=204)
def delete_memory(memory_id: str, request: Request):
    with database_connection(request.app.state.database_path) as connection:
        cursor = connection.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
    if not cursor.rowcount:
        raise HTTPException(status_code=404, detail="记忆不存在")
    return Response(status_code=204)


@router.delete("", status_code=204)
def clear_memories(request: Request, scope: Literal["global", "agent"] | None = None, agent_id: str | None = None):
    if scope is None and agent_id is not None:
        raise HTTPException(status_code=422, detail="清空专属记忆时必须指定范围")
    clause, values = _memory_scope(scope, agent_id)
    where = " WHERE " + clause if clause else ""
    with database_connection(request.app.state.database_path) as connection:
        connection.execute("DELETE FROM memories" + where, values)
    return Response(status_code=204)
