import csv
import io
import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Query, Request
from fastapi.responses import PlainTextResponse

from backend.app.db import database_connection

router = APIRouter(prefix="/api/logs", tags=["logs"])


def _prune(connection) -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    connection.execute("DELETE FROM run_events WHERE occurred_at < ?", (cutoff,))


def _query_events(request: Request, *, q: str, run_id: str | None, session_id: str | None,
                  level: str | None, subsystem: str | None, since: str | None, until: str | None, limit: int):
    clauses = []
    values: list[object] = []
    if q:
        clauses.append("instr(lower(summary || ' ' || event_type || ' ' || subsystem), lower(?)) > 0")
        values.append(q)
    for column, value in (("run_id", run_id), ("session_id", session_id), ("level", level), ("subsystem", subsystem)):
        if value:
            clauses.append(f"{column} = ?")
            values.append(value)
    if since:
        clauses.append("occurred_at >= ?")
        values.append(since)
    if until:
        clauses.append("occurred_at <= ?")
        values.append(until)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with database_connection(request.app.state.database_path) as connection:
        _prune(connection)
        rows = connection.execute(
            "SELECT id, occurred_at, run_id, session_id, level, subsystem, event_type, "
            "status, duration_ms, summary, input_tokens, output_tokens, estimated_cost, cost_currency FROM run_events" + where +
            " ORDER BY occurred_at DESC, id DESC LIMIT ?",
            (*values, limit),
        ).fetchall()
    return [dict(row) for row in rows]


@router.get("")
def list_logs(
    request: Request,
    q: str = Query(default="", max_length=200),
    run_id: str | None = None,
    session_id: str | None = None,
    level: str | None = None,
    subsystem: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = Query(default=200, ge=1, le=1000),
):
    return _query_events(request, q=q.strip(), run_id=run_id, session_id=session_id,
                         level=level, subsystem=subsystem, since=since, until=until, limit=limit)


@router.get("/export")
def export_logs(
    request: Request,
    format: str = Query(default="jsonl", pattern="^(jsonl|csv)$"),
    q: str = Query(default="", max_length=200),
    run_id: str | None = None,
    session_id: str | None = None,
    level: str | None = None,
    subsystem: str | None = None,
    since: str | None = None,
    until: str | None = None,
):
    events = _query_events(request, q=q.strip(), run_id=run_id,
                           session_id=session_id, level=level, subsystem=subsystem,
                           since=since, until=until, limit=10000)
    if format == "jsonl":
        content = "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events)
        return PlainTextResponse(content, media_type="application/x-ndjson")
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=list(events[0]) if events else [
        "id", "occurred_at", "run_id", "session_id", "level", "subsystem",
        "event_type", "status", "duration_ms", "summary", "input_tokens", "output_tokens", "estimated_cost", "cost_currency",
    ])
    writer.writeheader()
    writer.writerows(events)
    return PlainTextResponse(output.getvalue(), media_type="text/csv; charset=utf-8")
