from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import logging
import re
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import parse_qs, urlparse

from polymarket import (
    PRODUCTION,
    AsyncPublicClient,
    RateLimitError,
    TimeoutError as PolymarketTimeoutError,
    TransportError,
)
from polymarket._internal.actions import data as polymarket_data_actions
from polymarket._internal.pagination import encode_offset_cursor
from py_clob_client_v2 import BookParams, ClobClient

from app.core.config import settings
from app.services.external_http import is_retryable_http_error, with_retry
from app.services.service_health import service_health_store

logger = logging.getLogger(__name__)

WALLET_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
ACTIVITY_PAGE_SIZE = 500
ACTIVITY_PARALLEL_REQUESTS = 15
# The public docs still show a wider offset range, but the live data API rejects
# historical activity windows at the 3000 boundary.
ACTIVITY_MAX_OFFSET = 3000
UP_DOWN_INTERVAL_TAGS = {
    "5m": "5M",
    "15m": "15M",
    "1h": "1H",
    "4h": "4H",
}
UP_DOWN_INTERVAL_SERIES = {
    "5m": "btc-up-or-down-5m",
    "15m": "btc-up-or-down-15m",
    "1h": "btc-up-or-down-hourly",
    "4h": "btc-up-or-down-4h",
}
UP_DOWN_SERIES_ALIASES = {
    "1h": ["btc-up-or-down-1h", "btc-up-or-down-60m", "btc-up-or-down-hourly", "btc-hourly-up-or-down"],
    "4h": ["btc-up-or-down-4h", "btc-up-or-down-240m", "btc-up-or-down-4h-window"],
}
UP_DOWN_INTERVAL_SLUG_PATTERNS = {
    "5m": re.compile(r"^btc-updown-5m-\d+$"),
    "15m": re.compile(r"^btc-updown-15m-\d+$"),
    "4h": re.compile(r"^btc-updown-4h-\d+$"),
}
UP_DOWN_INTERVAL_LOOKBACK_SECONDS = {
    "5m": 5 * 60,
    "15m": 15 * 60,
    "1h": 60 * 60,
    "4h": 4 * 60 * 60,
}


@dataclass(frozen=True)
class ResolvedPolymarketAccount:
    input: str
    normalized_user: str
    proxy_wallet: str
    profile: dict[str, Any]


@dataclass(frozen=True)
class NormalizedActivity:
    id: str
    proxy_wallet: str
    timestamp: datetime
    type: str
    condition_id: str | None
    slug: str | None
    event_slug: str | None
    title: str | None
    side: str | None
    outcome: str | None
    asset: str | None
    price: Decimal | None
    size: Decimal | None
    usdc_size: Decimal | None
    transaction_hash: str | None
    raw: dict[str, Any]


@dataclass(frozen=True)
class PolymarketOrderLevel:
    price: Decimal | None
    size: Decimal | None


@dataclass(frozen=True)
class PolymarketOutcomeQuote:
    name: str
    token_id: str | None
    price: Decimal | None
    buy_price: Decimal | None
    sell_price: Decimal | None
    best_bid: Decimal | None
    best_ask: Decimal | None
    last_trade_price: Decimal | None
    updated_at: datetime | None
    bids: list[PolymarketOrderLevel]
    asks: list[PolymarketOrderLevel]


@dataclass(frozen=True)
class PolymarketUpDownMarket:
    id: str
    condition_id: str | None
    slug: str | None
    title: str
    series_slug: str | None
    interval: str
    start_time: datetime | None
    end_time: datetime | None
    window: str
    seconds_to_start: int | None
    seconds_to_end: int | None
    accepting_orders: bool
    volume: Decimal | None
    liquidity: Decimal | None
    outcome_quotes: list[PolymarketOutcomeQuote]
    updated_at: datetime | None
    raw_event: dict[str, Any]


class PolymarketInputError(ValueError):
    pass


