import httpx
import pytest

from app.services import polymarket_status


def status_payload(*, page_status="OPERATIONAL", incidents=None):
    return {
        "page": {"name": "Polymarket", "status": page_status},
        "activeIncidents": incidents or [],
    }


def incident_payload():
    return {
        "id": "incident-1",
        "name": "Trading issue",
        "started": "2026-07-12T07:23:37.076Z",
        "status": "INVESTIGATING",
        "impact": "PARTIALOUTAGE",
        "url": "https://status.polymarket.com/incident-1",
        "updatedAt": "2026-07-12T07:23:38.085Z",
    }


def test_normalize_operational_status() -> None:
    result = polymarket_status.normalize_status_payload(status_payload())

    assert result.healthy is True
    assert result.page_status == "OPERATIONAL"
    assert result.active_incidents == []


@pytest.mark.parametrize("page_status", ["HASISSUES", "UNDERMAINTENANCE"])
def test_normalize_non_operational_status(page_status) -> None:
    result = polymarket_status.normalize_status_payload(status_payload(page_status=page_status))

    assert result.healthy is False
    assert result.page_status == page_status


def test_active_incident_makes_status_unhealthy() -> None:
    result = polymarket_status.normalize_status_payload(
        status_payload(incidents=[incident_payload()])
    )

    assert result.healthy is False
    assert result.active_incidents[0].name == "Trading issue"
    assert result.active_incidents[0].updated_at == "2026-07-12T07:23:38.085Z"


@pytest.mark.asyncio
async def test_fetch_failure_returns_unhealthy_status(monkeypatch) -> None:
    async def fake_fetch_status_payload():
        raise httpx.TimeoutException("status timeout")

    monkeypatch.setattr(polymarket_status, "fetch_status_payload", fake_fetch_status_payload)

    result = await polymarket_status.get_polymarket_status()

    assert result.healthy is False
    assert "status timeout" in (result.error or "")


def test_invalid_payload_raises() -> None:
    with pytest.raises(ValueError, match="missing page.status"):
        polymarket_status.normalize_status_payload({})
