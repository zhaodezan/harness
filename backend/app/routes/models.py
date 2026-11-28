from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, Response, status

from backend.app.db import database_connection
from backend.app.models import ModelConfigCreate, ModelConfigPatch, ModelConfigResponse, ModelConnectionTest
from backend.app.provider.openai_compatible import ProviderError, complete_chat

router = APIRouter(prefix="/api/models", tags=["models"])


def _credential_save_error() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail="模型连接成功，但 API 密钥未保存。请检查系统凭据库及应用数据目录的权限后重试。",
        headers={"X-Harness-Error": "model_credential_save_failed"},
    )


def _read_key(request: Request, name: str) -> str | None:
    try:
        return request.app.state.secret_store.get(name)
    except Exception:
        raise HTTPException(status_code=503, detail="无法读取本机 API 密钥，请检查系统凭据库或应用数据目录。") from None


async def _test_candidate(request: Request, *, base_url: str, model: str, api_key: str) -> None:
    try:
        await complete_chat(
            request.app.state.http_client,
            base_url=base_url,
            model=model,
            api_key=api_key,
            messages=[{"role": "user", "content": "请仅回复：连接成功"}],
        )
    except ProviderError as error:
        detail = str(error)
        if "认证失败" in detail:
            detail = "模型连接测试失败：请检查 API 密钥和服务商权限"
        elif "HTTP 404" in detail or "拒绝了请求" in detail:
            detail = "模型连接测试失败：请检查 API Base URL 和模型标识"
        elif "超时" in detail or "无法连接" in detail:
            detail = f"模型连接测试失败：{detail}"
        else:
            detail = f"模型连接测试失败：{detail}"
        raise HTTPException(status_code=502, detail=detail) from None


def _model_response(row, request: Request) -> ModelConfigResponse:
    item = dict(row)
    model_id = item["id"]
    item["credential_configured"] = bool(_read_key(request, f"model:{model_id}:api_key"))
    return ModelConfigResponse(**item)


def _get_row(model_id: str, request: Request):
    with database_connection(request.app.state.database_path) as connection:
        row = connection.execute(
            "SELECT id,name,base_url,model,last_tested_at,created_at,updated_at "
            "FROM model_configs WHERE id = ?", (model_id,),
        ).fetchone()
    return row


@router.get("", response_model=list[ModelConfigResponse])
def list_models(request: Request):
    with database_connection(request.app.state.database_path) as connection:
        rows = connection.execute(
            "SELECT id,name,base_url,model,last_tested_at,created_at,updated_at "
            "FROM model_configs ORDER BY name COLLATE NOCASE,id"
        ).fetchall()
    return [_model_response(row, request) for row in rows]


@router.get("/migration-status")
def model_migration_status(request: Request):
    return {"warnings": getattr(request.app.state, "model_migration_warnings", [])}


@router.post("/test-connection")
async def test_candidate_model(payload: ModelConnectionTest, request: Request):
    await _test_candidate(
        request,
        base_url=payload.base_url,
        model=payload.model,
        api_key=payload.api_key,
    )
    return {"status": "ok"}


@router.post("", response_model=ModelConfigResponse, status_code=status.HTTP_201_CREATED)
async def create_model(payload: ModelConfigCreate, request: Request):
    await _test_candidate(
        request,
        base_url=payload.base_url,
        model=payload.model,
        api_key=payload.api_key,
    )
    model_id = uuid4().hex
    with database_connection(request.app.state.database_path) as connection:
        connection.execute(
            "INSERT INTO model_configs(id,name,base_url,model,last_tested_at) VALUES(?,?,?,?,CURRENT_TIMESTAMP)",
            (model_id, payload.name, payload.base_url, payload.model),
        )
    try:
        request.app.state.secret_store.set(f"model:{model_id}:api_key", payload.api_key)
    except Exception:
        with database_connection(request.app.state.database_path) as connection:
            connection.execute("DELETE FROM model_configs WHERE id = ?", (model_id,))
        raise _credential_save_error() from None
    row = _get_row(model_id, request)
    return _model_response(row, request)


@router.patch("/{model_id}", response_model=ModelConfigResponse)
async def update_model(model_id: str, payload: ModelConfigPatch, request: Request):
    updates = payload.model_dump(exclude_unset=True)
    if "base_url" in updates and updates["base_url"] is not None:
        if not updates["base_url"].startswith(("http://", "https://")):
            raise HTTPException(status_code=422, detail="模型服务地址必须以 http:// 或 https:// 开头")
        updates["base_url"] = updates["base_url"].rstrip("/")
    if not updates:
        raise HTTPException(status_code=422, detail="请提供有效的 Model 更新内容")
    updates = {key: value for key, value in updates.items() if value is not None}
    if not updates:
        raise HTTPException(status_code=422, detail="请提供有效的 Model 更新内容")
    current = _get_row(model_id, request)
    if current is None:
        raise HTTPException(status_code=404, detail="Model 配置不存在")
    candidate_base_url = updates.get("base_url", current["base_url"])
    candidate_model = updates.get("model", current["model"])
    key_name = f"model:{model_id}:api_key"
    current_key = _read_key(request, key_name)
    candidate_key = updates.pop("api_key", current_key)
    if not candidate_key:
        raise HTTPException(status_code=409, detail="请先为此 Model 配置 API 密钥")
    if candidate_key != current_key:
        with database_connection(request.app.state.database_path) as connection:
            migration = connection.execute(
                "SELECT legacy_agent_id,credential_migrated FROM model_configs WHERE id=?", (model_id,)
            ).fetchone()
        if migration["legacy_agent_id"] and not migration["credential_migrated"]:
            legacy_key = request.app.state.secret_store.get(f"agent:{migration['legacy_agent_id']}:api_key")
            if legacy_key:
                raise HTTPException(status_code=409, detail="旧密钥迁移仍未完成；请重启应用重试迁移后再更新凭据")
    await _test_candidate(
        request, base_url=candidate_base_url, model=candidate_model, api_key=candidate_key
    )
    secret_changed = candidate_key != current_key
    if secret_changed:
        try:
            request.app.state.secret_store.set(key_name, candidate_key)
        except Exception:
            raise _credential_save_error() from None
    assignments = [f'{field} = ?' for field in updates]
    assignments.extend(['last_tested_at=CURRENT_TIMESTAMP', 'updated_at=CURRENT_TIMESTAMP'])
    try:
        with database_connection(request.app.state.database_path) as connection:
            cursor = connection.execute(
                f"UPDATE model_configs SET {', '.join(assignments)} WHERE id=?",
                [*updates.values(), model_id],
            )
            if cursor.rowcount == 0:
                raise HTTPException(status_code=404, detail="Model 配置不存在")
    except Exception:
        if secret_changed:
            try:
                if current_key is None:
                    request.app.state.secret_store.delete(key_name)
                else:
                    request.app.state.secret_store.set(key_name, current_key)
            except Exception:
                raise HTTPException(status_code=503, detail="配置更新失败且无法恢复旧密钥，请重新设置凭据") from None
        raise
    return _model_response(_get_row(model_id, request), request)


