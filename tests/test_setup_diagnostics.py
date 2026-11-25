from fastapi.testclient import TestClient

from backend.app.main import create_app


def test_setup_diagnostics_reports_readiness_without_exposing_configuration(tmp_path):
    with TestClient(create_app(tmp_path / "diagnostics.sqlite3")) as client:
        response = client.get("/api/setup/diagnostics")

    assert response.status_code == 200
    checks = response.json()["checks"]
    assert {item["name"] for item in checks} >= {"database", "frontend", "credentials"}
    assert all("path" not in item for item in checks)
