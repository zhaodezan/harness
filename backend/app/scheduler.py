import ast
import asyncio
import datetime as dt
import hashlib
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter

from backend.app.db import database_connection

MAX_SCRIPT_CHARS = 100_000
DEFAULT_TIMEOUT_SECONDS = 300
MAX_TIMEOUT_SECONDS = 3600
DEFAULT_OUTPUT_LIMIT_BYTES = 64 * 1024


class SchedulerError(ValueError):
    """Safe validation/execution error for scheduled scripts."""


def script_hash(script: str) -> str:
    return hashlib.sha256(script.encode("utf-8")).hexdigest()


class CronSchedule:
    def __init__(self, expression: str, timezone_name: str):
        try:
            self.timezone = ZoneInfo(timezone_name)
        except (ZoneInfoNotFoundError, TypeError, ValueError) as error:
            raise SchedulerError("时区必须是有效的 IANA 时区名称") from error
        if not isinstance(expression, str) or not croniter.is_valid(expression):
            raise SchedulerError("Cron 必须是有效的五段式表达式")
        self.expression = expression

    def next_after(self, instant: dt.datetime) -> dt.datetime:
        if instant.tzinfo is None:
            instant = instant.replace(tzinfo=dt.timezone.utc)
        local = instant.astimezone(self.timezone)
        try:
            return croniter(self.expression, local).get_next(dt.datetime).astimezone(self.timezone)
        except (ValueError, OverflowError) as error:
            raise SchedulerError("无法计算此 Cron 表达式的下次运行时间") from error


def validate_script(script: str) -> None:
    if not isinstance(script, str) or not script.strip():
        raise SchedulerError("Python 脚本不能为空")
    if len(script) > MAX_SCRIPT_CHARS:
        raise SchedulerError("Python 脚本不能超过 100,000 字符")
    try:
        ast.parse(script, filename="scheduled_task.py", mode="exec")
        compile(script, "scheduled_task.py", "exec")
    except (SyntaxError, ValueError) as error:
        line = getattr(error, "lineno", None)
        suffix = f"（第 {line} 行）" if line else ""
        raise SchedulerError(f"Python 脚本语法无效{suffix}") from error


@dataclass(frozen=True)
class ExecutionResult:
    status: str
    exit_code: int | None
    stdout: str
    stderr: str
    truncated: bool
    duration_ms: float