@router.put("/{model_id}/credentials", status_code=status.HTTP_204_NO_CONTENT)
async def set_model_credentials(model_id: str, request: Request, payload: dict):
    api_key = payload.get("api_key") if isinstance(payload, dict) else None
    if not isinstance(api_key, str) or not api_key.strip() or len(api_key) > 4096:
        raise HTTPException(status_code=422, detail="请输入有效的 API 密钥")
    with database_connection(request.app.state.database_path) as connection:
        row = connection.execute(
            "SELECT id,base_url,model,legacy_agent_id,credential_migrated FROM model_configs WHERE id=?", (model_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Model 配置不存在")
    await _test_candidate(
        request, base_url=row["base_url"], model=row["model"], api_key=api_key
    )
    if row["legacy_agent_id"] and not row["credential_migrated"]:
        legacy_key = request.app.state.secret_store.get(f"agent:{row['legacy_agent_id']}:api_key")
        if legacy_key:
            raise HTTPException(status_code=409, detail="旧密钥迁移仍未完成；请重启应用重试迁移后再更新凭据")
        with database_connection(request.app.state.database_path) as connection:
            connection.execute(
                "UPDATE model_configs SET credential_migrated=1 WHERE id=?", (model_id,)
            )
    try:
        request.app.state.secret_store.set(f"model:{model_id}:api_key", api_key)
    except Exception:
        raise _credential_save_error() from None
    with database_connection(request.app.state.database_path) as connection:
        connection.execute(
            "UPDATE model_configs SET last_tested_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (model_id,),
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/{model_id}/credentials", status_code=status.HTTP_204_NO_CONTENT)
def delete_model_credentials(model_id: str, request: Request):
    row = _get_row(model_id, request)
    if row is None:
        raise HTTPException(status_code=404, detail="Model 配置不存在")
    with database_connection(request.app.state.database_path) as connection:
        migration = connection.execute(
            "SELECT legacy_agent_id,credential_migrated FROM model_configs WHERE id=?", (model_id,)
        ).fetchone()
    if migration["legacy_agent_id"] and not migration["credential_migrated"]:
        raise HTTPException(status_code=409, detail="旧密钥迁移仍未完成；请先重试迁移")
    try:
        request.app.state.secret_store.delete(f"model:{model_id}:api_key")
    except Exception:
        raise HTTPException(status_code=503, detail="无法清除系统凭据库中的 API 密钥") from None
    with database_connection(request.app.state.database_path) as connection:
        connection.execute("UPDATE model_configs SET last_tested_at=NULL WHERE id=?", (model_id,))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{model_id}/test-connection")
async def test_model_connection(model_id: str, request: Request):
    row = _get_row(model_id, request)
    if row is None:
        raise HTTPException(status_code=404, detail="Model 配置不存在")
    api_key = request.app.state.secret_store.get(f"model:{model_id}:api_key")
    if not api_key:
        raise HTTPException(status_code=409, detail="请先为此 Model 配置 API 密钥")
    try:
        await complete_chat(
            request.app.state.http_client, base_url=row["base_url"], model=row["model"],
            api_key=api_key, messages=[{"role": "user", "content": "请仅回复：连接成功"}],
        )
    except ProviderError as error:
        raise HTTPException(status_code=502, detail=str(error)) from None
    with database_connection(request.app.state.database_path) as connection:
        connection.execute(
            "UPDATE model_configs SET last_tested_at=CURRENT_TIMESTAMP WHERE id=?", (model_id,)
        )
    return {"status": "ok"}


@router.delete("/{model_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_model(model_id: str, request: Request):
    with database_connection(request.app.state.database_path) as connection:
        connection.execute(
            "UPDATE agents SET model_config_id=NULL, model_binding_cleared=1, updated_at=CURRENT_TIMESTAMP "
            "WHERE model_config_id=?", (model_id,),
        )
        if connection.execute("DELETE FROM model_configs WHERE id=?", (model_id,)).rowcount == 0:
            raise HTTPException(status_code=404, detail="Model 配置不存在")
    try:
        request.app.state.secret_store.delete(f"model:{model_id}:api_key")
    except Exception:
        raise HTTPException(status_code=503, detail="Model 已删除，但无法清除系统凭据库中的旧密钥") from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)
