from fastapi.testclient import TestClient

from app.main import create_app


def test_health() -> None:
    client = TestClient(create_app(enable_lifespan=False))
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] in {"ok", "degraded"}
    assert body["checks"]["api"]["ok"] is True


def test_services_status() -> None:
    client = TestClient(create_app(enable_lifespan=False))
    response = client.get("/api/status/services")
    assert response.status_code == 200
    services = response.json()
    assert any(service["name"] == "api" for service in services)
