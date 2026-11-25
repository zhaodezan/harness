import datetime as dt

import pytest

import backend.app.scheduler as scheduler_module
from backend.app.db import database_connection, initialize_database
from backend.app.scheduler import CronSchedule, Scheduler, SchedulerError, script_hash, validate_script


def test_cron_calculates_next_run_in_requested_timezone():
    next_run = CronSchedule("0 9 * * *", "Asia/Shanghai").next_after(
        dt.datetime(2026, 1, 1, 0, 1, tzinfo=dt.timezone.utc)
    )
    assert next_run.date() == dt.date(2026, 1, 1)
    assert (next_run.hour, next_run.minute) == (9, 0)


def test_cron_rejects_invalid_expression_and_timezone():
    with pytest.raises(SchedulerError, match="Cron"):
        CronSchedule("61 * * * *", "UTC")
    with pytest.raises(SchedulerError, match="时区"):
        CronSchedule("* * * * *", "Mars/Base")


def test_cron_treats_naive_instants_as_utc():
    next_run = CronSchedule("0 9 * * *", "Asia/Shanghai").next_after(dt.datetime(2026, 1, 1, 0, 1))
    assert next_run.date() == dt.date(2026, 1, 1)
    assert next_run.hour == 9


def test_script_validation_rejects_empty_oversized_and_invalid_python():
    with pytest.raises(SchedulerError, match="不能为空"):
        validate_script("  \n")
    with pytest.raises(SchedulerError, match="100,000"):
        validate_script("#" + "x" * 100_000)
    with pytest.raises(SchedulerError, match="语法无效"):
        validate_script("def broken(:")
    validate_script("print('valid')")


def test_execute_script_returns_stdout_and_success_status(tmp_path):
    result = Scheduler(tmp_path / "db.sqlite3", tmp_path / "work").execute_script("print('hello from task')")
    assert result.status == "succeeded"
    assert result.exit_code == 0
    assert result.stdout.strip() == "hello from task"
    assert result.stderr == ""
    assert not result.truncated


def test_execute_script_reports_failure_exit_and_stderr(tmp_path):
    result = Scheduler(tmp_path / "db.sqlite3", tmp_path / "work").execute_script(
        "import sys; print('failure', file=sys.stderr); sys.exit(7)"
    )
    assert result.status == "failed"
    assert result.exit_code == 7
    assert "failure" in result.stderr


def test_execute_script_terminates_timed_out_process(tmp_path):
    result = Scheduler(tmp_path / "db.sqlite3", tmp_path / "work").execute_script(
        "import time; time.sleep(10)", timeout_seconds=1
    )
    assert result.status == "timeout"
    assert result.exit_code is not None
    assert result.duration_ms < 5000


def test_execute_script_caps_each_output_stream_and_marks_truncation(tmp_path):
    scheduler = Scheduler(tmp_path / "db.sqlite3", tmp_path / "work", output_limit_bytes=128)
    result = scheduler.execute_script("import sys; print('o' * 200); print('e' * 200, file=sys.stderr)")
    assert result.truncated
    assert len(result.stdout.encode("utf-8")) <= 128
    assert len(result.stderr.encode("utf-8")) <= 128 + len("\n[输出已截断]".encode("utf-8"))
    assert "[输出已截断]" in result.stderr


def test_execute_script_rejects_invalid_timeout(tmp_path):
    with pytest.raises(SchedulerError, match="超时"):
        Scheduler(tmp_path / "db.sqlite3", tmp_path / "work").execute_script("print('x')", timeout_seconds=0)


def test_scheduler_does_not_catch_up_missed_runs_and_moves_next_run_forward(tmp_path):
    database = tmp_path / "scheduled.sqlite3"
    initialize_database(database)
    script = "print('confirmed')"
    with database_connection(database) as connection:
        connection.execute(
            "INSERT INTO scheduled_tasks(id,name,script,cron_expression,timezone,enabled,confirmed_hash,next_run_at) "
            "VALUES('task-1','Job',?,'* * * * *','UTC',1,?,?)",
            (script, script_hash(script), "2020-01-01T00:00:00+00:00"),
        )
    Scheduler(database, tmp_path / "work")._recompute_next_runs()
    assert Scheduler(database, tmp_path / "work")._get_due_tasks() == []
    with database_connection(database) as connection:
        next_run = dt.datetime.fromisoformat(connection.execute(
            "SELECT next_run_at FROM scheduled_tasks WHERE id='task-1'"
        ).fetchone()[0])
    assert next_run > dt.datetime.now(dt.timezone.utc)


def test_scheduler_never_claims_same_task_while_previous_run_is_running(tmp_path):
    database = tmp_path / "scheduled.sqlite3"
    initialize_database(database)
    script = "print('confirmed')"
    with database_connection(database) as connection:
        connection.execute(
            "INSERT INTO scheduled_tasks(id,name,script,cron_expression,timezone,enabled,confirmed_hash) "
            "VALUES('task-1','Job',?,'* * * * *','UTC',1,?)",
            (script, script_hash(script)),
        )
    scheduler = Scheduler(database, tmp_path / "work")
    assert scheduler._claim_run("task-1", "manual") is not None
    assert scheduler._claim_run("task-1", "manual") is None


def test_scheduler_disables_enabled_script_whose_confirmed_hash_does_not_match(tmp_path):
    database = tmp_path / "scheduled.sqlite3"
    initialize_database(database)
    script = "print('new unconfirmed code')"
    with database_connection(database) as connection:
        connection.execute(
            "INSERT INTO scheduled_tasks(id,name,script,cron_expression,timezone,enabled,confirmed_hash,next_run_at) "
            "VALUES('task-1','Job',?,'* * * * *','UTC',1,'old-hash','2020-01-01T00:00:00+00:00')",
            (script,),
        )
    Scheduler(database, tmp_path / "work")._recompute_next_runs()
    with database_connection(database) as connection:
        task = connection.execute("SELECT enabled,confirmed_hash,next_run_at FROM scheduled_tasks WHERE id='task-1'").fetchone()
    assert task["enabled"] == 0
    assert task["confirmed_hash"] is None


def test_windows_termination_attempts_to_kill_process_tree(monkeypatch):
    calls = []

    class Process:
        pid = 4321
        returncode = None

        def poll(self):
            return self.returncode

        def kill(self):
            self.returncode = -9

        def wait(self, timeout=None):
            return self.returncode

    process = Process()

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))

    monkeypatch.setattr(scheduler_module.subprocess, "run", fake_run)
    monkeypatch.setattr(scheduler_module.os, "name", "nt")
    Scheduler._terminate_process(process)
    assert calls[0][0] == ["taskkill", "/F", "/T", "/PID", "4321"]
