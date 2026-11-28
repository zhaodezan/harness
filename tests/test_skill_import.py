import io
import zipfile
from contextlib import contextmanager

from fastapi.testclient import TestClient

from backend.app.main import create_app
from model_test_utils import create_test_agent


VALID_SKILL = "---\nname: summarize\ndescription: Summarize documents\n---\n\nUse concise bullets."


def make_zip(entries: dict[str, str]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return output.getvalue()


@contextmanager
def skill_client(tmp_path):
    data_dir = tmp_path / "app-data"
    app = create_app(tmp_path / "skills.sqlite3", data_dir=data_dir)
    with TestClient(app) as client:
        yield client, data_dir


def test_imports_valid_skill_zip_into_managed_library(tmp_path):
    archive = make_zip({"summarize/SKILL.md": VALID_SKILL})
    with skill_client(tmp_path) as (client, _data_dir):
        response = client.post(
            "/api/skills/import",
            files={"file": ("summarize.zip", archive, "application/zip")},
        )
        listed = client.get("/api/skills").json()

    assert response.status_code == 201
    assert response.json()["skills"][0]["name"] == "summarize"
    assert [skill["name"] for skill in listed] == ["summarize"]
    assert listed[0]["description"] == "Summarize documents"


def test_import_rejects_zip_slip_without_writing_outside_data_dir(tmp_path):
    archive = make_zip({"../escape/SKILL.md": VALID_SKILL})
    with skill_client(tmp_path) as (client, data_dir):
        response = client.post(
            "/api/skills/import",
            files={"file": ("bad.zip", archive, "application/zip")},
        )
        listed = client.get("/api/skills").json()

    assert response.status_code == 400
    assert listed == []
    assert not (data_dir.parent / "escape" / "SKILL.md").exists()


def test_import_rejects_invalid_skill_and_leaves_library_empty(tmp_path):
    archive = make_zip({"broken/SKILL.md": "missing required frontmatter"})
    with skill_client(tmp_path) as (client, _data_dir):
        response = client.post(
            "/api/skills/import",
            files={"file": ("broken.zip", archive, "application/zip")},
        )
        listed = client.get("/api/skills").json()

    assert response.status_code == 400
    assert listed == []


def test_import_rejects_duplicate_skill_name(tmp_path):
    archive = make_zip({"summarize/SKILL.md": VALID_SKILL})
    with skill_client(tmp_path) as (client, _data_dir):
        first = client.post(
            "/api/skills/import",
            files={"file": ("first.zip", archive, "application/zip")},
        )
        duplicate = client.post(
            "/api/skills/import",
            files={"file": ("duplicate.zip", archive, "application/zip")},
        )
        listed = client.get("/api/skills").json()

    assert first.status_code == 201
    assert duplicate.status_code == 409
    assert [skill["name"] for skill in listed] == ["summarize"]


def test_import_rejects_corrupt_zip(tmp_path):
    with skill_client(tmp_path) as (client, _data_dir):
        response = client.post(
            "/api/skills/import",
            files={"file": ("broken.zip", b"not a zip", "application/zip")},
        )
    assert response.status_code == 400


def test_delete_skill_removes_only_its_agent_selections(tmp_path):
    archive = make_zip({"summarize/SKILL.md": VALID_SKILL})
    with skill_client(tmp_path) as (client, _data_dir):
        files = {"file": ("skill.zip", archive, "application/zip")}
        assert client.post("/api/skills/import", files=files).status_code == 201
        agents = [create_test_agent(client, {"name": name, "selected_skills": selected}).json()
                  for name, selected in [("First", ["other", "summarize", "tail"]),
                                         ("Second", ["summarize"]), ("Unrelated", ["other"])]]
        response = client.delete("/api/skills/summarize")
        assert response.status_code == 204, response.text
        assert client.get("/api/skills").json() == []
        assert client.post("/api/skills/import", files=files).status_code == 201
        restored = {agent["id"]: agent["selected_skills"] for agent in client.get("/api/agents").json()}
        assert restored == {agents[0]["id"]: ["other", "tail"], agents[1]["id"]: [], agents[2]["id"]: ["other"]}


def test_deleting_missing_skill_does_not_change_agent_selections(tmp_path):
    with skill_client(tmp_path) as (client, _):
        agent = create_test_agent(client, {"name": "Agent", "selected_skills": ["summarize"]}).json()
        assert client.delete("/api/skills/summarize").status_code == 404
        assert client.get(f"/api/agents/{agent['id']}").json()["selected_skills"] == ["summarize"]


def test_skill_removal_failure_keeps_agent_selections(tmp_path, monkeypatch):
    def fail_delete(_name):
        raise PermissionError("cannot remove directory")

    with skill_client(tmp_path) as (client, _):
        files = {"file": ("skill.zip", make_zip({"summarize/SKILL.md": VALID_SKILL}))}
        assert client.post("/api/skills/import", files=files).status_code == 201
        agent = create_test_agent(client, {"name": "Agent", "selected_skills": ["summarize"]}).json()
        monkeypatch.setattr(client.app.state.plugin_store, "delete_skill", fail_delete)
        assert client.delete("/api/skills/summarize").status_code == 500
        assert client.get(f"/api/agents/{agent['id']}").json()["selected_skills"] == ["summarize"]


def test_import_rejects_expanded_archive_over_ten_megabytes(tmp_path):
    archive = make_zip({"large/SKILL.md": "x" * (10 * 1024 * 1024 + 1)})
    with skill_client(tmp_path) as (client, _data_dir):
        response = client.post(
            "/api/skills/import",
            files={"file": ("large.zip", archive, "application/zip")},
        )

    assert response.status_code == 413
