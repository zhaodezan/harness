from uuid import uuid4

from fastapi.testclient import TestClient

from backend.app.db import database_connection
from backend.app.main import create_app
from model_test_utils import create_test_agent


def seed_user_message(database_path, session_id, content):
    with database_connection(database_path) as connection:
        connection.execute(
            "INSERT INTO messages(id, session_id, role, content) VALUES (?, ?, 'user', ?)",
            (uuid4().hex, session_id, content),
        )


def test_session_search_finds_matching_message(tmp_path):
    database_path = tmp_path / "search.sqlite3"
    with TestClient(create_app(database_path)) as client:
        agent = create_test_agent(client, {"name": "Helper"}).json()
        session = client.post("/api/sessions", json={"agent_id": agent["id"]}).json()
        seed_user_message(database_path, session["id"], "收集关于海洋保护的资料")
        results = client.get("/api/sessions/search?q=海洋保护").json()

    assert len(results) == 1
    assert results[0]["session_id"] == session["id"]
    assert results[0]["matched_message"] == "收集关于海洋保护的资料"


def test_branch_copies_history_without_replaying_tools(tmp_path):
    database_path = tmp_path / "branch.sqlite3"
    with TestClient(create_app(database_path)) as client:
        agent = create_test_agent(client, {"name": "Helper"}).json()
        session = client.post("/api/sessions", json={"agent_id": agent["id"]}).json()
        seed_user_message(database_path, session["id"], "原始问题")
        original_message = client.get(f"/api/sessions/{session['id']}/messages").json()[0]
        branch = client.post(
            f"/api/sessions/{session['id']}/branch",
            json={"message_id": original_message["id"], "content": "修改后的问题"},
        )
        assert branch.status_code == 201
        source = client.get(f"/api/sessions/{session['id']}/messages").json()
        fork = client.get(f"/api/sessions/{branch.json()['id']}/messages").json()

    assert [item["content"] for item in source] == ["原始问题"]
    assert [item["content"] for item in fork] == ["修改后的问题"]
