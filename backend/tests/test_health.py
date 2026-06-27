from fastapi.testclient import TestClient

from app.db.session import get_session
from app.main import create_app
from app.api import routes_status
from app.schemas.status import ServiceEventRecord
from datetime import datetime, timezone
from conftest import login_test_client


def make_client() -> TestClient:
    app = create_app(enable_lifespan=False)

    async def fake_session():
        yield object()

    app.dependency_overrides[get_session] = fake_session
    client = TestClient(app)
    login_test_client(client)
    return client


def test_health() -> None:
    client = make_client()
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.headers["X-Request-ID"]
    body = response.json()
    assert body["status"] in {"ok", "degraded"}
    assert body["checks"]["api"]["ok"] is True


def test_services_status() -> None:
    client = make_client()
    response = client.get("/api/status/services")
    assert response.status_code == 200
    services = response.json()
    assert any(service["name"] == "api" for service in services)


def test_service_events_status(monkeypatch) -> None:
    async def fake_list_service_events(session, **kwargs):
        assert kwargs["limit"] == 10
        return [
            ServiceEventRecord(
                id=1,
                service="binance_rest",
                level="error",
                message="failed",
                payload={"endpoint": "test"},
                created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )
        ]

    monkeypatch.setattr(routes_status, "list_service_events", fake_list_service_events)

    client = make_client()
    response = client.get("/api/status/events?limit=10")

    assert response.status_code == 200
    assert response.json()[0]["service"] == "binance_rest"