class Scheduler:
    def __init__(
        self,
        database_path,
        work_dir,
        *,
        default_timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        output_limit_bytes: int = DEFAULT_OUTPUT_LIMIT_BYTES,
        poll_interval_seconds: float = 10,
        now_provider=None,
    ):
        self.database_path = Path(database_path).resolve()
        self.work_dir = Path(work_dir).resolve()
        self.default_timeout_seconds = default_timeout_seconds
        self.output_limit_bytes = output_limit_bytes
        self.poll_interval_seconds = poll_interval_seconds
        self.now_provider = now_provider or (lambda: dt.datetime.now(dt.timezone.utc))
        self._stop_event: asyncio.Event | None = None
        self._loop_task: asyncio.Task | None = None
        self._active_tasks: dict[str, asyncio.Task] = {}
        self._processes: dict[str, subprocess.Popen] = {}
        self._process_lock = threading.Lock()

    @staticmethod
    def validate_script(script: str) -> None:
        validate_script(script)

    def execute_script(self, script: str, *, timeout_seconds: int | None = None, task_id: str | None = None) -> ExecutionResult:
        validate_script(script)
        timeout = timeout_seconds if timeout_seconds is not None else self.default_timeout_seconds
        if not isinstance(timeout, int) or isinstance(timeout, bool) or not 1 <= timeout <= MAX_TIMEOUT_SECONDS:
            raise SchedulerError("任务超时必须在 1 到 3,600 秒之间")
        self.work_dir.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="scheduled-task-", dir=self.work_dir) as temporary:
            directory = Path(temporary)
            script_path = directory / "task.py"
            script_path.write_text(script, encoding="utf-8", newline="\n")
            environment = {"PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"}
            for key in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LANG", "LC_ALL"):
                if key in os.environ:
                    environment[key] = os.environ[key]
            kwargs = {
                "args": [sys.executable, "-I", str(script_path)],
                "cwd": str(directory), "env": environment, "shell": False,
                "stdin": subprocess.DEVNULL, "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE, "bufsize": 0,
            }
            if os.name == "nt":
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            else:
                kwargs["start_new_session"] = True
            try:
                process = subprocess.Popen(**kwargs)
            except OSError as error:
                raise SchedulerError("无法启动本机 Python 子进程") from error
            if task_id:
                with self._process_lock:
                    self._processes[task_id] = process
            captured = {"stdout": bytearray(), "stderr": bytearray()}
            totals = {"stdout": 0, "stderr": 0}
            reader_errors: list[BaseException] = []

            def read_stream(name: str, stream):
                try:
                    while True:
                        chunk = stream.read(8192)
                        if not chunk:
                            break
                        totals[name] += len(chunk)
                        remaining = max(0, self.output_limit_bytes - len(captured[name]))
                        if remaining:
                            captured[name].extend(chunk[:remaining])
                except BaseException as error:
                    reader_errors.append(error)
                finally:
                    stream.close()

            readers = [
                threading.Thread(target=read_stream, args=("stdout", process.stdout), daemon=True),
                threading.Thread(target=read_stream, args=("stderr", process.stderr), daemon=True),
            ]
            for reader in readers:
                reader.start()
            timed_out = False
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                self._terminate_process(process)
            finally:
                for reader in readers:
                    reader.join(timeout=5)
                if task_id:
                    with self._process_lock:
                        self._processes.pop(task_id, None)
            if any(reader.is_alive() for reader in readers):
                self._terminate_process(process)
                raise SchedulerError("任务输出流未能关闭")
            duration_ms = (time.monotonic() - started) * 1000
            stdout = captured["stdout"].decode("utf-8", errors="replace")
            stderr = captured["stderr"].decode("utf-8", errors="replace")
            truncated = any(totals[name] > len(captured[name]) for name in captured)
            if truncated:
                stderr += "\n[输出已截断]"
            status = "timeout" if timed_out else "succeeded" if process.returncode == 0 else "failed"
            return ExecutionResult(status, process.returncode, stdout, stderr, truncated, duration_ms)

    @staticmethod
    def _terminate_process(process: subprocess.Popen) -> None:
        if process.poll() is not None:
            return
        try:
            if os.name != "nt":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                try:
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                        check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        timeout=5,
                    )
                except (OSError, subprocess.TimeoutExpired):
                    pass
                if process.poll() is None:
                    process.kill()
        except (OSError, ProcessLookupError):
            try:
                process.kill()
            except OSError:
                pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass

    async def start(self) -> None:
        if self._loop_task and not self._loop_task.done():
            return
        self._stop_event = asyncio.Event()
        self._mark_interrupted()
        self._recompute_next_runs()
        self._loop_task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        if self._stop_event:
            self._stop_event.set()
        with self._process_lock:
            processes = list(self._processes.values())
        for process in processes:
            await asyncio.to_thread(self._terminate_process, process)
        if self._loop_task:
            try:
                await asyncio.wait_for(self._loop_task, timeout=10)
            except asyncio.TimeoutError:
                self._loop_task.cancel()
                try:
                    await self._loop_task
                except asyncio.CancelledError:
                    pass
        active = list(self._active_tasks.values())
        if active:
            await asyncio.gather(*active, return_exceptions=True)

    def _mark_interrupted(self) -> None:
        with database_connection(self.database_path) as connection:
            connection.execute(
                "UPDATE scheduled_task_runs SET status='interrupted',finished_at=CURRENT_TIMESTAMP,summary='后端退出时任务仍在运行' WHERE status='running'"
            )

    def _recompute_next_runs(self) -> None:
        now = self.now_provider()
        with database_connection(self.database_path) as connection:
            rows = connection.execute("SELECT id,script,confirmed_hash,cron_expression,timezone FROM scheduled_tasks WHERE enabled=1").fetchall()
            for row in rows:
                if not row["confirmed_hash"] or row["confirmed_hash"] != script_hash(row["script"]):
                    connection.execute(
                        "UPDATE scheduled_tasks SET enabled=0,confirmed_hash=NULL,confirmed_at=NULL,next_run_at=NULL WHERE id=?",
                        (row["id"],),
                    )
                    continue
                next_run = CronSchedule(row["cron_expression"], row["timezone"]).next_after(now)
                connection.execute(
                    "UPDATE scheduled_tasks SET next_run_at=? WHERE id=?",
                    (next_run.isoformat(), row["id"]),
                )

    async def _run_loop(self) -> None:
        assert self._stop_event is not None
        while not self._stop_event.is_set():
            due = await asyncio.to_thread(self._get_due_tasks)
            for task in due:
                task_id = task["id"]
                if task_id in self._active_tasks and not self._active_tasks[task_id].done():
                    await asyncio.to_thread(self._record_skipped, task, "同一任务上一轮仍在运行")
                    continue
                run = await asyncio.to_thread(self._claim_run, task_id, "scheduled", task["next_run_at"])
                if run is not None:
                    child = asyncio.create_task(self._execute_claimed(run))
                    self._active_tasks[task_id] = child
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.poll_interval_seconds)
            except asyncio.TimeoutError:
                pass

    def _get_due_tasks(self):
        now = self.now_provider().astimezone(dt.timezone.utc)
        with database_connection(self.database_path) as connection:
            rows = connection.execute(
                "SELECT id,next_run_at FROM scheduled_tasks WHERE enabled=1 AND next_run_at IS NOT NULL"
            ).fetchall()
        return [dict(row) for row in rows if dt.datetime.fromisoformat(row["next_run_at"]).astimezone(dt.timezone.utc) <= now]

    def _record_skipped(self, task: dict, summary: str) -> None:
        run_id = __import__("uuid").uuid4().hex
        with database_connection(self.database_path) as connection:
            connection.execute(
                "INSERT INTO scheduled_task_runs(id,task_id,trigger,scheduled_at,status,summary) VALUES(?,?,?,?,?,?)",
                (run_id, task["id"], "scheduled", task["next_run_at"], "skipped", summary),
            )
            row = connection.execute(
                "SELECT cron_expression,timezone FROM scheduled_tasks WHERE id=?", (task["id"],)
            ).fetchone()
            if row:
                next_run = CronSchedule(row["cron_expression"], row["timezone"]).next_after(self.now_provider())
                connection.execute("UPDATE scheduled_tasks SET next_run_at=? WHERE id=?", (next_run.isoformat(), task["id"]))

    def _claim_run(self, task_id: str, trigger: str, scheduled_at: str | None = None):
        run_id = __import__("uuid").uuid4().hex
        with database_connection(self.database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            task = connection.execute("SELECT * FROM scheduled_tasks WHERE id=?", (task_id,)).fetchone()
            if task is None or task["confirmed_hash"] != script_hash(task["script"]):
                return None
            if trigger == "scheduled" and not task["enabled"]:
                return None
            running = connection.execute(
                "SELECT 1 FROM scheduled_task_runs WHERE task_id=? AND status='running' LIMIT 1", (task_id,)
            ).fetchone()
            if running:
                return None
            connection.execute(
                "INSERT INTO scheduled_task_runs(id,task_id,trigger,scheduled_at,status) VALUES(?,?,?,?, 'running')",
                (run_id, task_id, trigger, scheduled_at),
            )
            if trigger == "scheduled":
                next_run = CronSchedule(task["cron_expression"], task["timezone"]).next_after(self.now_provider())
                connection.execute(
                    "UPDATE scheduled_tasks SET next_run_at=?,last_run_at=CURRENT_TIMESTAMP WHERE id=?",
                    (next_run.isoformat(), task_id),
                )
            return {"run_id": run_id, "task_id": task_id, "script": task["script"], "timeout_seconds": task["timeout_seconds"]}

    async def _execute_claimed(self, run: dict) -> None:
        try:
            result = await asyncio.to_thread(
                self.execute_script, run["script"], timeout_seconds=run["timeout_seconds"], task_id=run["task_id"]
            )
            with database_connection(self.database_path) as connection:
                connection.execute(
                    "UPDATE scheduled_task_runs SET finished_at=CURRENT_TIMESTAMP,duration_ms=?,status=?,exit_code=?,stdout=?,stderr=?,truncated=? WHERE id=?",
                    (result.duration_ms, result.status, result.exit_code, result.stdout, result.stderr,
                     int(result.truncated), run["run_id"]),
                )
        except Exception:
            with database_connection(self.database_path) as connection:
                connection.execute(
                    "UPDATE scheduled_task_runs SET finished_at=CURRENT_TIMESTAMP,status='failed',summary='任务启动失败' WHERE id=?",
                    (run["run_id"],),
                )

    async def run_now(self, task_id: str, confirmed_hash: str) -> str:
        with database_connection(self.database_path) as connection:
            task = connection.execute("SELECT script,enabled FROM scheduled_tasks WHERE id=?", (task_id,)).fetchone()
        if task is None:
            raise SchedulerError("定时任务不存在")
        current_hash = script_hash(task["script"])
        if confirmed_hash != current_hash:
            raise SchedulerError("当前脚本版本未确认，请重新确认后运行")
        with database_connection(self.database_path) as connection:
            connection.execute(
                "UPDATE scheduled_tasks SET confirmed_hash=?,confirmed_at=CURRENT_TIMESTAMP WHERE id=?",
                (current_hash, task_id),
            )
        if task_id in self._active_tasks and not self._active_tasks[task_id].done():
            raise SchedulerError("此任务已在运行")
        run = await asyncio.to_thread(self._claim_run, task_id, "manual")
        if run is None:
            raise SchedulerError("此任务已在运行或脚本未确认")
        child = asyncio.create_task(self._execute_claimed(run))
        self._active_tasks[task_id] = child
        return run["run_id"]
