import asyncio
import json
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from backend.app.db import database_connection

MAX_DELEGATION_DEPTH = 2
MAX_DELEGATION_CALLS = 4
DELEGATION_TIMEOUT_SECONDS = 60
MAX_DELEGATION_TASK_CHARS = 4000
MAX_DELEGATION_RESULT_CHARS = 12000


class TextPart(BaseModel):
    kind: Literal["text"] = "text"
    text: str = Field(max_length=MAX_DELEGATION_TASK_CHARS)


class A2AMessage(BaseModel):
    messageId: str = Field(default_factory=lambda: uuid4().hex)
    role: Literal["user", "agent"]
    parts: list[TextPart] = Field(min_length=1, max_length=10)


class A2ASkill(BaseModel):
    id: str
    name: str
    description: str = ""


class AgentCard(BaseModel):
    protocolVersion: Literal["1.0"] = "1.0"
    name: str
    description: str = ""
    version: str = "1.0"
    url: str
    skills: list[A2ASkill] = Field(default_factory=list)


class TaskStatus(BaseModel):
    state: Literal["submitted", "working", "completed", "failed", "canceled", "rejected"]
    message: A2AMessage | None = None


class Artifact(BaseModel):
    artifactId: str = Field(default_factory=lambda: uuid4().hex)
    name: str = "result"
    parts: list[TextPart]


class A2ATask(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    status: TaskStatus
    artifacts: list[Artifact] = Field(default_factory=list)


class DelegationBudget:
    def __init__(self, chain: list[str | None]):
        self.chain = list(chain)
        self.calls = 0


class A2AService:
    def __init__(self, database_path, runner=None):
        self.database_path = database_path
        self.runner = runner

    def agent_card(self, agent_id: str) -> AgentCard:
        with database_connection(self.database_path) as connection:
            row = connection.execute(
                "SELECT name,system_prompt FROM agents WHERE id=?", (agent_id,)
            ).fetchone()
        if row is None:
            raise ValueError("目标 Agent 不存在")
        return AgentCard(name=row["name"], description=row["system_prompt"][:300], url=f"in-process://agents/{agent_id}")

    def create_delegate_tool(self, source_agent_id: str | None, selected_ids: list[str], existing_tools: list[dict]) -> tuple[dict | None, dict[str, str]]:
        with database_connection(self.database_path) as connection:
            targets = []
            for target_id in dict.fromkeys(selected_ids):
                if target_id == source_agent_id:
                    continue
                row = connection.execute("SELECT id,name FROM agents WHERE id=?", (target_id,)).fetchone()
                if row is not None:
                    targets.append(dict(row))
        if not targets:
            return None, {}
        occupied = {tool["name"] for tool in existing_tools}
        name = "a2a:delegate_to_agent"
        while name in occupied:
            name = f"a2a:delegate_to_agent:{uuid4().hex[:8]}"
        return ({
            "name": name,
            "description": ("Delegate a focused task to an available Agent using A2A, only when useful. "
                            "Available Agents: " + "; ".join(f"{t['name']} ({t['id']})" for t in targets)),
            "input_schema": {
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string", "enum": [target["id"] for target in targets]},
                    "task": {"type": "string", "minLength": 1, "maxLength": MAX_DELEGATION_TASK_CHARS},
                },
                "required": ["agent_id", "task"],
                "additionalProperties": False,
            },
            "risk": "read_only",
            "plugin_id": "internal:a2a",
        }, {target["id"]: target["name"] for target in targets})

    async def delegate(
        self, source_agent_id: str | None, target_agent_id: str, task_text: str, budget: DelegationBudget,
        *, chat_targets: frozenset[str] | None = None,
    ) -> A2ATask:
        if not isinstance(task_text, str) or not task_text.strip() or len(task_text) > MAX_DELEGATION_TASK_CHARS:
            return A2ATask(status=TaskStatus(state="rejected", message=A2AMessage(role="agent", parts=[TextPart(text="任务文本无效")])) )
        if budget.calls >= MAX_DELEGATION_CALLS:
            return A2ATask(status=TaskStatus(state="rejected", message=A2AMessage(role="agent", parts=[TextPart(text="本次运行的 Agent 委派次数已达上限")])) )
        if len(budget.chain) - 1 >= MAX_DELEGATION_DEPTH:
            return A2ATask(status=TaskStatus(state="rejected", message=A2AMessage(role="agent", parts=[TextPart(text="Agent 委派深度已达上限")])) )
        if target_agent_id == source_agent_id or target_agent_id in budget.chain:
            return A2ATask(status=TaskStatus(state="rejected", message=A2AMessage(role="agent", parts=[TextPart(text="已阻止 Agent 自调用或循环委派")])) )
        with database_connection(self.database_path) as connection:
            source = connection.execute("SELECT selected_agents_json FROM agents WHERE id=?", (source_agent_id,)).fetchone()
            target = connection.execute("SELECT name FROM agents WHERE id=?", (target_agent_id,)).fetchone()
        if chat_targets is not None and budget.chain == [source_agent_id]:
            allowed = chat_targets
        else:
            allowed = json.loads(source["selected_agents_json"] or "[]") if source is not None else ()
        if (source_agent_id is not None and source is None) or target is None or target_agent_id not in allowed:
            return A2ATask(status=TaskStatus(state="rejected", message=A2AMessage(role="agent", parts=[TextPart(text="目标 Agent 未获授权或已不存在")])) )
        if self.runner is None:
            return A2ATask(status=TaskStatus(state="failed", message=A2AMessage(role="agent", parts=[TextPart(text="Agent 执行器不可用")])) )
        incoming = A2AMessage(role="user", parts=[TextPart(text=task_text.strip())])
        task = A2ATask(status=TaskStatus(state="working", message=incoming))
        budget.calls += 1
        budget.chain.append(target_agent_id)
        try:
            result = await asyncio.wait_for(self.runner(target_agent_id, task_text.strip(), budget), timeout=DELEGATION_TIMEOUT_SECONDS)
            task.artifacts = [Artifact(parts=[TextPart(text=str(result)[:MAX_DELEGATION_RESULT_CHARS])])]
            task.status = TaskStatus(state="completed")
        except asyncio.CancelledError:
            task.status = TaskStatus(state="canceled", message=A2AMessage(role="agent", parts=[TextPart(text="父任务已取消")]))
            raise
        except asyncio.TimeoutError:
            task.status = TaskStatus(state="failed", message=A2AMessage(role="agent", parts=[TextPart(text="子 Agent 执行超时")]))
        except Exception:
            task.status = TaskStatus(state="failed", message=A2AMessage(role="agent", parts=[TextPart(text="子 Agent 执行失败")]))
        finally:
            budget.chain.pop()
        return task
