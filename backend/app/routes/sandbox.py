from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from backend.app.db import database_connection
from backend.app.runtime.docker_sandbox import DockerSandboxSettings, DockerSandboxUnavailable

router = APIRouter(prefix="/api/sandbox", tags=["sandbox"])
SETTING_KEY = "docker_sandbox"
DEFAULTS = {
    "enabled": False,
    "image": "python:3.11-slim",
    "timeout_seconds": 30,
    "memory_mb": 512,
    "cpu_count": 1,
    "network_enabled": False,
}
PUBLIC_FIELDS = tuple(DEFAULTS)


class SandboxConfigPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    image: str = Field(default="python:3.11-slim", min_length=1, max_length=256)
    timeout_seconds: int = Field(default=30, ge=1, le=300)
    memory_mb: int = Field(default=512, ge=128, le=8192)
    cpu_count: int = Field(default=1, ge=1, le=8)
    network_enabled: bool = False


def _stored_settings(request: Request) -> dict:
    with database_connection(request.app.state.database_path) as connection:
        row = connection.execute(
            "SELECT value_json FROM app_settings WHERE key=?", (SETTING_KEY,)
        ).fetchone()
    if row is None:
        return dict(DEFAULTS)
    try:
        values = json.loads(row["value_json"])
    except (TypeError, ValueError, json.JSONDecodeError):
        return dict(DEFAULTS)
    if not isinstance(values, dict):
        return dict(DEFAULTS)
    # Filter both malformed values and any historical remote sandbox keys.
    return {**DEFAULTS, **{key: values[key] for key in PUBLIC_FIELDS if key in values}}


def _configured(request: Request, values: dict) -> bool:
    if not values.get("enabled"):
        return False
    try:
        return bool(request.app.state.sandbox_executor.is_configured(values))
    except TypeError:
        # Keep simple test/integration doubles that expose the no-argument API.
        return bool(request.app.state.sandbox_executor.is_configured())
    except Exception:
        return False


def _public_settings(request: Request) -> dict:
    values = _stored_settings(request)
    return {**values, "configured": _configured(request, values)}


@router.get("")
def get_sandbox_settings(request: Request):
    return _public_settings(request)


@router.put("")
def put_sandbox_settings(payload: SandboxConfigPayload, request: Request):
    values = payload.model_dump()
    # Validate the image and all resource fields even when the entry is being
    # disabled.  The executor's settings parser intentionally rejects disabled
    # entries for execution, so validate a temporary enabled candidate here.
    try:
        DockerSandboxSettings.from_value({**values, "enabled": True})
    except DockerSandboxUnavailable as error:
        raise HTTPException(status_code=422, detail=str(error)) from None

    if values["enabled"] and not _configured(request, values):
        raise HTTPException(
            status_code=409,
            detail=(
                "本机 Docker 沙箱不可用；请安装并启动 Docker Desktop，"
                "并确认指定镜像已存在。应用不会自动拉取镜像。"
            ),
        )
    with database_connection(request.app.state.database_path) as connection:
        connection.execute(
            "INSERT INTO app_settings(key,value_json) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=CURRENT_TIMESTAMP",
            (SETTING_KEY, json.dumps(values, ensure_ascii=False)),
        )
    return _public_settings(request)


@router.post("/test")
def test_sandbox_connection(request: Request):
    values = _stored_settings(request)
    if not values.get("enabled"):
        raise HTTPException(status_code=409, detail="本地 Docker 沙箱尚未启用")
    if not _configured(request, values):
        raise HTTPException(
            status_code=409,
            detail="本机 Docker 或指定镜像不可用；请启动 Docker 并确认镜像已存在",
        )
    return {"ok": True, "message": "Docker CLI、Engine 和本地镜像检查通过；未运行容器"}
