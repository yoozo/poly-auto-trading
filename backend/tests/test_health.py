from fastapi.testclient import TestClient

from app.db.session import get_session
from app.main import create_app


def make_client() -> TestClient:
    app = create_app(enable_lifespan=False)

    async def fake_session():
        yield object()

    app.dependency_overrides[get_session] = fake_session
    return TestClient(app)


def test_health() -> None:
    client = make_client()
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] in {"ok", "degraded"}
    assert body["checks"]["api"]["ok"] is True


def test_services_status() -> None:
    client = make_client()
    response = client.get("/api/status/services")
    assert response.status_code == 200
    services = response.json()
    assert any(service["name"] == "api" for service in services)
