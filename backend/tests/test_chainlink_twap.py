from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from types import SimpleNamespace

import pytest

from app.schemas.chainlink_twap import MarketPricePoint
from app.schemas.candle import Candle
from app.api.routes_candles import merge_chainlink_history
from app.services import chainlink_twap
from app.services.chainlink_twap import (
    PolymarketPastResult,
    aggregate_spot_candle,
    candles_to_persist,
    market_price_context,
    notify_closed_market_signal,
    parse_polymarket_past_result,
    parse_polymarket_page_open_price,
    parse_twap_message,
    past_result_candle,
    polymarket_price_to_beat,
    subscription_payload,
    twap_stream_is_stale,
)


def test_subscription_payload_subscribes_spot_and_60s_twap() -> None:
    payload = subscription_payload()

    assert payload["action"] == "subscribe"
    assert {item["topic"] for item in payload["subscriptions"]} == {
        "crypto_prices_chainlink",
        "crypto_prices_twap_sixty",
    }
    assert all(
        item["filters"] == '{"symbol":"btc/usd"}'
        for item in payload["subscriptions"]
    )
    assert {item["topic"]: item["type"] for item in payload["subscriptions"]} == {
        "crypto_prices_chainlink": "*",
        "crypto_prices_twap_sixty": "update",
    }


def test_parse_twap_message_uses_full_accuracy_e18_value() -> None:
    message = json.dumps(
        {
            "topic": "crypto_prices_twap_sixty",
            "type": "update",
            "timestamp": 1_785_178_800_123,
            "payload": {
                "symbol": "btc/usd",
                "value": 65000.4,
                "full_accuracy_value": "65000500000000000000000",
                "timestamp": 1_785_178_800_000,
                "window_s": 60,
            },
        }
    )

    observation = parse_twap_message(message)

    assert observation is not None
    assert observation.value == Decimal("65000.5")
    assert observation.window_seconds == 60
    assert observation.observed_at == datetime.fromtimestamp(
        1_785_178_800, timezone.utc
    )


def test_parse_twap_message_rejects_topic_window_mismatch() -> None:
    message = json.dumps(
        {
            "topic": "crypto_prices_twap_sixty",
            "type": "update",
            "timestamp": 1_785_178_800_123,
            "payload": {
                "symbol": "btc/usd",
                "full_accuracy_value": "65000500000000000000000",
                "timestamp": 1_785_178_800_000,
                "window_s": 30,
            },
        }
    )

    assert parse_twap_message(message) is None


def test_parse_chainlink_spot_message() -> None:
    observation = parse_twap_message(
        json.dumps(
            {
                "topic": "crypto_prices_chainlink",
                "type": "update",
                "timestamp": 1_785_178_800_123,
                "payload": {
                    "symbol": "btc/usd",
                    "value": 63504.82,
                    "timestamp": 1_785_178_800_000,
                },
            }
        )
    )

    assert observation is not None
    assert observation.window_seconds == 0
    assert observation.value == Decimal("63504.82")


def test_twap_stream_stale_ignores_spot_but_accepts_either_twap_window() -> None:
    connected_at = datetime(2026, 8, 14, 4, 0, tzinfo=timezone.utc)
    now = connected_at + timedelta(seconds=61)

    assert twap_stream_is_stale(
        {"crypto_prices_chainlink": now},
        connected_at=connected_at,
        now=now,
        stall_seconds=60,
    )
    assert not twap_stream_is_stale(
        {"crypto_prices_twap_sixty": now - timedelta(seconds=1)},
        connected_at=connected_at,
        now=now,
        stall_seconds=60,
    )


def test_parse_polymarket_page_open_price_matches_exact_market_query() -> None:
    start = datetime(2026, 8, 14, 3, 0, tzinfo=timezone.utc)
    active_market = market(start=start, interval="5m", twap_window_seconds=60)
    page = (
        r'{\"state\":{\"data\":{\"openPrice\":63413.31962811328}},'
        r'\"queryKey\":[\"crypto-prices\",\"price\",\"BTC\",'
        r'\"2026-08-14T03:00:00Z\",\"fiveminute\",'
        r'\"2026-08-14T03:05:00Z\",true,60]}'
    )

    assert parse_polymarket_page_open_price(page, active_market) == Decimal("63413.31962811328")


