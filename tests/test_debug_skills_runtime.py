import json

import httpx
from fastapi.testclient import TestClient

from backend.app.main import create_app


class Secrets:
    def get(self, name): return "key"
    def set(self, name, value): pass
    def delete(self, name): pass


def test_debug_chat_can_read_only_the_selected_skills_reference(tmp_path):
    data_dir = tmp_path / "data"
    folder = data_dir / "plugins" / "skills" / "guide"
    (folder / "references").mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        "---\nname: guide\ndescription: Guide\n---\nFollow the guide.", encoding="utf-8"
    )
    (folder / "references" / "rules.md").write_text("DEBUG_REFERENCE_MARKER", encoding="utf-8")
    requests = []

    async def provider(request):
        body = json.loads(await request.aread())
        requests.append(body)
        if body.get("stream") is False:
            return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
        if len([item for item in requests if item.get("stream")]) == 1:
            name = body["tools"][0]["function"]["name"]
            call = {"index": 0, "id": "call-read", "type": "function", "function": {
                "name": name, "arguments": json.dumps({"skill_name": "guide", "path": "references/rules.md"})
            }}
            return httpx.Response(200, text=f'data: {json.dumps({"choices": [{"delta": {"tool_calls": [call]}}]})}\n\ndata: [DONE]\n\n')
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"done"}}]}\n\ndata: [DONE]\n\n')

    app = create_app(tmp_path / "debug-resource.sqlite3", data_dir=data_dir, secret_store=Secrets(), http_transport=httpx.MockTransport(provider))
    with TestClient(app) as client:
        model = client.post("/api/models", json={
            "name": "Model", "base_url": "https://example.test/v1", "model": "m", "api_key": "key",
        }).json()
        agent = client.post("/api/agents", json={"name": "Agent", "model_config_id": model["id"]}).json()
        response = client.post(f"/api/agents/{agent['id']}/debug", json={
            "config": {"model_config_id": model["id"], "selected_skills": ["guide"]},
            "messages": [{"role": "user", "content": "use the guide"}],
        })

    assert response.status_code == 200
    assert "DEBUG_REFERENCE_MARKER" in json.dumps(requests[-1]["messages"], ensure_ascii=False)


def test_debug_loads_only_selected_skills_and_reports_missing_names(tmp_path):
    data_dir = tmp_path / "data"
    skills_dir = data_dir / "plugins" / "skills"
    for name, content in [("selected", "SELECTED INSTRUCTIONS"), ("other", "UNSELECTED SECRET")]:
        folder = skills_dir / name
        folder.mkdir(parents=True)
        (folder / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {name} skill\n---\n{content}", encoding="utf-8"
        )
    calls = []

    async def provider(request):
        body = json.loads(await request.aread())
        calls.append(body)
        if body.get("stream") is False:
            return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n')

    app = create_app(
        tmp_path / "skills-debug.sqlite3", data_dir=data_dir, secret_store=Secrets(),
        http_transport=httpx.MockTransport(provider),
    )
    with TestClient(app) as client:
        model = client.post("/api/models", json={
            "name": "Model", "base_url": "https://example.test/v1", "model": "m", "api_key": "key",
        }).json()
        agent = client.post("/api/agents", json={"name": "Agent", "model_config_id": model["id"]}).json()
        response = client.post(f"/api/agents/{agent['id']}/debug", json={
            "config": {"model_config_id": model["id"], "selected_skills": ["selected", "missing"]},
            "messages": [{"role": "user", "content": "try skill"}],
        })

    assert response.status_code == 200
    system_content = "\n".join(
        message.get("content", "")
        for message in calls[-1]["messages"]
        if message["role"] == "system"
    )
    assert "SELECTED INSTRUCTIONS" in system_content
    assert "UNSELECTED SECRET" not in system_content
    assert '"event_type":"skills_loaded","status":"warning"' in response.text
    assert "missing" in response.text
