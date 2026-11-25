import json
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field, field_validator

from backend.app.db import database_connection
from backend.app.scheduler import (
    MAX_SCRIPT_CHARS,
    MAX_TIMEOUT_SECONDS,
    CronSchedule,
    SchedulerError,
    script_hash,
    validate_script,
)

router = APIRouter(prefix="/api/scheduled-tasks", tags=["scheduled tasks"])


class ScheduledTaskCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    script: str = Field(min_length=1, max_length=MAX_SCRIPT_CHARS)
    cron_expression: str = Field(min_length=1, max_length=100)
    timezone: str = Field(min_length=1, max_length=100)
    timeout_seconds: int = Field(default=300, ge=1, le=MAX_TIMEOUT_SECONDS)

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("任务名称不能为空")
        return value


class ScheduledTaskPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    script: str | None = Field(default=None, min_length=1, max_length=MAX_SCRIPT_CHARS)
    cron_expression: str | None = Field(default=None, min_length=1, max_length=100)
    timezone: str | None = Field(default=None, min_length=1, max_length=100)
    timeout_seconds: int | None = Field(default=None, ge=1, le=MAX_TIMEOUT_SECONDS)

    @field_validator("name")
    @classmethod
    def patch_name_not_blank(cls, value):
        if value is None:
            return value
        value = value.strip()
        if not value:
            raise ValueError("任务名称不能为空")
        return value


class Confirmation(BaseModel):
    confirmed: bool
    script_hash: str = Field(min_length=64, max_length=64)


class ValidateRequest(BaseModel):
    script: str = Field(min_length=1, max_length=MAX_SCRIPT_CHARS)
    cron_expression: str = Field(min_length=1, max_length=100)
    timezone: str = Field(min_length=1, max_length=100)


def _task_view(row) -> dict:
    result = dict(row)
    result["enabled"] = bool(result["enabled"])
    result["script_hash"] = script_hash(result["script"])
    result["confirmed"] = result["confirmed_hash"] == result["script_hash"]
    return result


def _get_task(connection, task_id):
    return connection.execute("SELECT * FROM scheduled_tasks WHERE id=?", (task_id,)).fetchone()


@router.post("/validate")
def validate_task(payload: ValidateRequest):
    try:
        validate_script(payload.script)
        next_run = CronSchedule(payload.cron_expression, payload.timezone).next_after(
            __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
        )
    except SchedulerError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    return {"valid": True, "script_hash": script_hash(payload.script), "next_run_at": next_run.isoformat()}


@router.get("")
def list_tasks(request: Request, limit: int = Query(default=100, ge=1, le=500), offset: int = Query(default=0, ge=0)):
    with database_connection(request.app.state.database_path) as connection:
        rows = connection.execute(
            "SELECT id,name,cron_expression,timezone,timeout_seconds,enabled,confirmed_hash,confirmed_at,next_run_at,last_run_at,created_at,updated_at,script "
            "FROM scheduled_tasks ORDER BY updated_at DESC LIMIT ? OFFSET ?", (limit, offset),
        ).fetchall()
    # The script source is only returned from the detail endpoint.
    return [{key: value for key, value in _task_view(row).items() if key != "script"} for row in rows]


@router.post("", status_code=201)
def create_task(payload: ScheduledTaskCreate, request: Request):
    try:
        validate_script(payload.script)
        CronSchedule(payload.cron_expression, payload.timezone)
    except SchedulerError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    task_id = uuid4().hex
    with database_connection(request.app.state.database_path) as connection:
        connection.execute(
            "INSERT INTO scheduled_tasks(id,name,script,cron_expression,timezone,timeout_seconds) VALUES(?,?,?,?,?,?)",
            (task_id, payload.name, payload.script, payload.cron_expression, payload.timezone, payload.timeout_seconds),
        )
        return _task_view(_get_task(connection, task_id))


@router.get("/{task_id}")
def get_task(task_id: str, request: Request):
    with database_connection(request.app.state.database_path) as connection:
        row = _get_task(connection, task_id)
    if row is None:
        raise HTTPException(status_code=404, detail="定时任务不存在")
    return _task_view(row)