def test_parse_polymarket_past_result_matches_exact_market_window() -> None:
    start = datetime(2026, 8, 14, 3, 40, tzinfo=timezone.utc)
    closed_market = market(start=start, interval="5m", twap_window_seconds=60)
    payload = {
        "data": {
            "results": [
                {
                    "startTime": "2026-08-14T03:40:00Z",
                    "endTime": "2026-08-14T03:45:00Z",
                    "openPrice": "63300.6962318655",
                    "closePrice": "63318.667178096744",
                }
            ]
        }
    }

    result = parse_polymarket_past_result(payload, closed_market)

    assert result is not None
    assert result.open_price == Decimal("63300.6962318655")
    assert result.close_price == Decimal("63318.667178096744")


def test_polymarket_past_result_builds_missing_candle_from_official_open_close() -> None:
    start = datetime(2026, 8, 14, 3, 40, tzinfo=timezone.utc)
    closed_market = market(start=start, interval="5m", twap_window_seconds=60)
    result = PolymarketPastResult(
        start_time=start,
        end_time=start + timedelta(minutes=5),
        open_price=Decimal("63300.70"),
        close_price=Decimal("63318.67"),
    )

    candle = past_result_candle(closed_market, result)

    assert candle.open == 63300.70
    assert candle.close == 63318.67
    assert candle.high == 63318.67
    assert candle.low == 63300.70
    assert candle.source == "polymarket"
    assert candle.is_closed is True
    assert candle.is_complete is False


@pytest.mark.asyncio
async def test_closed_market_signal_keeps_chainlink_close_and_adds_final_twap(monkeypatch) -> None:
    start = datetime(2026, 8, 14, 3, 55, tzinfo=timezone.utc)
    closed_market = market(start=start, interval="5m", twap_window_seconds=60)
    raw_candle = candle_at(start, source="chainlink").model_copy(
        update={"interval": "5m", "open": 65000, "high": 65010, "low": 64990, "close": 65005}
    )
    result = PolymarketPastResult(
        start_time=start,
        end_time=closed_market.end_time,
        open_price=Decimal("65020.25"),
        close_price=Decimal("65010.75"),
    )
    signal_events = []

    async def fake_list(*args, **kwargs):
        assert kwargs["start"] == start
        assert kwargs["end"] == start
        return [raw_candle]

    monkeypatch.setattr(chainlink_twap, "list_candles_between", fake_list)
    async def fake_handle(event):
        signal_events.append(event)

    monkeypatch.setattr(chainlink_twap.market_signal_pipeline, "handle_market_event", fake_handle)
    chainlink_twap._notified_past_result_market_ids.discard(closed_market.id)

    await notify_closed_market_signal(object(), closed_market, result)

    assert len(signal_events) == 1
    assert signal_events[0].candle == raw_candle
    assert signal_events[0].candle.close == 65005
    assert signal_events[0].metadata["polymarket_final"] == {
        "price_to_beat": "65020.25",
        "close_twap": "65010.75",
    }


def test_chainlink_spot_ticks_aggregate_ohlc_without_volume() -> None:
    first = chainlink_spot("65000", 1_786_675_201_000)
    high = chainlink_spot("65020", 1_786_675_215_000)
    close = chainlink_spot("64990", 1_786_675_225_000)

    candle = aggregate_spot_candle(None, first, "1m")
    candle = aggregate_spot_candle(candle, high, "1m")
    candle = aggregate_spot_candle(candle, close, "1m")

    assert candle.open == 65000
    assert candle.high == 65020
    assert candle.low == 64990
    assert candle.close == 64990
    assert candle.volume is None
    assert candle.source == "chainlink"


