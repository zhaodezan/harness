import json

import httpx
from fastapi.testclient import TestClient

from backend.app.main import create_app
from model_test_utils import create_test_agent


class DebugSecrets:
    def __init__(self): self.values = {}
    def get(self, name): return self.values.get(name)
    def set(self, name, value): self.values[name] = value
    def delete(self, name): self.values.pop(name, None)


def test_debug_uses_unsaved_model_selection_without_sending_credentials(tmp_path):
    requests = []
    secrets = DebugSecrets()

    async def provider(request):
        body = json.loads(await request.aread())
        requests.append((body, request.headers.get("authorization")))
        if body.get("stream") is False:
            return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"draft-answer"}}]}\n\ndata: [DONE]\n\n')

    app = create_app(tmp_path / "draft-debug.sqlite3", data_dir=tmp_path / "data",
                     secret_store=secrets, http_transport=httpx.MockTransport(provider))
    with TestClient(app) as client:
        saved_model = client.post("/api/models", json={
            "name": "Saved", "base_url": "https://saved.test/v1", "model": "saved-model", "api_key": "saved-key",
        }).json()
        draft_model = client.post("/api/models", json={
            "name": "Draft", "base_url": "https://draft.test/v1", "model": "draft-model", "api_key": "draft-key",
        }).json()
        client.post(f"/api/models/{saved_model['id']}/test-connection")
        client.post(f"/api/models/{draft_model['id']}/test-connection")
        agent = create_test_agent(client, {"name": "Debug", "model_config_id": saved_model["id"]}).json()
        response = client.post(f"/api/agents/{agent['id']}/debug", json={
            "config": {"model_config_id": draft_model["id"], "mode": "react"},
            "messages": [{"role": "user", "content": "hello"}],
        })

    assert response.status_code == 200
    assert '"text":"draft-answer"' in response.text
    assert any(body.get("model") == "draft-model" and auth == "Bearer draft-key" for body, auth in requests)
    assert "draft-key" not in response.text


def test_plan_execute_debug_runs_steps_after_planning(tmp_path):
    requests = []

    async def provider(request):
        body = json.loads(await request.aread())
        requests.append(body)
        if body.get("stream") is False:
            return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
        if any("仅制定" in message.get("content", "") for message in body["messages"]):
            content = chr(10).join(["1. First step", "2. Second step"])
        else:
            content = "step done"
        frame = {"choices": [{"delta": {"content": content}}]}
        stream_text = "data: " + json.dumps(frame) + chr(10) + chr(10) + "data: [DONE]" + chr(10) + chr(10)
        return httpx.Response(200, text=stream_text)

    secrets = DebugSecrets()
    app = create_app(tmp_path / "plan-debug.sqlite3", data_dir=tmp_path / "data", secret_store=secrets,
                     http_transport=httpx.MockTransport(provider))
    with TestClient(app) as client:
        model = client.post("/api/models", json={
            "name": "Planner", "base_url": "https://example.test/v1", "model": "planner-model", "api_key": "key",
        }).json()
        client.post(f"/api/models/{model['id']}/test-connection")
        agent = client.post("/api/agents", json={"name": "Planner", "model_config_id": model["id"]}).json()
        response = client.post(f"/api/agents/{agent['id']}/debug", json={
            "config": {"model_config_id": model["id"], "mode": "plan_execute"},
            "messages": [{"role": "user", "content": "Do two things"}],
        })

    assert response.status_code == 200
    assert "event: plan" in response.text
    assert len(requests) >= 3
    assert any("First step" in message.get("content", "") for item in requests[2:] for message in item["messages"])
    assert any("Second step" in message.get("content", "") for item in requests[2:] for message in item["messages"])
    assert any("执行计划：" in message.get("content", "") for item in requests[2:] for message in item["messages"])
    assert response.text.count('"status":"completed","summary":"第') == 2
