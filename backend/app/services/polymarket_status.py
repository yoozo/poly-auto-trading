from __future__ import annotations

import logging
from typing import Any

import httpx

from app.schemas.polymarket_status import PolymarketIncident, PolymarketStatusResponse
from app.services.external_http import with_retry

logger = logging.getLogger(__name__)

POLYMARKET_STATUS_URL = "https://status.polymarket.com/v3/summary.json"


async def get_polymarket_status() -> PolymarketStatusResponse:
    try:
        payload = await fetch_status_payload()
        return normalize_status_payload(payload)
    except Exception as exc:
        # 状态接口不可达时按异常处理，避免网络故障被误显示为平台正常。
        logger.warning("Failed to fetch Polymarket status", exc_info=exc)
        return PolymarketStatusResponse(
            healthy=False,
            error=f"{exc.__class__.__name__}: {exc}",
        )


async def fetch_status_payload() -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=5.0) as client:
        async def get_status() -> dict[str, Any]:
            response = await client.get(POLYMARKET_STATUS_URL)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("Polymarket status response must be an object")
            return payload

        return await with_retry(get_status)


def normalize_status_payload(payload: dict[str, Any]) -> PolymarketStatusResponse:
    page = payload.get("page")
    if not isinstance(page, dict) or not isinstance(page.get("status"), str):
        raise ValueError("Polymarket status response is missing page.status")

    raw_incidents = payload.get("activeIncidents", [])
    if not isinstance(raw_incidents, list):
        raise ValueError("Polymarket status response has invalid activeIncidents")

    incidents = [normalize_incident(item) for item in raw_incidents]
    page_status = page["status"]
    return PolymarketStatusResponse(
        healthy=page_status == "OPERATIONAL" and not incidents,
        page_status=page_status,
        active_incidents=incidents,
    )


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
