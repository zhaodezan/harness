import asyncio
import json
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.skills import resolve_selected_skills
from backend.app.skills import Skill
from backend.app.runtime import skill_context
from model_test_utils import create_test_agent, create_test_agent_async


def test_skill_resource_reader_allows_text_inside_selected_skill_and_rejects_escape(tmp_path):
    skill_dir = tmp_path / "skills" / "selected"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: selected\ndescription: selected skill\n---\nInstructions", encoding="utf-8"
    )
    resource_dir = skill_dir / "references"
    resource_dir.mkdir()
    resource = resource_dir / "format.md"
    resource.write_text("Use heading then bullets.", encoding="utf-8")
    skill = Skill("selected", "selected skill", skill_dir / "SKILL.md")

    reader = getattr(skill_context, "read_skill_resource", None)
    assert callable(reader)
    assert reader(skill, "references/format.md") == "Use heading then bullets."
    for path in ("../other/SKILL.md", str(resource.resolve()), ".env", "SKILL.md"):
        try:
            reader(skill, path)
        except ValueError:
            pass
        else:
            raise AssertionError(f"unsafe or non-reference path was accepted: {path}")


def test_skill_resource_reader_rejects_symlinks_and_oversized_files(tmp_path):
    skill_dir = tmp_path / "skills" / "selected"
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text("---\nname: selected\ndescription: selected skill\n---\nInstructions", encoding="utf-8")
    resource = skill_dir / "references"
    resource.mkdir()
    oversized = resource / "large.md"
    oversized.write_text("x" * (256 * 1024 + 1), encoding="utf-8")
    external = tmp_path / "external.md"
    external.write_text("external", encoding="utf-8")
    link = resource / "outside.md"
    try:
        link.symlink_to(external)
    except (OSError, NotImplementedError):
        link = None
    skill = Skill("selected", "selected skill", skill_file)
    reader = getattr(skill_context, "read_skill_resource", None)
    assert callable(reader)
    for path in ["references/large.md", *(["references/outside.md"] if link else [])]:
        try:
            reader(skill, path)
        except ValueError:
            pass
        else:
            raise AssertionError(f"unsafe resource was accepted: {path}")


def test_session_can_read_only_the_selected_skills_reference(tmp_path):
    folder = tmp_path / "data" / "plugins" / "skills" / "guide"
    (folder / "references").mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        "---\nname: guide\ndescription: Guide\n---\nFollow the guide.", encoding="utf-8"
    )
    (folder / "references" / "rules.md").write_text("REFERENCE_MARKER: Use title then bullets.", encoding="utf-8")
    requests = []

    async def provider(request):
        body = json.loads(await request.aread())
        requests.append(body)
        if len(requests) == 1:
            name = body["tools"][0]["function"]["name"]
            call = {"index": 0, "id": "call-read", "type": "function", "function": {
                "name": name, "arguments": json.dumps({"skill_name": "guide", "path": "references/rules.md"})
            }}
            return httpx.Response(200, text=f'data: {json.dumps({"choices": [{"delta": {"tool_calls": [call]}}]})}\n\ndata: [DONE]\n\n')
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"done"}}]}\n\ndata: [DONE]\n\n')

    app = create_app(tmp_path / "resource.sqlite3", data_dir=tmp_path / "data", secret_store=Secrets(), http_transport=httpx.MockTransport(provider))
    with TestClient(app) as client:
        agent = create_test_agent(client, {"name": "Reader", "selected_skills": ["guide"]}).json()
        session = client.post("/api/sessions", json={"agent_id": agent["id"]}).json()
        response = client.post(f"/api/sessions/{session['id']}/messages", json={"content": "use guide"})

    assert response.status_code == 200
    assert "REFERENCE_MARKER" in json.dumps(requests[1]["messages"], ensure_ascii=False)


def test_skill_script_reader_restricts_paths_to_python_inside_selected_skill(tmp_path):
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text("---\nname: calc\ndescription: calc\n---\nrun", encoding="utf-8")
    script = skill_dir / "calculate.py"
    script.write_text("return arguments['value'] * 2", encoding="utf-8")
    skill = Skill("calc", "calc", skill_file)

    reader = getattr(skill_context, "read_skill_script", None)
    assert callable(reader)
    assert reader(skill, "calculate.py") == "return arguments['value'] * 2"
    for path in ("../outside.py", "SKILL.md", "note.md", str(script.resolve())):
        try:
            reader(skill, path)
        except ValueError:
            pass
        else:
            raise AssertionError(f"unsafe Skill script path was accepted: {path}")


