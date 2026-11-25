from datetime import datetime, timedelta, timezone

from backend.app.db import database_connection


def prune_expired_events(database_path) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    with database_connection(database_path) as connection:
        cursor = connection.execute("DELETE FROM run_events WHERE occurred_at < ?", (cutoff,))
        return cursor.rowcount
