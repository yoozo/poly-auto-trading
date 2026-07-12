import httpx
import pytest

from app.services import polymarket_status


def components_payload(*, status="OPERATIONAL", incidents=None, include_clob=True):
    components = [
        {"id": "website", "name": "Website", "status": "OPERATIONAL"},
    ]
    if include_clob:
        components.append(
            {
                "id": "clob",
                "name": "CLOB API",
                "status": status,
                "activeIncidents": incidents or [],
            }
        )
    return {"components": components}


def summary_payload(*, page_status="UP", incidents=None):
    return {
        "page": {"name": "Polymarket", "status": page_status},
        "activeIncidents": incidents or [],
    }


def incident_payload(*, incident_id="incident-1", updated=True):
    payload = {
        "id": incident_id,
        "name": "Trading issue",
        "started": "2026-07-12T07:23:37.076Z",
        "status": "INVESTIGATING",
        "impact": "PARTIALOUTAGE",
        "url": "https://status.polymarket.com/incident-1",
    }
    if updated:
        payload["updatedAt"] = "2026-07-12T07:23:38.085Z"
    return payload


def normalize(components=None, summary=None):
    return polymarket_status.normalize_status_payload(
        components or components_payload(),
        summary or summary_payload(),
    )


def test_clob_operational_is_healthy() -> None:
    result = normalize()

    assert result.healthy is True
    assert result.component_name == "CLOB API"
    assert result.component_status == "OPERATIONAL"
    assert result.summary_page_status == "UP"
    assert result.active_incidents == []


@pytest.mark.parametrize("status", ["DEGRADEDPERFORMANCE", "PARTIALOUTAGE", "MAJOROUTAGE"])
def test_non_operational_clob_status_is_unhealthy(status) -> None:
    result = normalize(components_payload(status=status))

    assert result.healthy is False
    assert result.component_status == status


def test_missing_clob_component_raises() -> None:
    with pytest.raises(ValueError, match="CLOB API"):
        normalize(components_payload(include_clob=False))


def test_clob_incident_is_unhealthy() -> None:
    incident = incident_payload()
    result = normalize(components_payload(incidents=[incident]))

    assert result.healthy is False
    assert [item.id for item in result.active_incidents] == ["incident-1"]


def test_summary_incident_is_displayed_and_deduplicated() -> None:
    incident = incident_payload()
    result = normalize(
        components_payload(incidents=[incident]),
        summary_payload(page_status="HASISSUES", incidents=[incident]),
    )

    assert result.summary_page_status == "HASISSUES"
    assert len(result.active_incidents) == 1
    assert result.active_incidents[0].updated_at == "2026-07-12T07:23:38.085Z"


def test_summary_other_incident_does_not_make_healthy_clob_unhealthy() -> None:
    result = normalize(summary=summary_payload(incidents=[incident_payload()]))

    assert result.healthy is True
    assert len(result.active_incidents) == 1


@pytest.mark.asyncio
async def test_any_status_source_failure_returns_unhealthy_status(monkeypatch) -> None:
    async def fake_fetch_status_payload(url):
        if url == polymarket_status.POLYMARKET_SUMMARY_URL:
            raise httpx.TimeoutException("summary timeout")
        return components_payload()

    monkeypatch.setattr(polymarket_status, "fetch_status_payload", fake_fetch_status_payload)

    result = await polymarket_status.get_polymarket_status()

    assert result.healthy is False
    assert "summary.json" in (result.error or "")
    assert "summary timeout" in (result.error or "")


@pytest.mark.asyncio
async def test_invalid_status_response_returns_unhealthy_status(monkeypatch) -> None:
    async def fake_fetch_status_payload(_url):
        return {"invalid": True}

    monkeypatch.setattr(polymarket_status, "fetch_status_payload", fake_fetch_status_payload)

    result = await polymarket_status.get_polymarket_status()

    assert result.healthy is False
    assert "Invalid status response" in (result.error or "")
