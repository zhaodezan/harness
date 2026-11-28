from __future__ import annotations

import uuid
from pathlib import Path

from backend.app.db import database_connection
from backend.app.secrets import SecretStore


def migrate_legacy_models(database_path: str | Path, secret_store: SecretStore) -> list[str]:
    warnings: list[str] = []
    with database_connection(database_path) as connection:
        agents = connection.execute(
            # A cleared binding is intentional, not a pending legacy migration.
            "SELECT id,name,base_url,model,model_config_id FROM agents WHERE model_binding_cleared=0 ORDER BY rowid"
        ).fetchall()
        for agent in agents:
            legacy = connection.execute(
                "SELECT * FROM model_configs WHERE legacy_agent_id = ?", (agent["id"],)
            ).fetchone()
            if legacy is None and agent["model_config_id"] is None:
                model_id = uuid.uuid4().hex
                display_model = agent["model"] or "Model"
                name = f"{agent['name']} · {display_model}"[:100]
                connection.execute(
                    "INSERT INTO model_configs(id,name,base_url,model,legacy_agent_id,credential_migrated) "
                    "VALUES(?,?,?,?,?,0)",
                    (model_id, name, agent["base_url"] or "", agent["model"] or "", agent["id"]),
                )
                connection.execute(
                    "UPDATE agents SET model_config_id = ? WHERE id = ? AND model_config_id IS NULL",
                    (model_id, agent["id"]),
                )
                legacy = connection.execute(
                    "SELECT * FROM model_configs WHERE id = ?", (model_id,)
                ).fetchone()
            elif legacy is not None and agent["model_config_id"] is None:
                connection.execute(
                    "UPDATE agents SET model_config_id = ? WHERE id = ?",
                    (legacy["id"], agent["id"]),
                )

            if legacy is None or legacy["credential_migrated"]:
                continue
            if not legacy["base_url"] or not legacy["model"]:
                warnings.append(f"{agent['name']}：旧模型地址或模型标识为空；已保留配置，请在 Model 页修复。")
                continue
            old_key_name = f"agent:{agent['id']}:api_key"
            try:
                old_key = secret_store.get(old_key_name)
            except Exception:
                warnings.append(f"{agent['name']}：无法读取系统凭据库中的旧密钥；旧 Agent 配置已保留，可稍后重试迁移。")
                continue
            if not old_key:
                connection.execute(
                    "UPDATE model_configs SET credential_migrated = 1 WHERE id = ?", (legacy["id"],)
                )
                warnings.append(f"{agent['name']}：旧模型没有保存 API 密钥；请在 Model 页配置凭据。")
                continue
            try:
                secret_store.set(f"model:{legacy['id']}:api_key", old_key)
            except Exception:
                warnings.append(f"{agent['name']}：无法迁移旧模型密钥；旧密钥保留，可稍后重试。")
                continue
            connection.execute(
                "UPDATE model_configs SET credential_migrated = 1 WHERE id = ?", (legacy["id"],)
            )
            try:
                secret_store.delete(old_key_name)
            except Exception:
                warnings.append(f"{agent['name']}：新密钥已保存，但无法清理旧凭据项；旧项未被用于运行。")
    return warnings
