from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class ModelConnectionTest(BaseModel):
    base_url: str = Field(min_length=1, max_length=2048)
    model: str = Field(min_length=1, max_length=200)
    api_key: str = Field(min_length=1, max_length=4096, repr=False)

    @field_validator("base_url", "model", "api_key")
    @classmethod
    def strip_connection_fields(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("此字段不能为空")
        return value

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("模型服务地址必须以 http:// 或 https:// 开头")
        return value.rstrip("/")


class ModelConfigCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    base_url: str = Field(min_length=1, max_length=2048)
    model: str = Field(min_length=1, max_length=200)
    api_key: str = Field(min_length=1, max_length=4096, repr=False)

    @field_validator("name", "base_url", "model")
    @classmethod
    def strip_model_fields(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("此字段不能为空")
        return value

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("模型服务地址必须以 http:// 或 https:// 开头")
        return value.rstrip("/")


class ModelConfigPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    base_url: str | None = Field(default=None, min_length=1, max_length=2048)
    model: str | None = Field(default=None, min_length=1, max_length=200)
    api_key: str | None = Field(default=None, min_length=1, max_length=4096, repr=False)

    @field_validator("name", "base_url", "model", "api_key")
    @classmethod
    def validate_nonblank_patch_fields(cls, value: str | None) -> str | None:
        if value is None:
            return value
        value = value.strip()
        if not value:
            raise ValueError("此字段不能为空")
        return value


class ModelConfigResponse(BaseModel):
    id: str
    name: str
    base_url: str
    model: str
    credential_configured: bool
    last_tested_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class AgentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    model_config_id: str = Field(min_length=32, max_length=32)
    provider: Literal["openai_compatible"] = "openai_compatible"
    system_prompt: str = Field(default="", max_length=20000)
    mode: Literal["react", "plan_execute"] = "react"
    context_window: int = Field(default=32768, ge=1024, le=2_000_000)
    skill_directory: str = Field(default="", max_length=2048)
    selected_skills: list[str] = Field(default_factory=list, max_length=50)
    selected_mcp_tools: list[str] = Field(default_factory=list, max_length=100)
    selected_local_tools: list[str] = Field(default_factory=list, max_length=100)
    selected_knowledge_bases: list[str] = Field(default_factory=list, max_length=100)
    selected_database_connections: list[str] = Field(default_factory=list, max_length=100)
    selected_agents: list[str] = Field(default_factory=list, max_length=20)
    sandbox_code_enabled: bool = False
    prompt_variables: dict[str, str] = Field(default_factory=dict, max_length=50)
    input_rate_per_million: float | None = Field(default=None, ge=0, le=1_000_000)
    output_rate_per_million: float | None = Field(default=None, ge=0, le=1_000_000)

    @field_validator("name")
    @classmethod
    def name_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Agent 名称不能为空")
        return value


class AgentPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    model_config_id: str | None = Field(default=None, min_length=32, max_length=32)
    system_prompt: str | None = Field(default=None, max_length=20000)
    mode: Literal["react", "plan_execute"] | None = None
    context_window: int | None = Field(default=None, ge=1024, le=2_000_000)
    skill_directory: str | None = Field(default=None, max_length=2048)
    selected_skills: list[str] | None = Field(default=None, max_length=50)
    selected_mcp_tools: list[str] | None = Field(default=None, max_length=100)
    selected_local_tools: list[str] | None = Field(default=None, max_length=100)
    selected_knowledge_bases: list[str] | None = Field(default=None, max_length=100)
    selected_database_connections: list[str] | None = Field(default=None, max_length=100)
    selected_agents: list[str] | None = Field(default=None, max_length=20)
    sandbox_code_enabled: bool | None = None
    prompt_variables: dict[str, str] | None = Field(default=None, max_length=50)
    input_rate_per_million: float | None = Field(default=None, ge=0, le=1_000_000)
    output_rate_per_million: float | None = Field(default=None, ge=0, le=1_000_000)

    @field_validator("name")
    @classmethod
    def patch_name_must_not_be_blank(cls, value: str | None) -> str | None:
        if value is None:
            return value
        value = value.strip()
        if not value:
            raise ValueError("Agent 名称不能为空")
        return value


class AgentResponse(BaseModel):
    id: str
    name: str
    provider: str
    model_config_id: str
    model: str
    model_config_name: str
    system_prompt: str
    mode: str
    context_window: int
    skill_directory: str
    selected_skills: list[str]
    selected_mcp_tools: list[str] = Field(default_factory=list)
    selected_local_tools: list[str] = Field(default_factory=list)
    selected_knowledge_bases: list[str] = Field(default_factory=list)
    selected_database_connections: list[str] = Field(default_factory=list)
    selected_agents: list[str] = Field(default_factory=list)
    sandbox_code_enabled: bool = False
    prompt_variables: dict[str, str]
    input_rate_per_million: float | None = None
    output_rate_per_million: float | None = None
    created_at: datetime
    updated_at: datetime
