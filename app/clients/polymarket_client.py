from __future__ import annotations

import httpx


class PolymarketClient:
    def __init__(self, gamma_base_url: str, timeout: float = 10.0) -> None:
        self._gamma_base_url = gamma_base_url.rstrip("/")
        self._timeout = timeout

    async def fetch_active_events(self, limit: int = 100) -> list[dict]:
        async with httpx.AsyncClient(base_url=self._gamma_base_url, timeout=self._timeout) as client:
            response = await client.get(
                "/events",
                params={"active": "true", "closed": "false", "limit": limit},
            )
            response.raise_for_status()
            payload = response.json()
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            events = payload.get("events") or payload.get("data") or []
            return events if isinstance(events, list) else []
        return []

    async def fetch_event_by_slug(self, slug: str) -> dict | None:
        async with httpx.AsyncClient(base_url=self._gamma_base_url, timeout=self._timeout) as client:
            response = await client.get(f"/events/slug/{slug}")
            if response.status_code == 404:
                return None
            response.raise_for_status()
            payload = response.json()
        return payload if isinstance(payload, dict) else None
