import json
from pathlib import Path
from uuid import uuid4

import keyring
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from backend.app.db import database_connection
from backend.app.routes.agents import _validate_delegation_targets

router = APIRouter(prefix="/api/setup", tags=["setup"])


class ModelSetup(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    model_config_id: str = Field(min_length=32, max_length=32)
    mode: str = "react"
    system_prompt: str = Field(default="", max_length=20000)
    context_window: int = Field(default=32768, ge=1024, le=2_000_000)
    skill_directory: str = Field(default="", max_length=2048)
    selected_skills: list[str] = Field(default_factory=list, max_length=50)
    selected_mcp_tools: list[str] = Field(default_factory=list, max_length=100)
    selected_local_tools: list[str] = Field(default_factory=list, max_length=100)
    selected_knowledge_bases: list[str] = Field(default_factory=list, max_length=100)
    selected_database_connections: list[str] = Field(default_factory=list, max_length=100)
    selected_agents: list[str] = Field(default_factory=list, max_length=20)
    sandbox_code_enabled: bool = False
    max_rounds: int = Field(default=500, strict=True, ge=-1, le=10000)
    prompt_variables: dict[str, str] = Field(default_factory=dict, max_length=50)

    @field_validator("max_rounds")
    @classmethod
    def valid_max_rounds(cls, value: int) -> int:
        if value != -1 and not 1 <= value <= 10000:
            raise ValueError("最大轮次必须为 -1 或 1 到 10000 的整数")
        return value

    @field_validator("mode")
    @classmethod
    def valid_mode(cls, value: str) -> str:
        if value not in {"react", "plan_execute"}:
            raise ValueError("运行模式无效")
        return value


@router.get("/diagnostics")
def get_diagnostics(request: Request):
    checks = []
    try:
        with database_connection(request.app.state.database_path) as connection:
            connection.execute("SELECT 1").fetchone()
        checks.append({"name": "database", "ok": True, "detail": "本地数据库可读写"})
    except Exception:
        checks.append({"name": "database", "ok": False, "detail": "本地数据库不可用，请检查数据目录权限"})

    frontend = Path(__file__).resolve().parents[3] / "frontend" / "dist" / "index.html"
    checks.append({
        "name": "frontend", "ok": frontend.is_file(),
        "detail": "Web 界面已就绪" if frontend.is_file() else "Web 界面尚未构建，请运行安装器或 npm run build",
    })

    try:
        backend = keyring.get_keyring()
        usable = bool(getattr(backend, "priority", 0) > 0)
    except Exception:
        usable = False
    checks.append({
        "name": "credentials", "ok": usable,
        "detail": "系统凭据存储可用" if usable else "当前系统没有可用的安全凭据存储；为避免明文保存密钥，请先配置系统凭据库",
    })
    return {"ok": all(check["ok"] for check in checks), "checks": checks}


@router.get("/status")
def get_setup_status(request: Request):
    with database_connection(request.app.state.database_path) as connection:
        row = connection.execute(
            "SELECT a.id,a.name,a.model_config_id,m.model FROM agents a "
            "LEFT JOIN model_configs m ON m.id=a.model_config_id ORDER BY a.created_at LIMIT 1"
        ).fetchone()
    return {"completed": row is not None, "agent": dict(row) if row else None}


@router.post("/model", status_code=201)
def configure_model(payload: ModelSetup, request: Request):
    with database_connection(request.app.state.database_path) as connection:
        model = connection.execute(
            "SELECT id,model,last_tested_at FROM model_configs WHERE id=?", (payload.model_config_id,)
        ).fetchone()
        if model is None:
            raise HTTPException(status_code=422, detail="所选 Model 不存在")
        if not request.app.state.secret_store.get(f"model:{payload.model_config_id}:api_key"):
            raise HTTPException(status_code=409, detail="所选 Model 尚未配置 API 密钥")
        if not model["last_tested_at"]:
            raise HTTPException(status_code=409, detail="请先测试所选 Model 的连接")
        if any(
            not isinstance(item, str) or connection.execute(
                "SELECT 1 FROM knowledge_bases WHERE id=?", (item,)
            ).fetchone() is None
            for item in payload.selected_knowledge_bases
        ):
            raise HTTPException(status_code=422, detail="Agent 引用了不存在的知识库")
        if any(
            not isinstance(item, str) or connection.execute(
                "SELECT 1 FROM database_connections WHERE id=?", (item,)
            ).fetchone() is None
            for item in payload.selected_database_connections
        ):
            raise HTTPException(status_code=422, detail="Agent 引用了不存在的数据库连接")
        agent_id = uuid4().hex
        selected_agents = _validate_delegation_targets(connection, agent_id, payload.selected_agents)
        connection.execute(
            "INSERT INTO agents(id,name,provider,base_url,model,system_prompt,mode,context_window,skill_directory,selected_skills_json,selected_agents_json,sandbox_code_enabled,max_rounds,selected_mcp_tools_json,selected_local_tools_json,selected_knowledge_bases_json,selected_database_connections_json,prompt_variables_json,model_config_id) "
            "VALUES(?,?,?,'','',?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (agent_id, payload.name.strip(), "openai_compatible", payload.system_prompt, payload.mode,
             payload.context_window, payload.skill_directory, json.dumps(payload.selected_skills),
             json.dumps(selected_agents), int(payload.sandbox_code_enabled), payload.max_rounds,
             json.dumps(payload.selected_mcp_tools), json.dumps(payload.selected_local_tools),
             json.dumps(payload.selected_knowledge_bases), json.dumps(payload.selected_database_connections),
             json.dumps(payload.prompt_variables), payload.model_config_id),
        )
    return {"id": agent_id, "name": payload.name.strip(), "model": model["model"],
            "model_config_id": payload.model_config_id, "connection_tested": True}