class PolymarketClient:
    def __init__(
        self,
        gamma_base_url: str | None = None,
        data_base_url: str | None = None,
        clob_base_url: str | None = None,
        timeout: float = 15.0,
    ) -> None:
        self._gamma_base_url = (gamma_base_url or settings.polymarket_gamma_base_url).rstrip("/")
        self._data_base_url = (data_base_url or settings.polymarket_data_base_url).rstrip("/")
        self._clob_base_url = (clob_base_url or settings.polymarket_clob_base_url).rstrip("/")
        self._timeout = timeout
        self._environment = replace(
            PRODUCTION,
            gamma_url=self._gamma_base_url,
            data_url=self._data_base_url,
            clob_url=self._clob_base_url,
        )

    async def resolve_account(self, raw_input: str) -> ResolvedPolymarketAccount:
        normalized = normalize_polymarket_input(raw_input)
        if is_wallet(normalized):
            profile = await self.fetch_profile_by_wallet(normalized)
            return ResolvedPolymarketAccount(
                input=raw_input.strip(),
                normalized_user=profile_name(profile) or normalized.lower(),
                proxy_wallet=normalized.lower(),
                profile=profile,
            )

        search_result = await self.search_profiles(normalized)
        profiles = search_result.get("profiles") or []
        profile = select_profile(profiles, normalized)
        proxy_wallet = string_or_none(profile.get("proxyWallet"))
        if not proxy_wallet or not is_wallet(proxy_wallet):
            raise PolymarketInputError(f"未找到 Polymarket 用户对应的钱包: {raw_input}")
        return ResolvedPolymarketAccount(
            input=raw_input.strip(),
            normalized_user=profile_name(profile) or normalized,
            proxy_wallet=proxy_wallet.lower(),
            profile=profile,
        )

    async def search_profiles(self, query: str) -> dict[str, Any]:
        try:
            async with self._public_client() as client:
                paginator = client.search(q=query, search_profiles=True, page_size=10)
                page = await with_retry(lambda: paginator.first_page(), retryable=is_retryable_polymarket_sdk_error)
            profiles: list[dict[str, Any]] = []
            for result in page.items:
                raw_result = sdk_model_to_dict(result)
                profiles.extend(
                    sdk_profile_to_gamma_dict(profile)
                    for profile in raw_result.get("profiles", [])
                    if isinstance(profile, dict)
                )
            service_health_store.set("polymarket", "running", metadata={"endpoint": "public-search"})
            return {"profiles": profiles}
        except Exception as exc:
            service_health_store.set("polymarket", "error", last_error=str(exc), metadata={"endpoint": "public-search"})
            logger.warning("Polymarket profile search failed", extra={"query": query}, exc_info=exc)
            raise

    async def fetch_profile_by_wallet(self, wallet: str) -> dict[str, Any]:
        try:
            async with self._public_client() as client:
                profile = await with_retry(
                    lambda: client.get_public_profile(wallet),
                    retryable=is_retryable_polymarket_sdk_error,
                )
                if profile is None:
                    return {"proxyWallet": wallet}
                payload = sdk_profile_to_gamma_dict(sdk_model_to_dict(profile), wallet=wallet)
            service_health_store.set("polymarket", "running", metadata={"endpoint": "profiles"})
            return payload if isinstance(payload, dict) else {"proxyWallet": wallet, "raw": payload}
        except Exception as exc:
            service_health_store.set("polymarket", "error", last_error=str(exc), metadata={"endpoint": "profiles"})
            logger.warning("Polymarket profile lookup failed", extra={"wallet": wallet}, exc_info=exc)
            return {"proxyWallet": wallet}

    async def fetch_activity(self, wallet: str, activity_limit: int) -> list[NormalizedActivity]:
        activities: list[NormalizedActivity] = []
        async for batch in self.iter_activity_batches(wallet, activity_limit):
            activities.extend(batch)
        return activities[:activity_limit]

    async def iter_activity_batches(
        self,
        wallet: str,
        activity_limit: int,
        end: int | None = None,
    ) -> AsyncIterator[list[NormalizedActivity]]:
        if activity_limit <= 0:
            return
        page_size = activity_page_size(activity_limit)
        remaining = activity_limit
        seen: set[str] = set()
        async with self._public_client() as client:
            rows, exhausted = await self.fetch_parallel_offset_pages(
                client=client,
                wallet=wallet,
                activity_limit=activity_limit,
                page_size=page_size,
                end=end,
            )
            batch, remaining = self.normalize_activity_batch(
                rows=rows,
                wallet=wallet,
                seen=seen,
                remaining=remaining,
            )
            if batch:
                yield batch
            cursor_end = oldest_timestamp(rows)
            while not exhausted and remaining > 0 and cursor_end is not None:
                page_count = offset_batch_page_count(remaining, page_size)
                rows, exhausted = await self.fetch_offset_page_batch(
                    client=client,
                    wallet=wallet,
                    page_size=page_size,
                    page_count=page_count,
                    end=cursor_end,
                )
                if not rows:
                    break
                batch, remaining = self.normalize_activity_batch(
                    rows=rows,
                    wallet=wallet,
                    seen=seen,
                    remaining=remaining,
                )
                if batch:
                    yield batch
                next_end = oldest_timestamp(rows)
                if next_end is None or next_end >= cursor_end:
                    break
                cursor_end = next_end

    def normalize_activity_batch(
        self,
        *,
        rows: list[dict[str, Any]],
        wallet: str,
        seen: set[str],
        remaining: int,
    ) -> tuple[list[NormalizedActivity], int]:
        activities: list[NormalizedActivity] = []
        for row in rows:
            key = row_identity(row)
            if key in seen:
                continue
            seen.add(key)
            activities.append(normalize_activity(row, wallet))
            remaining -= 1
            if remaining <= 0:
                break
        return activities, remaining

    async def fetch_parallel_offset_pages(
        self,
        *,
        client: AsyncPublicClient,
        wallet: str,
        activity_limit: int,
        page_size: int,
        end: int | None = None,
    ) -> tuple[list[dict[str, Any]], bool]:
        page_count = offset_batch_page_count(activity_limit, page_size)
        rows, reached_end = await self.fetch_offset_page_batch(
            client=client,
            wallet=wallet,
            page_size=page_size,
            page_count=page_count,
            end=end,
        )
        if reached_end:
            return rows, True
        return rows, len(rows) >= activity_limit

    async def fetch_cursor_pages(
        self,
        *,
        client: AsyncPublicClient,
        wallet: str,
        activity_limit: int,
        page_size: int,
        end: int | None,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        cursor_end = end
        while len(rows) < activity_limit and cursor_end is not None:
            page_count = offset_batch_page_count(activity_limit - len(rows), page_size)
            batch_rows, reached_end = await self.fetch_offset_page_batch(
                client=client,
                wallet=wallet,
                page_size=page_size,
                page_count=page_count,
                end=cursor_end,
            )
            if not batch_rows:
                break
            rows.extend(batch_rows)
            if reached_end or len(rows) >= activity_limit:
                break
            next_end = oldest_timestamp(batch_rows)
            if next_end is None or next_end >= cursor_end:
                break
            cursor_end = next_end
        return rows

    async def fetch_offset_page_batch(
        self,
        *,
        client: AsyncPublicClient,
        wallet: str,
        page_size: int,
        page_count: int,
        end: int | None,
    ) -> tuple[list[dict[str, Any]], bool]:
        tasks = [
            self.fetch_activity_page(
                wallet=wallet,
                limit=page_size,
                offset=page_index * page_size,
                end=end,
                client=client,
            )
            for page_index in range(page_count)
        ]
        pages = await asyncio.gather(*tasks, return_exceptions=True)
        rows: list[dict[str, Any]] = []
        for page_index, page in enumerate(pages):
            offset = page_index * page_size
            if isinstance(page, Exception):
                if offset == 0:
                    raise page
                logger.warning(
                    "Polymarket offset page failed, falling back to cursor pagination",
                    extra={"wallet": wallet, "offset": offset, "end": end},
                    exc_info=page,
                )
                return rows, False
            rows.extend(page)
            if len(page) < page_size:
                return rows, True
        return rows, False

    async def fetch_activity_page(
        self,
        wallet: str,
        limit: int,
        offset: int,
        end: int | None = None,
        client: AsyncPublicClient | None = None,
    ) -> list[dict[str, Any]]:
        try:
            if client is None:
                async with self._public_client() as scoped_client:
                    payload = await self._fetch_activity_page_with_sdk(
                        scoped_client,
                        wallet=wallet,
                        limit=limit,
                        offset=offset,
                        end=end,
                    )
            else:
                payload = await self._fetch_activity_page_with_sdk(
                    client,
                    wallet=wallet,
                    limit=limit,
                    offset=offset,
                    end=end,
                )
            service_health_store.set("polymarket", "running", metadata={"endpoint": "activity"})
            if not isinstance(payload, list):
                raise RuntimeError("Unexpected Polymarket activity response")
            return [row for row in payload if isinstance(row, dict)]
        except Exception as exc:
            service_health_store.set("polymarket", "error", last_error=str(exc), metadata={"endpoint": "activity"})
            logger.warning(
                "Polymarket activity fetch failed",
                extra={"wallet": wallet, "offset": offset, "end": end},
                exc_info=exc,
            )
            raise

    async def fetch_btc_up_down_markets(
        self,
        *,
        interval: str = "5m",
        limit: int = 6,
        include_recent_closed: bool = True,
        now: datetime | None = None,
    ) -> list[PolymarketUpDownMarket]:
        if interval not in UP_DOWN_INTERVAL_TAGS:
            raise PolymarketInputError(f"暂不支持 Polymarket up/down 周期: {interval}")
        current_time = normalize_datetime(now or datetime.now(timezone.utc))
        events = await self.fetch_up_down_events(
            interval=interval,
            now=current_time,
            limit=max(limit * 8, 20),
            include_recent_closed=include_recent_closed,
        )
        candidates = [
            event
            for event in events
            if is_btc_up_down_event(event, interval=interval)
        ]
        selected = select_up_down_windows(candidates, now=current_time, limit=limit)
        books = await self.fetch_order_books(token_ids_for_events(selected))
        markets = [
            normalize_up_down_market(event, interval=interval, books=books, now=current_time)
            for event in selected
        ]
        return assign_up_down_windows(markets)

    async def fetch_up_down_events(
        self,
        *,
        interval: str,
        now: datetime,
        limit: int,
        include_recent_closed: bool = True,
    ) -> list[dict[str, Any]]:
        tag_slug = UP_DOWN_INTERVAL_TAGS[interval]
        series_slug = UP_DOWN_INTERVAL_SERIES[interval]
        end_date_min = now
        if include_recent_closed:
            lookback_seconds = UP_DOWN_INTERVAL_LOOKBACK_SECONDS[interval]
            end_date_min = now.replace() - timedelta(seconds=lookback_seconds)
        params = {
            "limit": limit,
            "related_tags": True,
            "end_date_min": end_date_min,
            "order": "end_date",
            "ascending": True,
        }
        try:
            rows: list[dict[str, Any]] = []
            try:
                rows.extend(await self.fetch_up_down_series_events(
                    interval=interval,
                    series_slug=series_slug,
                    end_date_min=end_date_min,
                    limit=limit,
                ))
            except Exception as exc:
                logger.warning(
                    "Polymarket up/down series fetch failed; falling back to events",
                    extra={"interval": interval, "series_slug": series_slug},
                    exc_info=exc,
                )
            async with self._public_client() as client:
                tag_params = dict(params)
                tag_params["tag_slug"] = tag_slug
                tag_params["closed"] = None
                payload = await with_retry(
                    lambda: self._fetch_event_page_with_sdk(client, **tag_params),
                    retryable=is_retryable_polymarket_sdk_error,
                )
                service_health_store.set(
                    "polymarket",
                    "running",
                    metadata={"endpoint": "events", "tag_slug": tag_slug, "source": "tag"},
                )
                if isinstance(payload, list):
                    rows.extend(row for row in payload if isinstance(row, dict))
            if not rows:
                async with self._public_client() as client:
                    fallback_params = dict(params)
                    fallback_params["tag_slug"] = tag_slug
                    payload = await with_retry(
                        lambda: self._fetch_event_page_with_sdk(client, **fallback_params),
                        retryable=is_retryable_polymarket_sdk_error,
                    )
                    service_health_store.set(
                        "polymarket",
                        "running",
                        metadata={"endpoint": "events", "tag_slug": tag_slug, "fallback": True},
                    )
                    if isinstance(payload, list):
                        rows.extend(row for row in payload if isinstance(row, dict))
            if not rows:
                return []
            deduped: dict[str, dict[str, Any]] = {}
            for row in rows:
                fallback_event_id = string_or_none(row.get("event_id"))
                fallback_series = string_or_none(row.get("seriesSlug")) or string_or_none(row.get("series"))
                key = (
                    string_or_none(row.get("id"))
                    or string_or_none(row.get("slug"))
                    or (f"{fallback_event_id}:{fallback_series}" if fallback_event_id and fallback_series else None)
                )
                if not key:
                    key = json.dumps(row, sort_keys=True, default=str)
                deduped[key] = row
            return list(deduped.values())
        except Exception as exc:
            service_health_store.set("polymarket", "error", last_error=str(exc), metadata={"endpoint": "events"})
            logger.warning("Polymarket up/down events fetch failed", extra={"interval": interval}, exc_info=exc)
            raise

    async def fetch_up_down_series_events(
        self,
        *,
        interval: str,
        series_slug: str,
        end_date_min: datetime,
        limit: int,
    ) -> list[dict[str, Any]]:
        payload = await with_retry(
            lambda: self._fetch_series_events(series_slug=series_slug),
        )
        service_health_store.set(
            "polymarket",
            "running",
            metadata={"endpoint": "series", "series_slug": series_slug},
        )
        rows = [
            event
            for event in payload
            if isinstance(event, dict)
            and is_btc_up_down_event(event, interval=interval)
            and event_not_before(event, end_date_min)
        ]
        selected = sorted(rows, key=lambda event: event_start_time(event) or datetime.max.replace(tzinfo=timezone.utc))[:limit]
        hydrated = await self._hydrate_events_missing_markets(selected)
        return [event for event in hydrated if has_up_down_market_payload(event)]

    async def fetch_order_books(self, token_ids: list[str]) -> dict[str, dict[str, Any]]:
        if not token_ids:
            return {}
        try:
            payload = await with_retry(
                lambda: asyncio.to_thread(self._fetch_order_books_with_clob_sdk, token_ids),
                retryable=is_retryable_polymarket_sdk_error,
            )
            service_health_store.set("polymarket", "running", metadata={"endpoint": "books"})
            if not isinstance(payload, list):
                raise RuntimeError("Unexpected Polymarket order books response")
            return {
                str(book.get("asset_id")): book
                for book in payload
                if isinstance(book, dict) and book.get("asset_id") is not None
            }
        except Exception as exc:
            service_health_store.set("polymarket", "error", last_error=str(exc), metadata={"endpoint": "books"})
            logger.warning("Polymarket order books fetch failed", extra={"token_count": len(token_ids)}, exc_info=exc)
            raise

    def _public_client(self) -> AsyncPublicClient:
        return AsyncPublicClient(environment=self._environment, logger=logger)

    async def _fetch_event_page_with_sdk(
        self,
        client: AsyncPublicClient,
        **params: Any,
    ) -> list[dict[str, Any]]:
        limit = int(params.pop("limit"))
        closed = params.pop("closed", False)
        paginator = client.list_events(closed=closed, page_size=limit, **params)
        page = await paginator.first_page()
        return [sdk_event_to_gamma_dict(item) for item in page.items]

    async def _fetch_series_events(self, *, series_slug: str) -> list[dict[str, Any]]:
        async with self._public_client() as client:
            paginator = client.list_series(slug=series_slug, closed=False, page_size=1)
            page = await with_retry(
                lambda: paginator.first_page(),
                retryable=is_retryable_polymarket_sdk_error,
            )
        events: list[dict[str, Any]] = []
        for series in page.items:
            events.extend(sdk_series_to_gamma_events(series, series_slug=series_slug))
        return events

    async def _hydrate_events_missing_markets(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        missing_slugs = [
            slug
            for event in events
            if not has_up_down_market_payload(event)
            for slug in [string_or_none(event.get("slug"))]
            if slug
        ]
        if not missing_slugs:
            return events
        async with self._public_client() as client:
            paginator = client.list_events(slug=missing_slugs, closed=None, page_size=max(len(missing_slugs), 1))
            page = await with_retry(
                lambda: paginator.first_page(),
                retryable=is_retryable_polymarket_sdk_error,
            )
        hydrated_by_slug = {
            string_or_none(row.get("slug")): row
            for row in [sdk_event_to_gamma_dict(item) for item in page.items]
            if string_or_none(row.get("slug"))
        }
        return [merge_event_payload(event, hydrated_by_slug.get(string_or_none(event.get("slug")))) for event in events]

    async def _fetch_activity_page_with_sdk(
        self,
        client: AsyncPublicClient,
        *,
        wallet: str,
        limit: int,
        offset: int,
        end: int | None,
    ) -> list[dict[str, Any]]:
        paginator = client.list_activity(
            user=wallet,
            sort_by="TIMESTAMP",
            sort_direction="DESC",
            end=end,
            page_size=limit,
        )
        # SDK exposes offset pagination through cursors; this keeps the project's bounded
        # parallel offset windows without reintroducing direct HTTP calls.
        if offset > 0:
            cursor = activity_offset_cursor(wallet=wallet, page_size=limit, offset=offset, end=end)
            paginator = paginator.from_cursor(cursor)
        page = await with_retry(lambda: paginator.first_page(), retryable=is_retryable_polymarket_sdk_error)
        return [sdk_activity_to_data_row(item) for item in page.items]

    def _fetch_order_books_with_clob_sdk(self, token_ids: list[str]) -> list[dict[str, Any]]:
        client = ClobClient(host=self._clob_base_url, chain_id=self._environment.chain_id)
        books = client.get_order_books([BookParams(token_id=token_id) for token_id in token_ids])
        return [sdk_order_book_to_clob_dict(book) for book in books]


def normalize_polymarket_input(raw_input: str) -> str:
    value = raw_input.strip()
    if not value:
        raise PolymarketInputError("请输入 Polymarket profile 或钱包地址")

    wallet_match = re.search(r"0x[a-fA-F0-9]{40}", value)
    if wallet_match:
        return wallet_match.group(0).lower()

    if value.startswith("@"):
        return value[1:].strip()

    if "://" in value or value.startswith("polymarket.com/"):
        parsed = urlparse(value if "://" in value else f"https://{value}")
        query = parse_qs(parsed.query)
        for key in ("user", "profile", "username"):
            if query.get(key):
                return query[key][0].strip().lstrip("@")
        parts = [part for part in parsed.path.split("/") if part]
        if parts:
            return parts[-1].strip().lstrip("@")

    return value


def page_count_for_limit(activity_limit: int, page_size: int) -> int:
    return max(1, (activity_limit + page_size - 1) // page_size)


def activity_page_size(activity_limit: int) -> int:
    if activity_limit <= ACTIVITY_PAGE_SIZE:
        return max(1, activity_limit)
    return min(ACTIVITY_PAGE_SIZE, max(1, ACTIVITY_MAX_OFFSET // ACTIVITY_PARALLEL_REQUESTS))


def offset_batch_page_count(activity_limit: int, page_size: int) -> int:
    max_offset_pages = ((ACTIVITY_MAX_OFFSET - 1) // page_size) + 1
    return min(page_count_for_limit(activity_limit, page_size), ACTIVITY_PARALLEL_REQUESTS, max_offset_pages)


def dedupe_activity_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        key = row_identity(row)
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


def row_identity(row: dict[str, Any]) -> str:
    transaction_hash = string_or_none(row.get("transactionHash") or row.get("transaction_hash"))
    if transaction_hash:
        return "|".join(
            [
                transaction_hash.lower(),
                str(row.get("timestamp") or ""),
                str(row.get("conditionId") or row.get("condition_id") or ""),
                str(row.get("asset") or ""),
                str(row.get("outcome") or ""),
                str(row.get("side") or ""),
                str(row.get("size") or ""),
                str(row.get("usdcSize") or row.get("usdc_size") or ""),
            ]
        )
    return json.dumps(row, sort_keys=True, default=str)


def oldest_timestamp(rows: list[dict[str, Any]]) -> int | None:
    timestamps: list[int] = []
    for row in rows:
        try:
            timestamps.append(int(parse_timestamp(row.get("timestamp")).timestamp()))
        except PolymarketInputError:
            continue
    if not timestamps:
        return None
    return min(timestamps) - 1


def activity_offset_cursor(*, wallet: str, page_size: int, offset: int, end: int | None) -> str:
    spec = polymarket_data_actions.list_activity_spec(
        user=wallet,
        end=end,
        sort_by="TIMESTAMP",
        sort_direction="DESC",
    )
    return encode_offset_cursor(
        service=spec.service,
        path=spec.path,
        base_params=spec.base_params,
        offset=offset,
        page_size=page_size,
    )


def is_retryable_polymarket_sdk_error(exc: Exception) -> bool:
    for current in exception_chain(exc):
        if isinstance(current, (RateLimitError, TransportError, PolymarketTimeoutError)):
            return True
        if isinstance(current, Exception) and is_retryable_http_error(current):
            return True
        if is_retryable_httpcore_error(current):
            return True
    return False


def exception_chain(exc: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        chain.append(current)
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return chain


def is_retryable_httpcore_error(exc: BaseException) -> bool:
    exc_type = type(exc)
    if not exc_type.__module__.startswith("httpcore"):
        return False
    return exc_type.__name__ in {
        "ConnectError",
        "ConnectTimeout",
        "NetworkError",
        "PoolTimeout",
        "ReadError",
        "ReadTimeout",
        "RemoteProtocolError",
        "TimeoutException",
        "WriteError",
        "WriteTimeout",
    }


def sdk_model_to_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="json")
    return {}


def sdk_profile_to_gamma_dict(profile: dict[str, Any], *, wallet: str | None = None) -> dict[str, Any]:
    normalized = dict(profile)
    proxy_wallet = normalized.get("proxyWallet") or normalized.get("wallet") or wallet
    if proxy_wallet:
        normalized["proxyWallet"] = str(proxy_wallet)
        normalized.setdefault("wallet", str(proxy_wallet))
    return normalized


def sdk_activity_to_data_row(activity: Any) -> dict[str, Any]:
    row = sdk_model_to_dict(activity)
    normalized = dict(row.get("raw") or {})
    normalized.update(row)
    field_map = {
        "wallet": "proxyWallet",
        "transaction_hash": "transactionHash",
        "condition_id": "conditionId",
        "token_id": "asset",
        "shares": "size",
        "amount": "usdcSize",
        "event_slug": "eventSlug",
        "outcome_index": "outcomeIndex",
        "profile_image": "profileImage",
        "profile_image_optimized": "profileImageOptimized",
    }
    for source, target in field_map.items():
        if source in row and row[source] is not None:
            normalized[target] = row[source]
    return normalized


def sdk_order_book_to_clob_dict(book: Any) -> dict[str, Any]:
    raw = sdk_model_to_dict(book)
    normalized = dict(raw)
    token_id = normalized.get("asset_id") or normalized.get("token_id")
    if token_id is not None:
        normalized["asset_id"] = str(token_id)
    timestamp = normalized.get("timestamp")
    if isinstance(timestamp, datetime):
        normalized["timestamp"] = str(int(timestamp.timestamp() * 1000))
    elif isinstance(timestamp, str) and "T" in timestamp:
        parsed = optional_timestamp(timestamp)
        if parsed is not None:
            normalized["timestamp"] = str(int(parsed.timestamp() * 1000))
    return normalized


def sdk_series_to_gamma_events(series: Any, *, series_slug: str) -> list[dict[str, Any]]:
    raw = sdk_model_to_dict(series)
    if (string_or_none(raw.get("slug")) or "").lower() != series_slug.lower():
        return []
    events = raw.get("events")
    if not isinstance(events, list):
        return []
    # Series 查询是 BTC up/down 的精准入口；这里把 SDK 模型恢复成现有 normalizer 依赖的 Gamma 字段。
    normalized_events: list[dict[str, Any]] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        normalized = sdk_event_to_gamma_dict(event)
        normalized["seriesSlug"] = string_or_none(normalized.get("seriesSlug")) or series_slug
        normalized_events.append(normalized)
    return normalized_events


def sdk_event_to_gamma_dict(event: Any) -> dict[str, Any]:
    raw = sdk_model_to_dict(event)
    series_slug = (
        nested_get(raw, "sports", "series_slug")
        or first_nested_value(raw.get("series"), "slug")
    )
    return {
        **raw,
        "id": string_or_none(raw.get("id")) or "",
        "slug": string_or_none(raw.get("slug")),
        "title": string_or_none(raw.get("title")),
        "seriesSlug": series_slug,
        "startTime": nested_get(raw, "schedule", "start_time"),
        "endDate": nested_get(raw, "schedule", "end_date"),
        "volume": first_present(nested_get(raw, "metrics", "volume"), raw.get("volume")),
        "liquidity": first_present(nested_get(raw, "metrics", "liquidity"), raw.get("liquidity")),
        "markets": [sdk_market_to_gamma_dict(market) for market in raw.get("markets", []) if isinstance(market, dict)],
    }


def sdk_market_to_gamma_dict(market: dict[str, Any]) -> dict[str, Any]:
    outcomes = sdk_market_outcomes(market)
    return {
        **market,
        "id": string_or_none(market.get("id")) or "",
        "conditionId": string_or_none(market.get("condition_id")),
        "slug": string_or_none(market.get("slug")),
        "question": string_or_none(market.get("question")),
        "eventStartTime": nested_get(market, "state", "start_date"),
        "endDate": nested_get(market, "state", "end_date"),
        "outcomes": json.dumps([outcome["label"] for outcome in outcomes]),
        "outcomePrices": json.dumps([outcome.get("price") for outcome in outcomes]),
        "clobTokenIds": json.dumps([outcome.get("token_id") for outcome in outcomes]),
        "acceptingOrders": nested_get(market, "state", "accepting_orders"),
        "volumeNum": first_present(
            nested_get(market, "metrics", "volume_num"),
            nested_get(market, "metrics", "volume"),
            market.get("volumeNum"),
            market.get("volume"),
        ),
        "liquidityNum": first_present(
            nested_get(market, "metrics", "liquidity_num"),
            nested_get(market, "metrics", "liquidity"),
            market.get("liquidityNum"),
            market.get("liquidity"),
        ),
    }


def sdk_market_outcomes(market: dict[str, Any]) -> list[dict[str, Any]]:
    outcomes = market.get("outcomes")
    if isinstance(outcomes, list):
        return [outcome for outcome in outcomes if isinstance(outcome, dict)]
    if isinstance(outcomes, dict):
        ordered = []
        for key in ("yes", "no"):
            outcome = outcomes.get(key)
            if isinstance(outcome, dict):
                ordered.append(outcome)
        if ordered:
            return ordered
    return []


def nested_get(value: dict[str, Any], *keys: str) -> Any:
    current: Any = value
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def first_present(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def first_nested_value(value: Any, key: str) -> Any:
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict) and item.get(key):
                return item[key]
    return None


def is_btc_up_down_event(event: dict[str, Any], *, interval: str) -> bool:
    title = (string_or_none(event.get("title")) or "").lower()
    slug = (string_or_none(event.get("slug")) or "").lower()
    series_slug = (
        string_or_none(event.get("seriesSlug"))
        or string_or_none(event.get("series_slug"))
        or string_or_none((event.get("series") or {}).get("slug") if isinstance(event.get("series"), dict) else None)
    )
    expected_series = UP_DOWN_INTERVAL_SERIES[interval]
    series_aliases = {expected_series}
    series_aliases.update(UP_DOWN_SERIES_ALIASES.get(interval, []))
    normalized_series_slug = (series_slug or "").lower()
    if normalized_series_slug in {alias.lower() for alias in series_aliases}:
        return True

    title_interval_tokens = {interval}
    if interval.endswith("h"):
        title_interval_tokens.add("1h" if interval == "1h" else interval.replace("h", " hour"))
        title_interval_tokens.add(f"{interval[:-1]} hour")
    elif interval.endswith("m"):
        title_interval_tokens.add(f"{interval[:-1]} minute")

    contains_slug_signal = (
        "btc-updown" in slug
        or "btc-up-or-down" in slug
        or "bitcoin up or down" in title
    )
    slug_pattern = UP_DOWN_INTERVAL_SLUG_PATTERNS.get(interval)
    if slug_pattern is not None and slug_pattern.match(slug):
        return True

    tag_slugs = event_tag_slugs(event)
    expected_tag = UP_DOWN_INTERVAL_TAGS[interval].lower()
    return (
        any(interval_token in title for interval_token in title_interval_tokens)
        and contains_slug_signal
        and (
            expected_tag in tag_slugs
            or interval in slug
            or any(token in normalized_series_slug for token in {alias.lower() for alias in series_aliases})
        )
    )


def event_tag_slugs(event: dict[str, Any]) -> set[str]:
    tags = event.get("tags")
    if not isinstance(tags, list):
        return set()
    return {
        slug.lower()
        for tag in tags
        if isinstance(tag, dict)
        for slug in [string_or_none(tag.get("slug"))]
        if slug
    }


def select_up_down_windows(
    events: list[dict[str, Any]],
    *,
    now: datetime,
    limit: int,
) -> list[dict[str, Any]]:
    valid = [
        event
        for event in events
        if event_start_time(event) is not None and event_end_time(event) is not None
    ]
    historical = sorted(
        [event for event in valid if (event_end_time(event) or datetime.min.replace(tzinfo=timezone.utc)) <= now],
        key=lambda event: event_end_time(event) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    current = sorted(
        [
            event
            for event in valid
            if (event_start_time(event) or datetime.max.replace(tzinfo=timezone.utc))
            <= now
            < (event_end_time(event) or datetime.min.replace(tzinfo=timezone.utc))
        ],
        key=lambda event: event_end_time(event) or datetime.max.replace(tzinfo=timezone.utc),
    )
    future = sorted(
        [event for event in valid if (event_start_time(event) or datetime.min.replace(tzinfo=timezone.utc)) > now],
        key=lambda event: event_start_time(event) or datetime.max.replace(tzinfo=timezone.utc),
    )
    # 前端需要最近历史盘、当前盘和未来盘；不要再用简单截断，否则会把远期盘误当主数据。
    ordered = historical[:1] + current + future
    seen: set[str] = set()
    selected: list[dict[str, Any]] = []
    for event in ordered:
        key = string_or_none(event.get("id")) or string_or_none(event.get("slug")) or json.dumps(event, sort_keys=True, default=str)
        if key in seen:
            continue
        seen.add(key)
        selected.append(event)
        if len(selected) >= limit:
            break
    return selected


def has_up_down_market_payload(event: dict[str, Any]) -> bool:
    market = first_market(event)
    outcomes = parse_json_list(market.get("outcomes"))
    token_ids = parse_json_list(market.get("clobTokenIds"))
    return len(outcomes) >= 2 and len(token_ids) >= 2


def merge_event_payload(original: dict[str, Any], hydrated: dict[str, Any] | None) -> dict[str, Any]:
    if hydrated is None:
        return original
    merged = {**original, **hydrated}
    for key in ("seriesSlug", "volume", "liquidity"):
        if merged.get(key) is None and original.get(key) is not None:
            merged[key] = original[key]
    return merged


def normalize_up_down_market(
    event: dict[str, Any],
    *,
    interval: str,
    books: dict[str, dict[str, Any]],
    now: datetime,
) -> PolymarketUpDownMarket:
    market = first_market(event)
    outcomes = parse_json_list(market.get("outcomes"))
    token_ids = [str(token_id) for token_id in parse_json_list(market.get("clobTokenIds")) if token_id is not None]
    prices = [decimal_or_none(price) for price in parse_json_list(market.get("outcomePrices"))]
    start_time = event_start_time(event)
    end_time = event_end_time(event)
    return PolymarketUpDownMarket(
        id=str(market.get("id") or event.get("id") or ""),
        condition_id=string_or_none(market.get("conditionId")),
        slug=string_or_none(market.get("slug") or event.get("slug")),
        title=string_or_none(market.get("question") or event.get("title")) or "Bitcoin Up or Down",
        series_slug=string_or_none(event.get("seriesSlug")),
        interval=interval,
        start_time=start_time,
        end_time=end_time,
        window=market_window(start_time=start_time, end_time=end_time, now=now),
        seconds_to_start=seconds_between(now, start_time),
        seconds_to_end=seconds_between(now, end_time),
        accepting_orders=bool(market.get("acceptingOrders")),
        volume=decimal_or_none(
            first_present(market.get("volumeNum"), market.get("volume"), event.get("volumeNum"), event.get("volume"))
        ),
        liquidity=decimal_or_none(
            first_present(
                market.get("liquidityNum"),
                market.get("liquidity"),
                event.get("liquidityNum"),
                event.get("liquidity"),
            )
        ),
        outcome_quotes=[
            normalize_outcome_quote(
                name=str(outcome),
                token_id=token_ids[index] if index < len(token_ids) else None,
                price=prices[index] if index < len(prices) else None,
                books=books,
            )
            for index, outcome in enumerate(outcomes)
        ],
        updated_at=now,
        raw_event=event,
    )


def normalize_outcome_quote(
    *,
    name: str,
    token_id: str | None,
    price: Decimal | None,
    books: dict[str, dict[str, Any]],
) -> PolymarketOutcomeQuote:
    book = books.get(token_id or "") or {}
    bids = normalize_order_levels(book.get("bids"))
    asks = normalize_order_levels(book.get("asks"))
    return PolymarketOutcomeQuote(
        name=name,
        token_id=token_id,
        price=price,
        buy_price=best_ask(asks),
        sell_price=best_bid(bids),
        best_bid=best_bid(bids),
        best_ask=best_ask(asks),
        last_trade_price=decimal_or_none(book.get("last_trade_price")),
        updated_at=optional_timestamp_ms(book.get("timestamp")),
        bids=bids[:10],
        asks=asks[:10],
    )


def normalize_order_levels(value: Any) -> list[PolymarketOrderLevel]:
    if not isinstance(value, list):
        return []
    levels = []
    for row in value:
        if not isinstance(row, dict):
            continue
        levels.append(
            PolymarketOrderLevel(
                price=decimal_or_none(row.get("price")),
                size=decimal_or_none(row.get("size")),
            )
        )
    return levels


def best_bid(levels: list[PolymarketOrderLevel]) -> Decimal | None:
    prices = [level.price for level in levels if level.price is not None]
    return max(prices) if prices else None


def best_ask(levels: list[PolymarketOrderLevel]) -> Decimal | None:
    prices = [level.price for level in levels if level.price is not None]
    return min(prices) if prices else None


def first_market(event: dict[str, Any]) -> dict[str, Any]:
    markets = event.get("markets")
    if isinstance(markets, list):
        for market in markets:
            if isinstance(market, dict):
                return market
    return event


def token_ids_for_events(events: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    token_ids: list[str] = []
    for event in events:
        market = first_market(event)
        for token_id in parse_json_list(market.get("clobTokenIds")):
            if token_id is None:
                continue
            normalized = str(token_id)
            if normalized in seen:
                continue
            seen.add(normalized)
            token_ids.append(normalized)
    return token_ids


def parse_json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def event_start_time(event: dict[str, Any]) -> datetime | None:
    market = first_market(event)
    start_time = optional_timestamp(event.get("startTime") or market.get("eventStartTime"))
    end_time = event_end_time(event)
    interval_seconds = event_interval_seconds(event)
    if end_time is None or interval_seconds is None:
        return start_time
    inferred_start = end_time - timedelta(seconds=interval_seconds)
    if start_time is None:
        return inferred_start
    duration = (end_time - start_time).total_seconds()
    if duration > interval_seconds * 2:
        return inferred_start
    return start_time


def event_end_time(event: dict[str, Any]) -> datetime | None:
    return optional_timestamp(event.get("endDate") or first_market(event).get("endDate"))


def event_interval_seconds(event: dict[str, Any]) -> int | None:
    series_slug = (
        string_or_none(event.get("seriesSlug"))
        or string_or_none(event.get("series_slug"))
        or string_or_none((event.get("series") or {}).get("slug") if isinstance(event.get("series"), dict) else None)
    )
    if series_slug:
        normalized = series_slug.lower()
        for interval, expected in UP_DOWN_INTERVAL_SERIES.items():
            aliases = {expected.lower(), *[alias.lower() for alias in UP_DOWN_SERIES_ALIASES.get(interval, [])]}
            if normalized in aliases:
                return UP_DOWN_INTERVAL_LOOKBACK_SECONDS[interval]
    slug = (string_or_none(event.get("slug")) or "").lower()
    for interval, pattern in UP_DOWN_INTERVAL_SLUG_PATTERNS.items():
        if pattern.match(slug):
            return UP_DOWN_INTERVAL_LOOKBACK_SECONDS[interval]
    return None


def event_not_before(event: dict[str, Any], end_date_min: datetime) -> bool:
    end_time = event_end_time(event)
    return end_time is not None and end_time >= end_date_min


def optional_timestamp(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    try:
        return normalize_datetime(parse_timestamp(value))
    except PolymarketInputError:
        return None


def optional_timestamp_ms(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def normalize_datetime(value: datetime) -> datetime:
    return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)


def market_window(*, start_time: datetime | None, end_time: datetime | None, now: datetime) -> str:
    if start_time is None or end_time is None:
        return "unknown"
    if start_time <= now < end_time:
        return "current"
    if now < start_time:
        return "upcoming"
    return "expired"


def seconds_between(now: datetime, target: datetime | None) -> int | None:
    if target is None:
        return None
    return int((target - now).total_seconds())


def assign_up_down_windows(markets: list[PolymarketUpDownMarket]) -> list[PolymarketUpDownMarket]:
    assigned: list[PolymarketUpDownMarket] = []
    next_assigned = False
    for market in markets:
        if market.window == "upcoming" and not next_assigned:
            assigned.append(replace(market, window="next"))
            next_assigned = True
            continue
        assigned.append(market)
    return assigned


def is_wallet(value: str) -> bool:
    return bool(WALLET_RE.fullmatch(value.strip()))


def account_id_for_wallet(wallet: str) -> str:
    return wallet.lower()


def select_profile(profiles: list[Any], query: str) -> dict[str, Any]:
    if not profiles:
        raise PolymarketInputError(f"未找到 Polymarket 用户: {query}")
    lowered = query.lower().lstrip("@")
    for profile in profiles:
        if not isinstance(profile, dict):
            continue
        names = [
            string_or_none(profile.get("name")),
            string_or_none(profile.get("pseudonym")),
        ]
        if any(name and name.lower() == lowered for name in names):
            return profile
    first = profiles[0]
    if not isinstance(first, dict):
        raise PolymarketInputError(f"未找到 Polymarket 用户: {query}")
    return first


def profile_name(profile: dict[str, Any]) -> str | None:
    return string_or_none(profile.get("name")) or string_or_none(profile.get("pseudonym"))


def normalize_activity(row: dict[str, Any], wallet: str) -> NormalizedActivity:
    timestamp = parse_timestamp(row.get("timestamp"))
    transaction_hash = string_or_none(row.get("transactionHash") or row.get("transaction_hash"))
    activity_type = string_or_none(row.get("type")) or "UNKNOWN"
    condition_id = string_or_none(row.get("conditionId") or row.get("condition_id"))
    asset = string_or_none(row.get("asset"))
    outcome = string_or_none(row.get("outcome"))
    activity_id = make_activity_id(
        row=row,
        wallet=wallet,
        transaction_hash=transaction_hash,
        timestamp=timestamp,
        activity_type=activity_type,
        condition_id=condition_id,
        asset=asset,
        outcome=outcome,
    )
    return NormalizedActivity(
        id=activity_id,
        proxy_wallet=string_or_none(row.get("proxyWallet")) or wallet.lower(),
        timestamp=timestamp,
        type=activity_type,
        condition_id=condition_id,
        slug=string_or_none(row.get("slug")),
        event_slug=string_or_none(row.get("eventSlug") or row.get("event_slug")),
        title=string_or_none(row.get("title")),
        side=string_or_none(row.get("side")),
        outcome=outcome,
        asset=asset,
        price=decimal_or_none(row.get("price")),
        size=decimal_or_none(row.get("size")),
        usdc_size=decimal_or_none(row.get("usdcSize") or row.get("usdc_size")),
        transaction_hash=transaction_hash,
        raw=row,
    )


def make_activity_id(
    *,
    row: dict[str, Any],
    wallet: str,
    transaction_hash: str | None,
    timestamp: datetime,
    activity_type: str,
    condition_id: str | None,
    asset: str | None,
    outcome: str | None,
) -> str:
    if transaction_hash:
        seed = "|".join(
            [
                transaction_hash.lower(),
                activity_type,
                str(int(timestamp.timestamp())),
                condition_id or "",
                asset or "",
                outcome or "",
                string_or_none(row.get("side")) or "",
                str(row.get("size") or ""),
                str(row.get("usdcSize") or row.get("usdc_size") or ""),
            ]
        )
    else:
        seed = json.dumps(row, sort_keys=True, default=str)
    digest = hashlib.sha1(f"{wallet.lower()}|{seed}".encode("utf-8")).hexdigest()
    return digest


def parse_timestamp(value: Any) -> datetime:
    if isinstance(value, (int, float)):
        timestamp = float(value)
        # 兼容秒级/毫秒级时间戳；Polymarket 有时会返回 13 位毫秒值。
        if abs(timestamp) > 1e11:
            timestamp /= 1000
        return datetime.fromtimestamp(timestamp, tz=timezone.utc)
    if isinstance(value, str):
        if value.isdigit():
            timestamp = float(value)
            if abs(timestamp) > 1e11:
                timestamp /= 1000
            return datetime.fromtimestamp(timestamp, tz=timezone.utc)
        normalized = value.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            pass
        else:
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    raise PolymarketInputError("Activity 缺少有效 timestamp")


def decimal_or_none(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