def test_binance_only_fills_history_before_chainlink_started() -> None:
    start = datetime(2026, 8, 14, 2, 0, tzinfo=timezone.utc)
    binance = [candle_at(start + timedelta(minutes=index), source="binance") for index in range(3)]
    chainlink = [candle_at(start + timedelta(minutes=1), source="chainlink")]

    merged = merge_chainlink_history(
        binance,
        chainlink,
        chainlink_started_at=start + timedelta(minutes=1),
    )

    assert [item.open_time for item in merged] == [start, start + timedelta(minutes=1)]
    assert [item.source for item in merged] == ["binance_fallback", "chainlink"]


def test_persistence_keeps_live_one_minute_as_replay_source() -> None:
    start = datetime(2026, 8, 14, 2, 0, tzinfo=timezone.utc)
    closed_five = candle_at(start, source="chainlink").model_copy(
        update={"interval": "5m", "is_closed": True}
    )
    live_one = candle_at(start + timedelta(minutes=1), source="chainlink").model_copy(
        update={"is_closed": False}
    )
    live_five = closed_five.model_copy(
        update={"open_time": start + timedelta(minutes=5), "is_closed": False}
    )

    persisted = candles_to_persist([closed_five], [live_one, live_five])

    assert [(item.interval, item.is_closed) for item in persisted] == [
        ("5m", True),
        ("1m", False),
    ]


@pytest.mark.asyncio
async def test_market_price_context_prefers_polymarket_price_to_beat(monkeypatch) -> None:
    start = datetime(2026, 8, 14, 2, 0, tzinfo=timezone.utc)
    current = SimpleNamespace(value=Decimal("65010"), observed_at=start)
    requested_windows = []

    async def latest(*args):
        requested_windows.append(("current", args[1]))
        return current

    monkeypatch.setattr(chainlink_twap, "latest_observation", latest)
    monkeypatch.setattr(
        chainlink_twap,
        "baseline_observation",
        lambda *args: (_ for _ in ()).throw(AssertionError("official baseline must win")),
    )
    monkeypatch.setattr(chainlink_twap, "binance_baseline", lambda *args: async_value(None))
    monkeypatch.setattr(
        chainlink_twap,
        "polymarket_price_to_beat",
        lambda *args: async_value(Decimal("65000")),
    )
    context = await market_price_context(
        object(),
        market(start=start, interval="5m", twap_window_seconds=60),
        now=start,
    )

    assert context is not None
    assert context.quality == "exact"
    assert context.baseline and context.baseline.source == "polymarket"
    assert context.difference == Decimal("10")
    assert context.direction == "up"
    assert context.current and context.current.value == Decimal("65010")
    assert context.settlement_twap and context.settlement_twap.value == Decimal("65010")
    assert requested_windows == [("current", 60)]


@pytest.mark.asyncio
async def test_market_price_context_marks_binance_startup_fallback(monkeypatch) -> None:
    start = datetime(2026, 8, 14, 2, 0, tzinfo=timezone.utc)
    current = SimpleNamespace(value=Decimal("64990"), observed_at=start)
    fallback = MarketPricePoint(source="binance", value=Decimal("65000"), observed_at=start)
    monkeypatch.setattr(chainlink_twap, "latest_observation", lambda *args: async_value(current))
    monkeypatch.setattr(chainlink_twap, "baseline_observation", lambda *args: async_value(None))
    monkeypatch.setattr(chainlink_twap, "binance_baseline", lambda *args: async_value(fallback))
    monkeypatch.setattr(chainlink_twap, "polymarket_price_to_beat", lambda *args: async_value(None))

    context = await market_price_context(
        object(),
        market(start=start, interval="15m"),
        now=start,
    )

    assert context is not None
    assert context.quality == "estimated_baseline"
    assert context.twap_window_seconds == 60
    assert context.direction == "down"
    assert context.warning and "可能存在误差" in context.warning


