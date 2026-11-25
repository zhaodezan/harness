import json

from fastapi.testclient import TestClient

from backend.app.db import database_connection
from backend.app.main import create_app


def test_knowledge_base_crud_upload_search_and_delete(tmp_path):
    database = tmp_path / "knowledge.sqlite3"
    data_dir = tmp_path / "data"
    with TestClient(create_app(database, data_dir=data_dir)) as client:
        created = client.post("/api/knowledge-bases", json={"name": "Project"})
        assert created.status_code == 201
        knowledge_base = created.json()
        knowledge_base_id = knowledge_base["id"]

        uploaded = client.post(
            f"/api/knowledge-bases/{knowledge_base_id}/documents",
            files=[("files", ("guide.md", b"Docker deployment instructions", "text/markdown"))],
        )
        assert uploaded.status_code == 201
        document = uploaded.json()[0]
        assert document["status"] == "ready"
        assert document["filename"] == "guide.md"
        assert "extracted_text" not in document
        stored_files = list((data_dir / "knowledge-bases" / knowledge_base_id / "files").iterdir())
        assert len(stored_files) == 1
        assert stored_files[0].name.endswith(".md")
        assert "guide" not in stored_files[0].name

        search = client.post(
            f"/api/knowledge-bases/{knowledge_base_id}/search",
            json={"query": "Docker deployment", "limit": 5},
        )
        assert search.status_code == 200
        assert search.json()[0]["filename"] == "guide.md"
        assert "Docker" in search.json()[0]["snippet"]
        listed = client.get(f"/api/knowledge-bases/{knowledge_base_id}/documents")
        assert listed.json()[0]["status"] == "ready"

        renamed = client.patch(
            f"/api/knowledge-bases/{knowledge_base_id}", json={"name": "Renamed"}
        )
        assert renamed.json()["name"] == "Renamed"
        deleted = client.delete(
            f"/api/knowledge-bases/{knowledge_base_id}/documents/{document['id']}"
        )
        assert deleted.status_code == 204
        assert not stored_files[0].exists()
        assert client.delete(f"/api/knowledge-bases/{knowledge_base_id}").status_code == 204


def test_failed_upload_is_listed_without_storing_file_and_reference_blocks_delete(tmp_path):
    database = tmp_path / "knowledge.sqlite3"
    data_dir = tmp_path / "data"
    with TestClient(create_app(database, data_dir=data_dir)) as client:
        kb = client.post("/api/knowledge-bases", json={"name": "Docs"}).json()
        response = client.post(
            f"/api/knowledge-bases/{kb['id']}/documents",
            files=[("files", ("broken.json", b"{", "application/json"))],
        )
        assert response.status_code == 201
        doc = response.json()[0]
        assert doc["status"] == "failed"
        assert doc["error"]
        assert list((data_dir / "knowledge-bases" / kb["id"] / "files").iterdir()) == []

        with database_connection(database) as connection:
            connection.execute(
                "INSERT INTO agents(id,name,selected_knowledge_bases_json) VALUES(?,?,?)",
                ("agent-1", "Research Agent", json.dumps([kb["id"]])),
            )
        deletion = client.delete(f"/api/knowledge-bases/{kb['id']}")
        assert deletion.status_code == 409
        assert "Research Agent" in deletion.text


def test_upload_limit_and_manual_search_do_not_mutate_chat_or_memories(tmp_path):
    database = tmp_path / "knowledge.sqlite3"
    with TestClient(create_app(database, data_dir=tmp_path / "data")) as client:
        kb = client.post("/api/knowledge-bases", json={"name": "Notes"}).json()
        too_large = client.post(
            f"/api/knowledge-bases/{kb['id']}/documents",
            files=[("files", ("large.txt", b"x" * (20 * 1024 * 1024 + 1), "text/plain"))],
        )
        assert too_large.status_code == 413
        search = client.post(
            f"/api/knowledge-bases/{kb['id']}/search",
            json={"query": "nothing", "limit": 5},
        )
        assert search.status_code == 200
        assert search.json() == []
        with database_connection(database) as connection:
            assert connection.execute("SELECT count(*) FROM messages").fetchone()[0] == 0
            assert connection.execute("SELECT count(*) FROM memories").fetchone()[0] == 0


def test_knowledge_base_routes_reject_unknown_ids_and_invalid_names(tmp_path):
    with TestClient(create_app(tmp_path / "knowledge.sqlite3", data_dir=tmp_path / "data")) as client:
        assert client.get("/api/knowledge-bases/missing").status_code == 404
        assert client.post("/api/knowledge-bases", json={"name": " "}).status_code == 422
        assert client.post(
            "/api/knowledge-bases/missing/search", json={"query": "query"}
        ).status_code == 404


def test_upload_rejects_path_components_in_filename(tmp_path):
    with TestClient(create_app(tmp_path / "knowledge.sqlite3", data_dir=tmp_path / "data")) as client:
        kb = client.post("/api/knowledge-bases", json={"name": "Safe"}).json()
        response = client.post(
            f"/api/knowledge-bases/{kb['id']}/documents",
            files=[("files", ("../escape.txt", b"text", "text/plain"))],
        )
    assert response.status_code == 422
