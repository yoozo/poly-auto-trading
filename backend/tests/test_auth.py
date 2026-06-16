import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.core.config import settings
from app.db.session import get_session
from app.main import create_app
from conftest import TEST_AUTH_PASSWORD


def make_client() -> TestClient:
    app = create_app(enable_lifespan=False)

    async def fake_session():
        yield object()

    app.dependency_overrides[get_session] = fake_session
    return TestClient(app)


def test_protected_api_requires_session() -> None:
    client = make_client()
    response = client.get("/api/status/services")

    assert response.status_code == 401
    assert response.json()["detail"] == "not authenticated"


def test_health_and_session_are_public() -> None:
    client = make_client()

    assert client.get("/api/health").status_code == 200
    response = client.get("/api/auth/session")

    assert response.status_code == 200
    assert response.json() == {"authenticated": False, "configured": True}


def test_login_rejects_invalid_password() -> None:
    client = make_client()
    response = client.post("/api/auth/login", json={"password": "wrong"})

    assert response.status_code == 401


def test_login_allows_protected_api_and_session_check() -> None:
    client = make_client()
    login = client.post("/api/auth/login", json={"password": TEST_AUTH_PASSWORD})

    assert login.status_code == 200
    assert settings.auth_cookie_name in client.cookies
    assert client.get("/api/auth/session").json()["authenticated"] is True
    assert client.get("/api/status/services").status_code == 200


def test_logout_clears_session() -> None:
    client = make_client()
    assert client.post("/api/auth/login", json={"password": TEST_AUTH_PASSWORD}).status_code == 200

    logout = client.post("/api/auth/logout")

    assert logout.status_code == 200
    assert client.get("/api/auth/session").json()["authenticated"] is False
    assert client.get("/api/status/services").status_code == 401


def test_missing_auth_config_blocks_protected_api(monkeypatch) -> None:
    monkeypatch.setattr(settings, "auth_password", "")
    client = make_client()

    response = client.get("/api/status/services")

    assert response.status_code == 503
    assert response.json()["detail"] == "authentication is not configured"


def test_websocket_requires_session() -> None:
    client = make_client()

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/ws/market?interval=1m"):
            pass


def test_websocket_accepts_authenticated_session() -> None:
    client = make_client()
    assert client.post("/api/auth/login", json={"password": TEST_AUTH_PASSWORD}).status_code == 200

    with client.websocket_connect("/api/ws/market?interval=1m") as websocket:
        websocket.close()
