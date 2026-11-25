from fastapi.testclient import TestClient

from backend.app.main import create_app


def test_startup_wizard_completes_without_secrets_and_connects_model(tmp_path):
    async def transport(request):
        import json
        payload = json.loads(await request.aread())
        if payload.get("stream") is False:
            return __import__("httpx").Response(200, json={"choices": [{"message": {"content": "连接成功"}}]})
        return __import__("httpx").Response(200, text='data: {"choices":[{"delta":{"content":"hello"}}]}\n\ndata: [DONE]\n\n')
    class Secrets:
        def __init__(self): self.values = {}
        def get(self, name): return self.values.get(name)
        def set(self, name, value): self.values[name] = value
        def delete(self, name): self.values.pop(name, None)
    app = create_app(tmp_path / "wizard.sqlite3", http_transport=__import__("httpx").MockTransport(transport), secret_store=Secrets())
    with TestClient(app) as client:
        assert client.get("/api/setup/status").json()["completed"] is False
        model = client.post("/api/models", json={
            "name": "Default model", "base_url": "https://example.test/v1", "model": "m", "api_key": "wizard-key",
        }).json()
        tested = client.post(f"/api/models/{model['id']}/test-connection")
        assert tested.status_code == 200
        setup = client.post("/api/setup/model", json={"name": "Local helper", "model_config_id": model["id"], "mode": "react", "selected_skills": ["summarize"], "selected_mcp_tools": ["mcp:srv:search"], "selected_local_tools": ["tool-1"], "input_rate_per_million": 2, "output_rate_per_million": 3})
        assert setup.status_code == 201
        assert client.get("/api/setup/status").json()["completed"] is True
        agents = client.get("/api/agents").json()
        assert agents[0]["name"] == "Local helper"
        assert agents[0]["selected_skills"] == ["summarize"]
        assert agents[0]["selected_mcp_tools"] == ["mcp:srv:search"]
        assert agents[0]["selected_local_tools"] == ["tool-1"]
        assert agents[0]["input_rate_per_million"] is None
        assert agents[0]["output_rate_per_million"] is None
        assert "wizard-key" not in setup.text