@pytest.mark.asyncio
async def test_closed_market_uses_polymarket_final_instead_of_latest_twap(monkeypatch) -> None:
    start = datetime(2026, 8, 14, 3, 40, tzinfo=timezone.utc)
    closed_market = market(start=start, interval="5m", twap_window_seconds=60)
    result = PolymarketPastResult(
        start_time=start,
        end_time=closed_market.end_time,
        open_price=Decimal("63300.70"),
        close_price=Decimal("63318.67"),
    )
    monkeypatch.setattr(chainlink_twap, "finalize_closed_market", lambda *args, **kwargs: async_value(result))
    monkeypatch.setattr(
        chainlink_twap,
        "latest_observation",
        lambda *args: (_ for _ in ()).throw(AssertionError("closed market must not use latest RTDS TWAP")),
    )

    context = await market_price_context(object(), closed_market, now=closed_market.end_time)

    assert context is not None
    assert context.quality == "exact"
    assert context.baseline and context.baseline.value == Decimal("63300.70")
    assert context.current and context.current.value == Decimal("63318.67")
    assert context.current.source == "polymarket"
    assert context.direction == "up"


@pytest.mark.asyncio
async def test_closed_market_waits_when_polymarket_final_is_not_ready(monkeypatch) -> None:
    start = datetime(2026, 8, 14, 3, 40, tzinfo=timezone.utc)
    closed_market = market(start=start, interval="5m", twap_window_seconds=60)
    monkeypatch.setattr(chainlink_twap, "finalize_closed_market", lambda *args, **kwargs: async_value(None))

    context = await market_price_context(object(), closed_market, now=closed_market.end_time)

    assert context is not None
    assert context.quality == "waiting_polymarket_final"
    assert context.current is None
    assert context.direction is None


@pytest.mark.asyncio
async def test_incomplete_polymarket_price_to_beat_is_only_cached_briefly(monkeypatch) -> None:
    start = datetime(2026, 8, 14, 2, 0, tzinfo=timezone.utc)
    requests = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"openPrice": 65000.5, "completed": False, "incomplete": True}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, *args, **kwargs):
            requests.append(kwargs)
            return FakeResponse()

    monkeypatch.setattr(chainlink_twap.httpx, "AsyncClient", lambda **kwargs: FakeClient())
    monkeypatch.setattr(
        chainlink_twap,
        "polymarket_page_price_to_beat",
        lambda *args: async_value(None),
    )
    chainlink_twap._price_to_beat_cache.clear()
    before = datetime.now(timezone.utc)
    active_market = market(start=start, interval="5m")

    value = await polymarket_price_to_beat(active_market)

    assert value == Decimal("65000.5")
    assert requests[0]["params"]["twapEnabled"] == "true"
    assert requests[0]["params"]["twapLookbackSeconds"] == 60
    assert chainlink_twap._price_to_beat_cache[active_market.id][1] <= before + timedelta(seconds=6)


async def async_value(value):
    return value


def market(*, start: datetime, interval: str, twap_window_seconds: int | None = None):
    minutes = 5 if interval == "5m" else 15
    return SimpleNamespace(
        id=f"btc-{interval}-{int(start.timestamp())}",
        slug=f"btc-updown-{interval}-{int(start.timestamp())}",
        interval=interval,
        twap_window_seconds=twap_window_seconds or 60,
        start_time=start,
        end_time=start + timedelta(minutes=minutes),
    )


def chainlink_spot(value: str, timestamp_ms: int):
    observed_at = datetime.fromtimestamp(timestamp_ms / 1000, timezone.utc)
    return chainlink_twap.ChainlinkTwapObservation(
        symbol="btc/usd",
        window_seconds=0,
        value=Decimal(value),
        observed_at=observed_at,
        published_at=observed_at,
        topic="crypto_prices_chainlink",
    )


def candle_at(open_time: datetime, *, source: str):
    return Candle(
        symbol="BTCUSD" if source == "chainlink" else "BTCUSDT",
        interval="1m",
        open_time=open_time,
        close_time=open_time + timedelta(seconds=59, milliseconds=999),
        open=65000,
        high=65010,
        low=64990,
        close=65005,
        volume=None if source == "chainlink" else 1,
        source=source,
    )
