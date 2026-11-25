from fastapi.testclient import TestClient

from backend.app.main import create_app


class MemorySecrets:
    def __init__(self): self.values = {}
    def get(self, name): return self.values.get(name)
    def set(self, name, value): self.values[name] = value
    def delete(self, name): self.values.pop(name, None)


def test_v2_backup_exports_model_profiles_without_secrets_and_remaps_reference(tmp_path):
    secrets = MemorySecrets()
    async def provider(request):
        if request.url.host == "example.test":
            return __import__("httpx").Response(200, json={"choices": [{"message": {"content": "ok"}}]})
        raise AssertionError(f"Unexpected model host: {request.url.host}")
    source = create_app(
        tmp_path / "source.sqlite3", data_dir=tmp_path / "source-data", secret_store=secrets,
        http_transport=__import__("httpx").MockTransport(provider),
    )
    with TestClient(source) as client:
        model = client.post("/api/models", json={
            "name": "Shared", "base_url": "https://example.test/v1", "model": "model-x", "api_key": "secret-value",
        }).json()
        with __import__("backend.app.db", fromlist=["database_connection"]).database_connection(source.state.database_path) as connection:
            connection.execute("UPDATE model_configs SET last_tested_at=CURRENT_TIMESTAMP WHERE id=?", (model["id"],))
        agent = client.post("/api/agents", json={"name": "Helper", "model_config_id": model["id"]}).json()
        exported = client.get("/api/data/export")
        payload = exported.json()

    assert payload["format_version"] == 2
    assert len(payload["models"]) == 1
    assert "secret-value" not in exported.text
    assert "api_key" not in payload["models"][0]

    target_secrets = MemorySecrets()
    target = create_app(tmp_path / "target.sqlite3", data_dir=tmp_path / "target-data", secret_store=target_secrets)
    with TestClient(target) as client:
        imported = client.post("/api/data/import", json=payload)
        models = client.get("/api/models").json()
        agents = client.get("/api/agents").json()

    assert imported.status_code == 200, imported.text
    assert len(models) == 1
    assert models[0]["id"] != model["id"]
    assert models[0]["credential_configured"] is False
    assert agents[0]["model_config_id"] == models[0]["id"]
    assert agents[0]["id"] != agent["id"]
    assert "凭据" in " ".join(imported.json()["warnings"])


def test_v1_backup_with_inline_agent_model_creates_model_profile(tmp_path):
    app = create_app(tmp_path / "legacy-import.sqlite3", data_dir=tmp_path / "data", secret_store=MemorySecrets())
    payload = {
        "format_version": 1,
        "secrets_included": False,
        "agents": [{
            "id": "legacy-agent", "name": "Old Agent", "base_url": "https://legacy.test/v1",
            "model": "legacy-model", "system_prompt": "hello", "selected_skills": [],
        }],
        "sessions": [], "messages": [], "memories": [], "prompt_versions": [], "mcp_servers": [],
    }
    with TestClient(app) as client:
        imported = client.post("/api/data/import", json=payload)
        agent = client.get("/api/agents").json()[0]
        model = client.get("/api/models").json()[0]

    assert imported.status_code == 200, imported.text
    assert agent["model_config_id"] == model["id"]
    assert model["base_url"] == "https://legacy.test/v1"
    assert model["model"] == "legacy-model"
    assert model["credential_configured"] is False
    assert "credential" in " ".join(imported.json()["warnings"]).lower() or "凭据" in " ".join(imported.json()["warnings"])


def test_v2_backup_rejects_agent_reference_to_missing_model(tmp_path):
    with TestClient(create_app(tmp_path / "invalid-import.sqlite3", data_dir=tmp_path / "data")) as client:
        response = client.post("/api/data/import", json={
            "format_version": 2, "secrets_included": False,
            "models": [],
            "agents": [{"id": "a", "name": "Bad", "model_config_id": "not-in-payload"}],
            "sessions": [], "messages": [], "memories": [], "prompt_versions": [], "mcp_servers": [],
        })
    assert response.status_code == 422