@router.patch("/{task_id}")
def update_task(task_id: str, payload: ScheduledTaskPatch, request: Request):
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=422, detail="请提供任务更新内容")
    with database_connection(request.app.state.database_path) as connection:
        current = _get_task(connection, task_id)
        if current is None:
            raise HTTPException(status_code=404, detail="定时任务不存在")
        merged = {key: current[key] for key in ("name", "script", "cron_expression", "timezone", "timeout_seconds")}
        merged.update(updates)
        try:
            validate_script(merged["script"])
            schedule = CronSchedule(merged["cron_expression"], merged["timezone"])
        except SchedulerError as error:
            raise HTTPException(status_code=422, detail=str(error)) from None
        script_changed = merged["script"] != current["script"]
        if script_changed:
            updates.update({"enabled": 0, "confirmed_hash": None, "confirmed_at": None, "next_run_at": None})
        elif current["enabled"] and ("cron_expression" in updates or "timezone" in updates):
            updates["next_run_at"] = schedule.next_after(request.app.state.scheduler.now_provider()).isoformat()
        updates["updated_at"] = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
        assignments = ",".join(f"{key}=?" for key in updates)
        connection.execute(
            f"UPDATE scheduled_tasks SET {assignments} WHERE id=?", [*updates.values(), task_id]
        )
        return _task_view(_get_task(connection, task_id))


@router.post("/{task_id}/enable")
def enable_task(task_id: str, payload: Confirmation, request: Request):
    if not payload.confirmed:
        raise HTTPException(status_code=409, detail="启用任务前必须确认脚本版本")
    with database_connection(request.app.state.database_path) as connection:
        row = _get_task(connection, task_id)
        if row is None:
            raise HTTPException(status_code=404, detail="定时任务不存在")
        current_hash = script_hash(row["script"])
        if payload.script_hash != current_hash:
            raise HTTPException(status_code=409, detail="脚本已变化，请重新查看并确认当前版本")
        try:
            next_run = CronSchedule(row["cron_expression"], row["timezone"]).next_after(
                request.app.state.scheduler.now_provider()
            )
        except SchedulerError as error:
            raise HTTPException(status_code=422, detail=str(error)) from None
        connection.execute(
            "UPDATE scheduled_tasks SET enabled=1,confirmed_hash=?,confirmed_at=CURRENT_TIMESTAMP,next_run_at=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (current_hash, next_run.isoformat(), task_id),
        )
        return _task_view(_get_task(connection, task_id))


@router.post("/{task_id}/disable")
def disable_task(task_id: str, request: Request):
    with database_connection(request.app.state.database_path) as connection:
        cursor = connection.execute(
            "UPDATE scheduled_tasks SET enabled=0,next_run_at=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (task_id,),
        )
        if not cursor.rowcount:
            raise HTTPException(status_code=404, detail="定时任务不存在")
        return _task_view(_get_task(connection, task_id))


@router.post("/{task_id}/run", status_code=202)
async def run_task(task_id: str, payload: Confirmation, request: Request):
    if not payload.confirmed:
        raise HTTPException(status_code=409, detail="手动运行前必须确认当前脚本版本")
    try:
        run_id = await request.app.state.scheduler.run_now(task_id, payload.script_hash)
    except SchedulerError as error:
        code = 404 if str(error) == "定时任务不存在" else 409
        raise HTTPException(status_code=code, detail=str(error)) from None
    return {"run_id": run_id, "status": "running"}


@router.get("/{task_id}/runs")
def list_runs(
    task_id: str, request: Request,
    limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0),
):
    with database_connection(request.app.state.database_path) as connection:
        exists = connection.execute("SELECT 1 FROM scheduled_tasks WHERE id=?", (task_id,)).fetchone()
        if exists is None:
            raise HTTPException(status_code=404, detail="定时任务不存在")
        rows = connection.execute(
            "SELECT id,task_id,trigger,scheduled_at,started_at,finished_at,duration_ms,status,exit_code,stdout,stderr,truncated,summary "
            "FROM scheduled_task_runs WHERE task_id=? ORDER BY started_at DESC LIMIT ? OFFSET ?",
            (task_id, limit, offset),
        ).fetchall()
        total = connection.execute("SELECT count(*) FROM scheduled_task_runs WHERE task_id=?", (task_id,)).fetchone()[0]
    return {"items": [dict(row) for row in rows], "total": total, "limit": limit, "offset": offset}


@router.delete("/{task_id}", status_code=204)
def delete_task(task_id: str, request: Request) -> Response:
    with database_connection(request.app.state.database_path) as connection:
        exists = connection.execute("SELECT 1 FROM scheduled_tasks WHERE id=?", (task_id,)).fetchone()
        if exists is None:
            raise HTTPException(status_code=404, detail="定时任务不存在")
        running = connection.execute(
            "SELECT 1 FROM scheduled_task_runs WHERE task_id=? AND status='running' LIMIT 1", (task_id,)
        ).fetchone()
        if running:
            raise HTTPException(status_code=409, detail="任务仍在运行，不能删除")
        connection.execute("DELETE FROM scheduled_tasks WHERE id=?", (task_id,))
    return Response(status_code=204)
