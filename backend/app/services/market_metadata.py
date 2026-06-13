from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.services.external_http import with_retry
from app.services.report_store import list_market_metadata, upsert_market_metadata_rows

logger = logging.getLogger(__name__)

MARKET_METADATA_TTL = timedelta(hours=6)
MARKET_METADATA_CONCURRENCY = 8
MARKET_METADATA_FETCH_BATCH_SIZE = 100
MARKET_METADATA_UPSERT_BATCH_SIZE = 500
_IN_FLIGHT_MARKET_METADATA: dict[str, asyncio.Task[dict[str, Any] | None]] = {}
_IN_FLIGHT_LOCK = asyncio.Lock()
MetadataProgressCallback = Callable[[int, int], Awaitable[None]]


class ActivityWithMarketSlug(Protocol):
    slug: str | None


async def ensure_market_metadata_for_activities(
    session: AsyncSession,
    activities: list[ActivityWithMarketSlug],
) -> dict[str, Any]:
    slugs = collect_market_slugs(activities)
    return await ensure_market_metadata_for_slugs(session, slugs)


async def ensure_market_metadata_for_slugs(
    session: AsyncSession,
    slugs: set[str],
    progress_callback: MetadataProgressCallback | None = None,
) -> dict[str, Any]:
    existing = await list_market_metadata(session, slugs)
    # 已关闭市场结果不会变化；未关闭市场按 TTL 刷新，兼顾准确性和 Gamma API 压力。
    stale_slugs = sorted({slug for slug in slugs if needs_refresh(existing.get(slug))})
    total_stale = len(stale_slugs)
    if total_stale == 0:
        return existing

    processed = 0
    if stale_slugs:
        for slug_batch in iter_slug_batches(stale_slugs, MARKET_METADATA_FETCH_BATCH_SIZE):
            rows = await fetch_market_metadata_rows(slug_batch)
            processed += len(slug_batch)
            if progress_callback is not None and total_stale > 0:
                await progress_callback(processed, total_stale)
            for row_batch in iter_batches(rows, MARKET_METADATA_UPSERT_BATCH_SIZE):
                await upsert_market_metadata_rows(session, row_batch)
    existing = await list_market_metadata(session, slugs)
    return existing


def collect_market_slugs(activities: list[ActivityWithMarketSlug]) -> set[str]:
    return {activity.slug.strip() for activity in activities if activity.slug and activity.slug.strip()}


def needs_refresh(metadata: Any | None) -> bool:
    if metadata is None:
        return True
    if metadata.closed:
        return False
    fetched_at = metadata.fetched_at
    if fetched_at is None:
        return True
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - fetched_at >= MARKET_METADATA_TTL


async def fetch_market_metadata_rows(slugs: list[str]) -> list[dict[str, Any]]:
    semaphore = asyncio.Semaphore(MARKET_METADATA_CONCURRENCY)
    async with httpx.AsyncClient(
        base_url=settings.polymarket_gamma_base_url.rstrip("/"),
        timeout=15.0,
    ) as client:
        tasks = [await metadata_fetch_task(client, semaphore, slug) for slug in sorted(set(slugs))]
        results = await asyncio.gather(*tasks)
    return [row for row in results if row is not None]


async def metadata_fetch_task(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    slug: str,
) -> asyncio.Task[dict[str, Any] | None]:
    async with _IN_FLIGHT_LOCK:
        task = _IN_FLIGHT_MARKET_METADATA.get(slug)
        if task and not task.done():
            # 同一个 slug 可能被多个账户报表同时需要，复用 in-flight 请求减少外部 API 抖动。
            return task
        task = asyncio.create_task(fetch_market_metadata_row(client, semaphore, slug))
        _IN_FLIGHT_MARKET_METADATA[slug] = task
        task.add_done_callback(lambda completed, slug=slug: clear_metadata_task(slug, completed))
        return task


def clear_metadata_task(slug: str, completed: asyncio.Task[dict[str, Any] | None]) -> None:
    if _IN_FLIGHT_MARKET_METADATA.get(slug) is completed:
        _IN_FLIGHT_MARKET_METADATA.pop(slug, None)


async def fetch_market_metadata_row(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    slug: str,
) -> dict[str, Any] | None:
    async with semaphore:
        try:
            response = await with_retry(lambda: get_market_metadata_response(client, slug))
            if response.status_code == 404:
                return None
            payload = response.json()
            if not isinstance(payload, dict):
                return None
            return market_metadata_row(slug, payload)
        except Exception as exc:
            logger.warning("Polymarket market metadata fetch failed", extra={"slug": slug}, exc_info=exc)
            return None


async def get_market_metadata_response(client: httpx.AsyncClient, slug: str) -> httpx.Response:
    response = await client.get(f"/markets/slug/{slug}")
    if response.status_code != 404:
        response.raise_for_status()
    return response


def iter_batches(rows: list[dict[str, Any]], batch_size: int):
    for index in range(0, len(rows), batch_size):
        yield rows[index : index + batch_size]


def iter_slug_batches(slugs: list[str], batch_size: int):
    unique_slugs = sorted(set(slugs))
    for index in range(0, len(unique_slugs), batch_size):
        yield unique_slugs[index : index + batch_size]


def market_metadata_row(slug: str, market: dict[str, Any]) -> dict[str, Any]:
    event = first_dict(market.get("events")) or {}
    outcome, raw_outcome = resolve_market_outcome(market)
    now = datetime.now(timezone.utc)
    return {
        "slug": string_or_none(market.get("slug")) or slug,
        "closed": bool(market.get("closed") or event.get("closed")),
        "outcome": outcome,
        "raw_outcome": raw_outcome,
        "event": event,
        "market": market,
        "fetched_at": now,
    }


def resolve_market_outcome(market: dict[str, Any]) -> tuple[str | None, str | None]:
    for key in ("outcome", "winningOutcome", "resolvedOutcome", "resolution"):
        raw = string_or_none(market.get(key))
        if raw:
            return normalize_outcome(raw), raw

    # Gamma 有些市场只给 outcomePrices；价格接近 1 的 outcome 视为最终结果。
    outcomes = parse_json_list(market.get("outcomes"))
    outcome_prices = parse_json_list(market.get("outcomePrices"))
    if not outcomes or not outcome_prices or len(outcomes) != len(outcome_prices):
        return None, None

    prices: list[Decimal] = []
    for price in outcome_prices:
        try:
            prices.append(Decimal(str(price)))
        except (InvalidOperation, TypeError, ValueError):
            return None, None
    winning_index = max(range(len(prices)), key=lambda index: prices[index])
    if prices[winning_index] < Decimal("0.99"):
        return None, None
    raw = string_or_none(outcomes[winning_index])
    return (normalize_outcome(raw), raw) if raw else (None, None)


def parse_json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if not isinstance(value, str) or not value.strip():
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def first_dict(value: Any) -> dict[str, Any] | None:
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                return item
    return None


def normalize_outcome(value: str | None) -> str | None:
    if not value:
        return None
    lowered = value.strip().lower()
    if lowered in {"up", "yes"}:
        return "up"
    if lowered in {"down", "no"}:
        return "down"
    return lowered


def string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
