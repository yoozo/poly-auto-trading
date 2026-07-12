from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from app.schemas.polymarket_status import PolymarketIncident, PolymarketStatusResponse
from app.services.external_http import with_retry

logger = logging.getLogger(__name__)

POLYMARKET_COMPONENTS_URL = "https://status.polymarket.com/v3/components.json"
POLYMARKET_SUMMARY_URL = "https://status.polymarket.com/v3/summary.json"
CLOB_API_COMPONENT_NAME = "CLOB API"


async def get_polymarket_status() -> PolymarketStatusResponse:
    components_result, summary_result = await asyncio.gather(
        fetch_status_payload(POLYMARKET_COMPONENTS_URL),
        fetch_status_payload(POLYMARKET_SUMMARY_URL),
        return_exceptions=True,
    )
    errors = [
        f"{source}: {result.__class__.__name__}: {result}"
        for source, result in (
            ("components.json", components_result),
            ("summary.json", summary_result),
        )
        if isinstance(result, Exception)
    ]
    if errors:
        # 任一状态源不可用时不能确认交易 API 正常，按保守策略标记为异常。
        error = "; ".join(errors)
        logger.warning("Failed to fetch Polymarket status: %s", error)
        return PolymarketStatusResponse(healthy=False, error=error)

    try:
        return normalize_status_payload(components_result, summary_result)
    except Exception as exc:
        logger.warning("Invalid Polymarket status response", exc_info=exc)
        return PolymarketStatusResponse(
            healthy=False,
            error=f"Invalid status response: {exc}",
        )


async def fetch_status_payload(url: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=5.0) as client:
        async def get_status() -> dict[str, Any]:
            response = await client.get(url)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("Polymarket status response must be an object")
            return payload

        return await with_retry(get_status)


def normalize_status_payload(
    components_payload: dict[str, Any],
    summary_payload: dict[str, Any],
) -> PolymarketStatusResponse:
    component = find_clob_component(components_payload)
    summary_page_status, summary_incidents = parse_summary(summary_payload)
    component_status = component["status"].upper()
    component_incidents = parse_incidents(component.get("activeIncidents", []))
    incidents = merge_incidents(summary_incidents, component_incidents)
    return PolymarketStatusResponse(
        healthy=component_status == "OPERATIONAL" and not component_incidents,
        component_name=component["name"],
        component_status=component_status,
        summary_page_status=summary_page_status,
        active_incidents=incidents,
    )


def find_clob_component(payload: dict[str, Any]) -> dict[str, Any]:
    components = payload.get("components")
    if not isinstance(components, list):
        raise ValueError("Polymarket components response is missing components")
    component = next(
        (item for item in components if isinstance(item, dict) and item.get("name") == CLOB_API_COMPONENT_NAME),
        None,
    )
    if component is None or not isinstance(component.get("status"), str):
        raise ValueError(f"Polymarket component {CLOB_API_COMPONENT_NAME!r} was not found")
    return component


def parse_summary(payload: dict[str, Any]) -> tuple[str, list[PolymarketIncident]]:
    page = payload.get("page")
    if not isinstance(page, dict) or not isinstance(page.get("status"), str):
        raise ValueError("Polymarket summary response is missing page.status")
    raw_incidents = payload.get("activeIncidents", [])
    return page["status"].upper(), parse_incidents(raw_incidents)


def parse_incidents(raw_incidents: object) -> list[PolymarketIncident]:
    if not isinstance(raw_incidents, list):
        raise ValueError("Polymarket status response has invalid activeIncidents")
    return [normalize_incident(item) for item in raw_incidents]


def merge_incidents(
    summary_incidents: list[PolymarketIncident],
    component_incidents: list[PolymarketIncident],
) -> list[PolymarketIncident]:
    merged = {incident.id: incident for incident in summary_incidents}
    for incident in component_incidents:
        existing = merged.get(incident.id)
        if existing is None:
            merged[incident.id] = incident
            continue
        merged[incident.id] = existing.model_copy(
            update={
                "started_at": existing.started_at or incident.started_at,
                "updated_at": existing.updated_at or incident.updated_at,
                "url": existing.url or incident.url,
            }
        )
    return list(merged.values())


def normalize_incident(payload: object) -> PolymarketIncident:
    if not isinstance(payload, dict):
        raise ValueError("Polymarket status incident must be an object")
    required_fields = ("id", "name", "status", "impact")
    if any(not isinstance(payload.get(field), str) for field in required_fields):
        raise ValueError("Polymarket status incident is missing required fields")
    return PolymarketIncident(
        id=payload["id"],
        name=payload["name"],
        status=payload["status"],
        impact=payload["impact"],
        started_at=string_or_none(payload.get("started")),
        updated_at=string_or_none(payload.get("updatedAt")),
        url=string_or_none(payload.get("url")),
    )


def string_or_none(value: object) -> str | None:
    return value if isinstance(value, str) else None
