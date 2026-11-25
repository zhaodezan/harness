import httpx
from fastapi.testclient import TestClient

from backend.app.main import create_app
from model_test_utils import create_test_agent


def test_prompt_preview_renders_only_local_template_without_model_call(tmp_path):
    calls = []
    async def provider(request: httpx.Request):
        calls.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
    app = create_app(tmp_path / "preview.sqlite3", http_transport=httpx.MockTransport(provider))
    with TestClient(app) as client:
        agent = create_test_agent(client, {"name": "Greeting", "system_prompt": "Hello {{ user_name }}"}).json()
        response = client.post(f"/api/agents/{agent['id']}/prompts/preview", json={"variables": {"user_name": "Mika"}})

    assert response.status_code == 200
    assert response.json()["rendered_prompt"] == "Hello Mika"
    assert response.json()["model_called"] is False
    assert calls == []
