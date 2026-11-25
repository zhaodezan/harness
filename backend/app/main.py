from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator
from urllib.parse import urlsplit

import asyncio
import httpx
import json
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError

from backend.app.tools import ToolRegistry
from backend.app.database_manager import DatabaseManager
from backend.app.scheduler import Scheduler
from backend.app.routes.scheduled_tasks import router as scheduled_tasks_router
from backend.app.routes.databases import router as databases_router
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from backend.app.db import database_connection, initialize_database
from backend.app.mcp import MCPManager
from backend.app.maintenance import prune_expired_events
from backend.app.recovery import mark_interrupted_runs
from backend.app.runtime.a2a import A2AService
from backend.app.runtime.agent_runner import AgentTaskRunner
from backend.app.runtime.cube_sandbox import SandboxExecutor
from backend.app.runtime.windows_tool import WindowsToolExecutor
from backend.app.routes.agents import router as agents_router
from backend.app.routes.models import router as models_router
from backend.app.routes.approvals import router as approvals_router
from backend.app.routes.mcp import router as mcp_router
from backend.app.routes.skills import router as skills_router
from backend.app.routes.tools import router as tools_router
from backend.app.routes.debug import router as debug_router
from backend.app.plugin_store import PluginStore
from backend.app.plugin_migrations import migrate_legacy_plugins
from backend.app.model_migrations import migrate_legacy_models
from backend.app.tool_plugins import LocalToolManager
from backend.app.routes.setup import router as setup_router
from backend.app.routes.data_transfer import router as data_transfer_router
from backend.app.routes.health import router as health_router
from backend.app.routes.logs import router as logs_router
from backend.app.routes.sandbox import router as sandbox_router
from backend.app.routes.knowledge_bases import router as knowledge_bases_router
from backend.app.routes.memories import router as memories_router
from backend.app.routes.sessions import router as sessions_router
from backend.app.secrets import SecretStore, SystemSecretStore
from backend.app.settings import get_data_dir


