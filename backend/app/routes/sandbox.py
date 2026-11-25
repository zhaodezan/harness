import json

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from backend.app.db import database_connection
from backend.app.runtime.cube_sandbox import SandboxSettings

router = APIRouter(prefix="/api/sandbox", tags=["sandbox"])
SETTING_KEY = "cubesandbox"
SECRET_KEY = "cubesandbox:api_key"


class SandboxConfigPayload(BaseModel):
    enabled: bool = False
    api_url: str = Field(min_length=1, max_length=2048)
    template_id: str = Field(min_length=1, max_length=128)
    timeout_seconds: int = Field(default=30, ge=1, le=300)
    allow_internet_access: bool = True
    api_key: str | None = Field(default=None, max_length=4096)


def _stored_settings(request: Request) -> dict:
    with database_connection(request.app.state.database_path) as connection:
        row = connection.execute("SELECT value_json FROM app_settings WHERE key=?", (SETTING_KEY,)).fetchone()
    settings = json.loads(row["value_json"]) if row else {}
    settings["api_key"] = request.app.state.secret_store.get(SECRET_KEY) or ""
    return settings


def _public_settings(request: Request) -> dict:
    settings = _stored_settings(request)
    key_configured = bool(settings.pop("api_key", ""))
    configured = bool(
        settings.get("enabled") and settings.get("api_url") and settings.get("template_id") and key_configured
    )
    defaults = {
        "enabled": False, "api_url": "", "template_id": "", "timeout_seconds": 30,
        "allow_internet_access": True,
    }
    return {**defaults, **settings, "configured": configured, "key_configured": key_configured}


@router.get("")
def get_sandbox_settings(request: Request):
    return _public_settings(request)


@router.put("")
def put_sandbox_settings(payload: SandboxConfigPayload, request: Request):
    existing_key = request.app.state.secret_store.get(SECRET_KEY)
    api_key = payload.api_key.strip() if payload.api_key and payload.api_key.strip() else existing_key
    values = {
        "enabled": payload.enabled,
        "api_url": payload.api_url.strip(),
        "template_id": payload.template_id.strip(),
        "timeout_seconds": payload.timeout_seconds,
        "allow_internet_access": payload.allow_internet_access,
    }
    # Validate structure without requiring that an intentionally disabled entry has a key.
    candidate = {**values, "api_key": api_key or "placeholder-key", "enabled": True}
    try:
        SandboxSettings.from_value(candidate)
    except Exception as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    if payload.enabled and not api_key:
        raise HTTPException(status_code=422, detail="启用 CubeSandbox 前必须配置 API Key")
    with database_connection(request.app.state.database_path) as connection:
        connection.execute(
            "INSERT INTO app_settings(key,value_json) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=CURRENT_TIMESTAMP",
            (SETTING_KEY, json.dumps(values, ensure_ascii=False)),
        )
    if payload.api_key and payload.api_key.strip():
        request.app.state.secret_store.set(SECRET_KEY, payload.api_key.strip())
    return _public_settings(request)


@router.post("/test")
def test_sandbox_connection(request: Request):
    settings = _stored_settings(request)
    if not settings.get("enabled"):
        raise HTTPException(status_code=409, detail="CubeSandbox 尚未启用")
    if not request.app.state.sandbox_executor.test_connection(settings):
        raise HTTPException(status_code=502, detail="无法连接 CubeSandbox 服务，请检查地址、凭据和服务状态")
    return {"ok": True, "message": "CubeSandbox 连接成功"}
