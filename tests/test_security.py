from fastapi.testclient import TestClient

from backend.app.main import create_app


def test_rejects_same_hostname_but_different_origin_port(tmp_path):
    with TestClient(create_app(tmp_path / "security.sqlite3"), base_url="http://127.0.0.1:8765") as client:
        response = client.get("/api/health", headers={"origin": "http://127.0.0.1:9999"})

    assert response.status_code == 403
