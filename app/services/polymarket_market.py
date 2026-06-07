from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime, timezone
from typing import Any

from app.clients.polymarket_client import PolymarketClient
from app.core.config import settings
from app.schemas import MarketResult, PolyMarket
from app.services.state_store import StateStore, state_store


BTC_PATTERN = re.compile(r"\b(bitcoin|btc)\b", re.IGNORECASE)
INTERVAL_PATTERNS = {
    "5m": re.compile(r"\b(5m|5\s*min|5\s*minute)", re.IGNORECASE),
    "15m": re.compile(r"\b(15m|15\s*min|15\s*minute)", re.IGNORECASE),
}


class PolymarketMarketService:
    def __init__(self, store: StateStore = state_store) -> None:
        self._store = store
        self._client = PolymarketClient(settings.polymarket_gamma_base_url)

    async def refresh_once(self) -> None:
        self._store.set_service_health("polymarket_market_refresh", "running")
        try:
            events = await self._fetch_candidate_events()
            markets = self._parse_btc_markets(events)
            self._store.set_markets(markets)
            self._store.set_service_health("polymarket_market_refresh", "running")
        except Exception as exc:
            self._store.set_service_health("polymarket_market_refresh", "error", last_error=str(exc))

    async def run_refresh_loop(self) -> None:
        while True:
            try:
                await self.refresh_once()
                await asyncio.sleep(settings.polymarket_refresh_seconds)
            except asyncio.CancelledError:
                self._store.set_service_health("polymarket_market_refresh", "stopped")
                raise

    async def fetch_market_result(self, event_slug: str) -> MarketResult | None:
        event = await self._client.fetch_event_by_slug(event_slug)
        if not event:
            return None

        parsed = self._parse_btc_markets([event], include_ended=True)
        market = next((item for item in parsed if item.event_slug == event_slug), None)
        if not market:
            return None

        return MarketResult(
            event_slug=event_slug,
            market_id=market.id,
            title=market.title,
            end_time=market.end_time,
            outcomes=market.outcomes,
            outcome_prices=market.outcome_prices,
            winning_outcome=market.winning_outcome,
            result_status=market.result_status,
        )

    def _parse_btc_markets(self, events: list[dict], include_ended: bool = False) -> list[PolyMarket]:
        parsed: list[PolyMarket] = []
        now = datetime.now(timezone.utc)
        for event in events:
            event_title = _first_string(event, "title", "question", "name")
            event_slug = _first_string(event, "slug")
            event_id = str(event.get("id") or event.get("eventId") or event_slug or "")
            event_text = f"{event_title} {event_slug}"
            event_markets = event.get("markets") if isinstance(event.get("markets"), list) else []

            for raw_market in event_markets:
                market = self._parse_market(raw_market, event_id, event_slug, event_title, event_text)
                if market and (include_ended or market.end_time is None or market.end_time > now):
                    parsed.append(market)

        return sorted(parsed, key=lambda market: (market.end_time or datetime.max.replace(tzinfo=timezone.utc), market.id))[:8]

    async def _fetch_candidate_events(self) -> list[dict]:
        slugs = _candidate_btc_slugs(settings.polymarket_slug_window_count, settings.polymarket_slug_lookback_count)
        semaphore = asyncio.Semaphore(10)

        async def fetch_slug(slug: str) -> dict | None:
            async with semaphore:
                return await self._client.fetch_event_by_slug(slug)

        candidates = await asyncio.gather(*(fetch_slug(slug) for slug in slugs))
        events = [event for event in candidates if event]

        if events or not settings.polymarket_use_events_fallback:
            return events

        return await self._client.fetch_active_events(limit=100)

    def _parse_market(
        self,
        raw_market: dict,
        event_id: str,
        event_slug: str,
        event_title: str,
        event_text: str,
    ) -> PolyMarket | None:
        title = _first_string(raw_market, "question", "title", "name")
        slug = _first_string(raw_market, "slug")
        text = f"{event_text} {title} {slug}"
        if not BTC_PATTERN.search(text):
            return None

        interval = _detect_interval(text)
        if interval is None:
            return None

        token_ids = _extract_token_ids(raw_market)
        if len(token_ids) < 2:
            return None

        market_id = str(raw_market.get("id") or raw_market.get("marketId") or raw_market.get("conditionId") or slug)
        condition_id = str(raw_market.get("conditionId") or raw_market.get("condition_id") or "")
        end_time = _parse_datetime(_first_string(raw_market, "endDate", "end_date", "endTime", "end_time", "closedTime"))
        outcomes = _extract_outcomes(raw_market)
        outcome_prices = _extract_outcome_prices(raw_market)
        winning_outcome = _winning_outcome(outcomes, outcome_prices)
        result_status = _result_status(raw_market, end_time, winning_outcome)

        return PolyMarket(
            id=market_id,
            title=title or event_title or market_id,
            interval=interval,
            condition_id=condition_id,
            yes_token_id=token_ids[0],
            no_token_id=token_ids[1],
            end_time=end_time,
            best_bid=_maybe_float(raw_market.get("bestBid")),
            best_ask=_maybe_float(raw_market.get("bestAsk")),
            spread=_spread(raw_market),
            liquidity=_maybe_float(raw_market.get("liquidity") or raw_market.get("liquidityNum")),
            status=str(raw_market.get("status") or "active"),
            event_id=event_id or None,
            event_slug=event_slug or None,
            event_title=event_title or None,
            outcomes=outcomes,
            outcome_prices=outcome_prices,
            winning_outcome=winning_outcome,
            result_status=result_status,
        )