def test_session_can_run_explicit_selected_skill_script_only_after_approval(tmp_path):
    folder = tmp_path / "data" / "plugins" / "skills" / "calc"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text("---\nname: calc\ndescription: calc\n---\nUse calculate.py", encoding="utf-8")
    (folder / "calculate.py").write_text("def run(arguments):\n    return arguments['value'] * 2", encoding="utf-8")
    requests = []
    executed = []

    async def provider(request):
        body = json.loads(await request.aread())
        requests.append(body)
        if len(requests) == 1:
            tool = next(t for t in body["tools"] if "selected Python script" in t["function"]["description"])
            call = {"index": 0, "id": "skill-script", "type": "function", "function": {
                "name": tool["function"]["name"],
                "arguments": json.dumps({"skill_name": "calc", "path": "calculate.py", "arguments": {"value": 21}}),
            }}
            frame = {"choices": [{"delta": {"tool_calls": [call]}}]}
            return httpx.Response(200, text="data: " + json.dumps(frame) + "\n\ndata: [DONE]\n\n")
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"done"}}]}\n\ndata: [DONE]\n\n')

    async def run():
        app = create_app(tmp_path / "skill-script.sqlite3", data_dir=tmp_path / "data", secret_store=Secrets(), http_transport=httpx.MockTransport(provider))
        async def fake_execute(source, arguments):
            executed.append((source, arguments))
            return 42
        app.state.sandbox_executor.execute_python_async = fake_execute
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
                agent = (await create_test_agent_async(client, app, {"name": "Calc", "selected_skills": ["calc"]})).json()
                session = (await client.post("/api/sessions", json={"agent_id": agent["id"]})).json()
                task = asyncio.create_task(client.post(f"/api/sessions/{session['id']}/messages", json={"content": "double 21"}))
                for _ in range(200):
                    if app.state.pending_approvals:
                        break
                    await asyncio.sleep(0.01)
                assert app.state.pending_approvals
                assert executed == []
                approval = next(iter(app.state.pending_approvals))
                await client.post(f"/api/approvals/{approval}", json={"approved": True})
                response = await task
                assert response.status_code == 200

    asyncio.run(run())
    assert executed == [("def run(arguments):\n    return arguments['value'] * 2", {"value": 21})]
    assert len(requests) == 2


def test_resolve_selected_skills_reports_missing_names():
    class SkillStub:
        def __init__(self, name): self.name = name
    selected, missing = resolve_selected_skills(
        ["summarize", "missing", "summarize"],
        [SkillStub("summarize")],
        [SkillStub("legacy")],
    )
    assert [skill.name for skill in selected] == ["summarize"]
    assert missing == ["missing"]


class Secrets:
    def get(self, name): return "key"
    def set(self, name, value): pass
    def delete(self, name): pass


def test_selected_skill_is_loaded_without_leaking_other_skill_contents(tmp_path):
    root = tmp_path / "skills"
    (root / "summarize").mkdir(parents=True)
    (root / "summarize" / "SKILL.md").write_text(
        "---\nname: summarize\ndescription: Summarize text\n---\nPreserve key points.", encoding="utf-8"
    )
    (root / "private").mkdir()
    (root / "private" / "SKILL.md").write_text(
        "---\nname: private\ndescription: Private secret notes\n---\nDO NOT LOAD THIS.", encoding="utf-8"
    )
    sent = []
    async def provider(request: httpx.Request):
        sent.append(json.loads(await request.aread()))
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n')

    app = create_app(tmp_path / "skills.sqlite3", http_transport=httpx.MockTransport(provider), secret_store=Secrets())
    with TestClient(app) as client:
        agent = create_test_agent(client, {
            "name": "Helper", "base_url": "https://example.test/v1", "model": "m",
            "skill_directory": str(root), "selected_skills": ["summarize"],
        }).json()
        discovered = client.get(f"/api/skills/discover?directory={root}").json()
        session = client.post("/api/sessions", json={"agent_id": agent["id"]}).json()
        client.post(f"/api/sessions/{session['id']}/messages", json={"content": "summarize this"})

    assert [skill["name"] for skill in discovered] == ["private", "summarize"]
    system_text = "\n".join(message["content"] for message in sent[0]["messages"] if message["role"] == "system")
    assert "Preserve key points." in system_text
    assert "适用时遵循" in system_text
    assert "DO NOT LOAD THIS." not in system_text


def test_compaction_preserves_rendered_prompt_and_skill_but_does_not_summarize_them(tmp_path):
    from backend.app.db import database_connection
    from uuid import uuid4

    data = tmp_path / "data"
    folder = data / "plugins" / "skills" / "format"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        "---\nname: format\ndescription: Format text\n---\nUse FORMAT_MARKER.", encoding="utf-8"
    )
    sent = []

    async def provider(request):
        body = json.loads(await request.aread())
        sent.append(body)
        if not body["stream"]:
            return httpx.Response(200, json={"choices": [{"message": {"content": "HISTORY_SUMMARY"}}]})
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n')

    app = create_app(tmp_path / "db.sqlite3", data_dir=data, secret_store=Secrets(), http_transport=httpx.MockTransport(provider))
    with TestClient(app) as client:
        agent = create_test_agent(client, {"name": "Helper", "context_window": 1024,
            "selected_skills": ["format"], "system_prompt": "Hello {{name}}", "prompt_variables": {"name": "Alice"}}).json()
        session = client.post("/api/sessions", json={"agent_id": agent["id"]}).json()
        with database_connection(app.state.database_path) as connection:
            connection.executemany("INSERT INTO messages(id,session_id,role,content) VALUES(?,?,'user',?)",
                [(uuid4().hex, session["id"], f"history {i} " + "x" * 500) for i in range(8)])
        result = client.post(f"/api/sessions/{session['id']}/messages", json={"content": "current request"})

    assert "event: done" in result.text
    assert len(sent) == 2
    summary_input, execution = [json.dumps(call["messages"], ensure_ascii=False) for call in sent]
    assert "FORMAT_MARKER" in execution and "Hello Alice" in execution
    assert "FORMAT_MARKER" not in summary_input and "Hello Alice" not in summary_input
    assert "HISTORY_SUMMARY" in execution and "current request" in execution
