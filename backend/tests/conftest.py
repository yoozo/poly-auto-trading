import pytest

from app.core.config import settings

TEST_AUTH_PASSWORD = "test-password"


@pytest.fixture(autouse=True)
def configure_test_auth(monkeypatch):
    monkeypatch.setattr(settings, "auth_password", TEST_AUTH_PASSWORD)
    monkeypatch.setattr(settings, "auth_session_secret", "test-session-secret")
    monkeypatch.setattr(settings, "auth_cookie_secure", False)


def login_test_client(client) -> None:
    response = client.post("/api/auth/login", json={"password": TEST_AUTH_PASSWORD})
    assert response.status_code == 200