def _detect_interval(text: str) -> str | None:
    for interval, pattern in INTERVAL_PATTERNS.items():
        if pattern.search(text):
            return interval
    return None


def _extract_token_ids(market: dict) -> list[str]:
    for key in ("clobTokenIds", "clob_token_ids"):
        value = market.get(key)
        parsed = _parse_json_if_string(value)
        if isinstance(parsed, list):
            return [str(item) for item in parsed if item]

    tokens: list[str] = []
    for outcome in _parse_json_if_string(market.get("outcomes")) or []:
        if isinstance(outcome, dict):
            token = outcome.get("clobTokenId") or outcome.get("token_id") or outcome.get("tokenId")
            if token:
                tokens.append(str(token))
    return tokens


def _extract_outcomes(market: dict) -> list[str]:
    parsed = _parse_json_if_string(market.get("outcomes"))
    if not isinstance(parsed, list):
        return []
    outcomes: list[str] = []
    for outcome in parsed:
        if isinstance(outcome, dict):
            value = outcome.get("name") or outcome.get("title") or outcome.get("outcome")
            if value:
                outcomes.append(str(value))
        elif outcome:
            outcomes.append(str(outcome))
    return outcomes


def _extract_outcome_prices(market: dict) -> list[float | None]:
    parsed = _parse_json_if_string(market.get("outcomePrices"))
    if not isinstance(parsed, list):
        return []
    return [_maybe_float(item) for item in parsed]


def _winning_outcome(outcomes: list[str], outcome_prices: list[float | None]) -> str | None:
    if not outcomes or not outcome_prices:
        return None
    resolved_index: int | None = None
    for index, price in enumerate(outcome_prices):
        if price is not None and price >= 0.99:
            resolved_index = index
            break
    if resolved_index is None or resolved_index >= len(outcomes):
        return None
    return outcomes[resolved_index]


def _result_status(raw_market: dict, end_time: datetime | None, winning_outcome: str | None) -> str:
    if winning_outcome:
        return "resolved"
    now = datetime.now(timezone.utc)
    is_closed = bool(raw_market.get("closed")) or bool(raw_market.get("closedTime"))
    if is_closed or (end_time is not None and end_time <= now):
        return "pending"
    return "open"


def _parse_json_if_string(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _first_string(payload: dict, *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if value:
            return str(value)
    return ""


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _maybe_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _spread(market: dict) -> float | None:
    best_bid = _maybe_float(market.get("bestBid"))
    best_ask = _maybe_float(market.get("bestAsk"))
    if best_bid is None or best_ask is None:
        return None
    return round(best_ask - best_bid, 4)


polymarket_market_service = PolymarketMarketService()


def _candidate_btc_slugs(window_count: int, lookback_count: int = 4) -> list[str]:
    now = int(time.time())
    slugs: list[str] = []
    for interval, step in (("5m", 300), ("15m", 900)):
        current = now - (now % step)
        for offset in range(-max(lookback_count, 0), max(window_count, 1)):
            timestamp = current + offset * step
            slugs.append(f"btc-updown-{interval}-{timestamp}")
            slugs.append(f"btc-up-or-down-{interval}-{timestamp}")
    return slugs
