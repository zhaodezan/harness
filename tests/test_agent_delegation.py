import json

import httpx
from fastapi.testclient import TestClient

from backend.app.main import create_app
from model_test_utils import create_test_agent


class Secrets:
    def get(self, name):
        return "key"
    def set(self, name, value):
        pass
    def delete(self, name):
        pass


def test_agent_delegates_only_to_an_authorized_agent_and_receives_task_result(tmp_path):
    requests = []

    async def provider(request):
        body = json.loads(await request.aread())
        requests.append(body)
        if body.get("stream") is False:
            return httpx.Response(200, json={"choices": [{"message": {"content": "child result"}}]})
        if body["model"] == "child-model":
            return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"child result"}}]}\n\ndata: [DONE]\n\n')
        if body["model"] == "parent-model" and body.get("tools") and not any(message.get("role") == "tool" for message in body["messages"]):
            delegate = next(tool for tool in body["tools"] if "delegate" in tool["function"]["description"].lower())
            call = {"index": 0, "id": "delegate-1", "type": "function", "function": {
                "name": delegate["function"]["name"],
                "arguments": json.dumps({"agent_id": target_id, "task": "Summarize the release notes"}),
            }}
            return httpx.Response(200, text=f'data: {json.dumps({"choices": [{"delta": {"tool_calls": [call]}}]})}\n\ndata: [DONE]\n\n')
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"parent done"}}]}\n\ndata: [DONE]\n\n')

    app = create_app(tmp_path / "delegation.sqlite3", data_dir=tmp_path / "data", secret_store=Secrets(), http_transport=httpx.MockTransport(provider))
    with TestClient(app) as client:
        target = create_test_agent(client, {"name": "Researcher", "model": "child-model", "system_prompt": "Child system"}).json()
        target_id = target["id"]
        parent = create_test_agent(client, {"name": "Coordinator", "model": "parent-model", "selected_agents": [target_id]}).json()
        session = client.post("/api/sessions", json={"agent_id": parent["id"]}).json()
        response = client.post(f"/api/sessions/{session['id']}/messages", json={"content": "Research this"})

    assert response.status_code == 200
    assert any(request["model"] == "child-model" for request in requests), response.text
    assert '"event_type":"a2a_task","status":"completed"' in response.text, response.text
    assert any(request["model"] == "child-model" and any(m["content"] == "Child system" for m in request["messages"]) for request in requests)
    parent_continuation = next(request for request in requests if request["model"] == "parent-model" and any(m.get("role") == "tool" for m in request["messages"]))
    assert "child result" in json.dumps(parent_continuation["messages"], ensure_ascii=False)


def test_debug_chat_can_delegate_only_to_the_saved_authorized_agent(tmp_path):
    requests = []

    async def provider(request):
        body = json.loads(await request.aread())
        requests.append(body)
        if body.get("stream") is False:
            return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
        if body["model"] == "child-model":
            return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"debug child result"}}]}\n\ndata: [DONE]\n\n')
        if body["model"] == "parent-model" and body.get("tools"):
            delegate = next(tool for tool in body["tools"] if "delegate" in tool["function"]["description"].lower())
            call = {"index": 0, "id": "debug-delegate", "type": "function", "function": {
                "name": delegate["function"]["name"],
                "arguments": json.dumps({"agent_id": target_id, "task": "Research this topic"}),
            }}
            frame = {"choices": [{"delta": {"tool_calls": [call]}}]}
            return httpx.Response(200, text="data: " + json.dumps(frame) + "\n\ndata: [DONE]\n\n")
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"debug done"}}]}\n\ndata: [DONE]\n\n')

    app = create_app(tmp_path / "debug-delegation.sqlite3", data_dir=tmp_path / "data", secret_store=Secrets(), http_transport=httpx.MockTransport(provider))
    with TestClient(app) as client:
        target = create_test_agent(client, {"name": "Debug Child", "model": "child-model"}).json()
        target_id = target["id"]
        parent = create_test_agent(client, {"name": "Debug Parent", "model": "parent-model", "selected_agents": [target_id]}).json()
        response = client.post(f"/api/agents/{parent['id']}/debug", json={
            "config": {"model_config_id": parent["model_config_id"], "selected_agents": [target_id]},
            "messages": [{"role": "user", "content": "delegate this"}],
        })

    assert response.status_code == 200
    assert any(call["model"] == "child-model" for call in requests)
    assert '"event_type":"a2a_task","status":"completed"' in response.text


def test_agent_delegation_rejects_target_not_in_allowlist_before_model_call(tmp_path):
    requests = []

    async def provider(request):
        body = json.loads(await request.aread())
        requests.append(body)
        if body.get("stream") is False:
            return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
        if "tools" in body and body["tools"]:
            call = {"index": 0, "id": "delegate-1", "type": "function", "function": {
                "name": body["tools"][0]["function"]["name"],
                "arguments": json.dumps({"agent_id": outsider_id, "task": "unapproved"}),
            }}
            return httpx.Response(200, text=f'data: {json.dumps({"choices": [{"delta": {"tool_calls": [call]}}]})}\n\ndata: [DONE]\n\n')
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"done"}}]}\n\ndata: [DONE]\n\n')

    app = create_app(tmp_path / "denied-delegation.sqlite3", data_dir=tmp_path / "data", secret_store=Secrets(), http_transport=httpx.MockTransport(provider))
    with TestClient(app) as client:
        outsider = create_test_agent(client, {"name": "Outsider", "model": "outsider-model"}).json()
        outsider_id = outsider["id"]
        parent = create_test_agent(client, {"name": "No access", "model": "parent-model"}).json()
        session = client.post("/api/sessions", json={"agent_id": parent["id"]}).json()
        response = client.post(f"/api/sessions/{session['id']}/messages", json={"content": "try"})

    assert response.status_code == 200
    assert all(request["model"] != "outsider-model" for request in requests)
