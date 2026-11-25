from fastapi.testclient import TestClient

from backend.app.main import create_app


TOOL_SOURCE = '''TOOL = {
    "name": "lookup",
    "description": "Lookup a value",
    "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    "risk": "read_only",
}
def run(arguments):
    return "ok"
'''


def test_local_tools_can_be_installed_listed_and_deleted(tmp_path):
    app = create_app(tmp_path / "tools.sqlite3", data_dir=tmp_path / "data")
    with TestClient(app) as client:
        imported = client.post("/api/tools/import", files={"file": ("lookup.py", TOOL_SOURCE, "text/x-python")})
        listed = client.get("/api/tools").json()
        tool_id = imported.json()["id"]
        deleted = client.delete(f"/api/tools/{tool_id}")
        final_list = client.get("/api/tools").json()

    assert imported.status_code == 201
    assert imported.json()["name"] == "lookup"
    assert listed[0]["id"] == tool_id
    assert deleted.status_code == 204
    assert final_list == []


def test_local_tool_source_must_be_python_and_valid_contract(tmp_path):
    app = create_app(tmp_path / "tools.sqlite3", data_dir=tmp_path / "data")
    with TestClient(app) as client:
        response = client.post("/api/tools/import", files={"file": ("bad.txt", "not python", "text/plain")})

    assert response.status_code == 400
