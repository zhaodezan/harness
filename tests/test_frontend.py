from fastapi.testclient import TestClient
import pytest

from backend.app.main import create_app


def test_frontend_root_serves_built_app(tmp_path):
    response = TestClient(create_app(tmp_path / "web.sqlite3")).get("/")

    assert response.status_code == 200
    assert '<div id="root"></div>' in response.text
    assert "前端尚未构建" not in response.text


@pytest.mark.parametrize(
    "path",
    ["/chat", "/agents", "/agents/new", "/agents/example-id/config", "/agents/example-id/chat", "/plugins"],
)
def test_frontend_routes_serve_built_app(tmp_path, path):
    response = TestClient(create_app(tmp_path / "web.sqlite3")).get(path)

    assert response.status_code == 200
    assert '<div id="root"></div>' in response.text
    assert "前端尚未构建" not in response.text


def test_unknown_api_route_remains_404(tmp_path):
    response = TestClient(create_app(tmp_path / "web.sqlite3")).get("/api/not-a-route")

    assert response.status_code == 404