def create_app(
    database_path: str | Path | None = None,
    *,
    http_transport: httpx.AsyncBaseTransport | None = None,
    secret_store: SecretStore | None = None,
    data_dir: str | Path | None = None,
) -> FastAPI:
    resolved_data_dir = Path(data_dir).expanduser() if data_dir is not None else get_data_dir()
    resolved_database_path = (
        Path(database_path)
        if database_path is not None
        else resolved_data_dir / "harness.sqlite3"
    )
    resolved_secret_store = secret_store or SystemSecretStore(model_fallback_dir=resolved_data_dir / 'model-credentials')

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        initialize_database(resolved_database_path)
        application.state.model_migration_warnings = migrate_legacy_models(
            resolved_database_path, application.state.secret_store
        )
        application.state.plugin_migration_warnings = migrate_legacy_plugins(
            resolved_database_path, application.state.plugin_store
        )
        application.state.database_manager.register_tools(application.state.tool_registry)
        await application.state.scheduler.start()
        mark_interrupted_runs(resolved_database_path)
        application.state.http_client = httpx.AsyncClient(
            transport=http_transport, timeout=httpx.Timeout(120.0)
        )
        application.state.a2a_service = A2AService(resolved_database_path)
        application.state.agent_task_runner = AgentTaskRunner(application, application.state.a2a_service)
        application.state.a2a_service.runner = application.state.agent_task_runner
        application.state.mcp_manager = MCPManager(application.state.http_client)
        application.state.local_tool_manager.load_installed()
        prune_expired_events(resolved_database_path)
        async def maintain_data():
            while True:
                await asyncio.sleep(3600)
                prune_expired_events(resolved_database_path)
        maintenance_task = asyncio.create_task(maintain_data())
        try:
            yield
        finally:
            await application.state.scheduler.stop()
            maintenance_task.cancel()
            try:
                await maintenance_task
            except asyncio.CancelledError:
                pass
            await application.state.mcp_manager.close()
            await application.state.http_client.aclose()

    application = FastAPI(title="Local Agent Harness", lifespan=lifespan)

    @application.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError):
        if request.url.path == '/api/models' or request.url.path.startswith('/api/models/'):
            # FastAPI's default validation response includes the rejected input.
            details = [{key: item[key] for key in ('loc', 'msg', 'type')} for item in error.errors()]
            return JSONResponse(status_code=422, content={'detail': details})
        return await request_validation_exception_handler(request, error)
    application.state.database_path = resolved_database_path
    application.state.data_dir = resolved_data_dir
    application.state.secret_store = resolved_secret_store
    application.state.plugin_store = PluginStore(resolved_data_dir)
    application.state.database_manager = DatabaseManager(resolved_database_path, resolved_secret_store)
    application.state.scheduler = Scheduler(
        resolved_database_path, resolved_data_dir / "scheduled-task-work"
    )
    application.state.tool_registry = ToolRegistry()

    def sandbox_settings():
        with database_connection(application.state.database_path) as connection:
            row = connection.execute(
                "SELECT value_json FROM app_settings WHERE key='cubesandbox'"
            ).fetchone()
        if row is None:
            return None
        values = json.loads(row["value_json"])
        values["api_key"] = application.state.secret_store.get("cubesandbox:api_key") or ""
        return values

    application.state.sandbox_executor = SandboxExecutor(sandbox_settings)
    application.state.local_tool_manager = LocalToolManager(
        application.state.plugin_store.tools_dir, application.state.tool_registry,
        sandbox_executor=application.state.sandbox_executor.execute_python_async,
        windows_executor=WindowsToolExecutor(),
    )
    application.state.pending_approvals = {}
    application.state.active_sessions = set()

    @application.middleware("http")
    async def protect_local_origin(request: Request, call_next):
        if request.url.path == "/api/data/import":
            content_length = request.headers.get("content-length")
            if content_length:
                try:
                    if int(content_length) > 10 * 1024 * 1024:
                        return PlainTextResponse("导入文件不能大于 10 MB。", status_code=413)
                except ValueError:
                    return PlainTextResponse("请求长度无效。", status_code=400)
        host = request.url.hostname or ""
        if host not in {"localhost", "127.0.0.1", "::1", "testserver"}:
            return PlainTextResponse("仅允许通过本机地址访问。", status_code=403)
        origin = request.headers.get("origin")
        if origin:
            origin_parts = urlsplit(origin)
            request_port = request.url.port or (443 if request.url.scheme == "https" else 80)
            origin_port = origin_parts.port or (443 if origin_parts.scheme == "https" else 80)
            if (
                origin_parts.hostname != host
                or origin_parts.scheme != request.url.scheme
                or origin_port != request_port
            ):
                return PlainTextResponse("已阻止来自其他网站的请求。", status_code=403)
        return await call_next(request)

    application.include_router(health_router, prefix="/api")
    application.include_router(data_transfer_router)
    application.include_router(agents_router, prefix="/api")
    application.include_router(models_router)
    application.include_router(approvals_router)
    application.include_router(mcp_router)
    application.include_router(skills_router)
    application.include_router(tools_router)
    application.include_router(debug_router)
    application.include_router(setup_router)
    application.include_router(sessions_router)
    application.include_router(logs_router)
    application.include_router(sandbox_router)
    application.include_router(knowledge_bases_router)
    application.include_router(databases_router)
    application.include_router(scheduled_tasks_router)
    application.include_router(memories_router)

    frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    assets_dir = frontend_dist / "assets"
    if assets_dir.is_dir():
        application.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @application.get("/", include_in_schema=False)
    def frontend_index():
        index_path = frontend_dist / "index.html"
        if index_path.is_file():
            return HTMLResponse(index_path.read_text(encoding="utf-8"))
        return HTMLResponse(
            "<html><body><h1>Agent Harness</h1>"
            "<p>前端尚未构建。请运行 npm --prefix frontend run build。</p>"
            "</body></html>",
            status_code=503,
        )

    @application.get("/{frontend_path:path}", include_in_schema=False)
    def frontend_route(frontend_path: str):
        segments = frontend_path.strip("/").split("/")
        is_agent_page = (
            len(segments) == 3
            and segments[0] == "agents"
            and bool(segments[1])
            and segments[2] in {"config", "chat"}
        )
        if frontend_path in {"chat", "agents", "agents/new", "plugins", "knowledge-bases", "databases", "scheduled-tasks"} or is_agent_page:
            return frontend_index()
        raise HTTPException(status_code=404, detail="Not Found")

    return application


app = create_app()
