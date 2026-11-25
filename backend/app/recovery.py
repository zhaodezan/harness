from backend.app.db import database_connection


def mark_interrupted_runs(database_path) -> int:
    with database_connection(database_path) as connection:
        cursor = connection.execute(
            "UPDATE run_events AS start_event "
            "SET status = 'interrupted', level = 'warning', "
            "summary = '应用上次未正常结束，本次启动时已标记为中断。' "
            "WHERE event_type = 'run_started' AND status = 'running' "
            "AND NOT EXISTS (SELECT 1 FROM run_events AS terminal "
            "WHERE terminal.run_id = start_event.run_id "
            "AND terminal.event_type IN ('run_completed','model_error','run_cancelled'))"
        )
        return cursor.rowcount
