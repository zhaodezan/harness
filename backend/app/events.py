from datetime import datetime, timedelta, timezone
from uuid import uuid4

from backend.app.db import database_connection


def record_event(
    database_path,
    *,
    run_id: str,
    event_type: str,
    status: str,
    subsystem: str = "runtime",
    level: str = "info",
    session_id: str | None = None,
    duration_ms: float | None = None,
    summary: str = "",
    occurred_at: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    estimated_cost: float | None = None,
    cost_currency: str | None = None,
) -> int:
    now = occurred_at or datetime.now(timezone.utc).isoformat()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    with database_connection(database_path) as connection:
        cursor = connection.execute(
            "INSERT INTO run_events(occurred_at, run_id, session_id, level, subsystem, "
            "event_type, status, duration_ms, summary, input_tokens, output_tokens, estimated_cost, cost_currency) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (now, run_id, session_id, level, subsystem, event_type, status, duration_ms,
             summary[:500], input_tokens, output_tokens, estimated_cost, cost_currency),
        )
        connection.execute("DELETE FROM run_events WHERE occurred_at < ?", (cutoff,))
        event_id = cursor.lastrowid
        assert event_id is not None
        return event_id


def new_run_id() -> str:
    return uuid4().hex
