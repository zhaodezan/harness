from uuid import uuid4

from backend.app.db import database_connection


def create_test_model(client_or_app, *, base_url="https://example.test/v1", model="test-model", api_key="test-key", name=None):
    app = getattr(client_or_app, "app", client_or_app)
    model_id = uuid4().hex
    with database_connection(app.state.database_path) as connection:
        connection.execute(
            "INSERT INTO model_configs(id,name,base_url,model,credential_migrated,last_tested_at) VALUES(?,?,?,?,1,CURRENT_TIMESTAMP)",
            (model_id, name or f"Test Model {model_id[:8]}", base_url, model),
        )
    try:
        app.state.secret_store.set(f"model:{model_id}:api_key", api_key)
    except Exception:
        pass
    return model_id


def create_test_agent(client, payload):
    values = dict(payload)
    model_config_id = values.pop("model_config_id", None)
    base_url = values.pop("base_url", "https://example.test/v1")
    model = values.pop("model", "test-model")
    if model_config_id is None:
        model_config_id = create_test_model(
            client, base_url=base_url, model=model,
            api_key=getattr(client.app.state, "test_api_key", "test-key"),
        )
    values["model_config_id"] = model_config_id
    return client.post("/api/agents", json=values)


async def create_test_agent_async(client, app, payload):
    values = dict(payload)
    model_config_id = values.pop("model_config_id", None)
    base_url = values.pop("base_url", "https://example.test/v1")
    model = values.pop("model", "test-model")
    if model_config_id is None:
        model_config_id = create_test_model(app, base_url=base_url, model=model)
    values["model_config_id"] = model_config_id
    return await client.post("/api/agents", json=values)
