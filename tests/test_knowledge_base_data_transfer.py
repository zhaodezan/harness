from fastapi.testclient import TestClient

from backend.app.db import database_connection
from backend.app.main import create_app


def test_backup_exports_knowledge_text_without_filesystem_paths_and_restores_ids(tmp_path):
    database = tmp_path / "transfer.sqlite3"
    with TestClient(create_app(database, data_dir=tmp_path / "data")) as client:
        kb = client.post("/api/knowledge-bases", json={"name": "Research"}).json()
        document = client.post(
            f"/api/knowledge-bases/{kb['id']}/documents",
            files=[("files", ("facts.md", b"A confidential searchable fact", "text/markdown"))],
        ).json()[0]
        exported = client.get("/api/data/export").json()

        assert exported["knowledge_bases"][0]["id"] == kb["id"]
        assert "stored_path" not in exported["knowledge_documents"][0]
        assert exported["knowledge_documents"][0]["extracted_text"] == "A confidential searchable fact"
        assert exported["format_version"] == 2

        imported = client.post("/api/data/import", json={
            **exported,
            "agents": [], "sessions": [], "messages": [],
        })
        assert imported.status_code == 200
        imported_id = imported.json()["knowledge_bases"][0]["id"]
        assert imported_id != kb["id"]
        assert client.post(
            f"/api/knowledge-bases/{imported_id}/search",
            json={"query": "confidential searchable", "limit": 5},
        ).json()[0]["snippet"] == "A confidential searchable fact"
        with database_connection(database) as connection:
            restored = connection.execute(
                "SELECT stored_path FROM knowledge_documents WHERE knowledge_base_id=?",
                (imported_id,),
            ).fetchone()
        assert restored["stored_path"] == ""


def test_backup_maps_agent_knowledge_authorization_and_legacy_backup_defaults_empty(tmp_path):
    database = tmp_path / "legacy-transfer.sqlite3"
    with TestClient(create_app(database, data_dir=tmp_path / "data")) as client:
        kb = client.post("/api/knowledge-bases", json={"name": "Docs"}).json()
        exported = client.get("/api/data/export").json()
        exported["agents"] = [{
            "id": "old-agent", "name": "Assistant", "model_config_id": "old-model",
            "selected_knowledge_bases": [kb["id"]],
        }]
        exported["models"] = [{
            "id": "old-model", "name": "Test", "base_url": "https://example.test/v1", "model": "test",
        }]
        imported = client.post("/api/data/import", json=exported)
        assert imported.status_code == 200
        agent = client.get("/api/agents").json()[0]
        assert agent["selected_knowledge_bases"] == [imported.json()["knowledge_bases"][0]["id"]]

        legacy_payload = {
            "format_version": 1, "agents": [], "sessions": [], "messages": [],
        }
        old_import = client.post("/api/data/import", json=legacy_payload)
        assert old_import.status_code == 200
        assert old_import.json()["knowledge_bases_imported"] == 0


def test_import_rejects_dangling_knowledge_authorization_and_untrusted_paths(tmp_path):
    with TestClient(create_app(tmp_path / "transfer.sqlite3", data_dir=tmp_path / "data")) as client:
        base = {
            "format_version": 2, "models": [{
                "id": "model-old", "name": "Test", "base_url": "https://example.test/v1", "model": "test",
            }],
            "agents": [{
                "id": "agent-old", "name": "Assistant", "model_config_id": "model-old",
                "selected_knowledge_bases": ["missing-kb"],
            }],
            "sessions": [], "messages": [],
            "knowledge_bases": [{"id": "kb-old", "name": "Safe"}],
            "knowledge_documents": [{
                "id": "doc-old", "knowledge_base_id": "kb-old", "filename": "facts.md",
                "extension": ".md", "stored_path": "../../outside", "extracted_text": "facts",
                "char_count": 5, "status": "ready",
            }],
        }
        bad_reference = client.post("/api/data/import", json=base)
        assert bad_reference.status_code == 422
        base["agents"][0]["selected_knowledge_bases"] = ["kb-old"]
        restored = client.post("/api/data/import", json=base)
        assert restored.status_code == 200
        with database_connection(client.app.state.database_path) as connection:
            row = connection.execute("SELECT stored_path FROM knowledge_documents").fetchone()
        assert row["stored_path"] == ""
