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
    aggregate_spot_candle,
    candles_to_persist,
    market_price_context,
    parse_twap_message,
    subscription_payload,
)


def test_subscription_payload_subscribes_both_twap_windows() -> None:
    payload = subscription_payload()

    assert payload["action"] == "subscribe"
    assert {item["topic"] for item in payload["subscriptions"]} == {
        "crypto_prices_chainlink",
        "crypto_prices_twap_thirty",
        "crypto_prices_twap_sixty",
    }
    assert all(
        item["filters"] == '{"symbol":"btc/usd"}'
        for item in payload["subscriptions"]
    )
    assert {item["topic"]: item["type"] for item in payload["subscriptions"]} == {
        "crypto_prices_chainlink": "*",
        "crypto_prices_twap_thirty": "update",
        "crypto_prices_twap_sixty": "update",
    }


def test_parse_twap_message_uses_full_accuracy_e18_value() -> None:
    message = json.dumps(
        {
            "topic": "crypto_prices_twap_thirty",
            "type": "update",
            "timestamp": 1_785_178_800_123,
            "payload": {
                "symbol": "btc/usd",
                "value": 65000.4,
                "full_accuracy_value": "65000500000000000000000",
                "timestamp": 1_785_178_800_000,
                "window_s": 30,
            },
        }
    )

    observation = parse_twap_message(message)

    assert observation is not None
    assert observation.value == Decimal("65000.5")
    assert observation.window_seconds == 30
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
async def test_market_price_context_prefers_chainlink_baseline(monkeypatch) -> None:
    start = datetime(2026, 8, 14, 2, 0, tzinfo=timezone.utc)
    current = SimpleNamespace(value=Decimal("65010"), observed_at=start)
    baseline = SimpleNamespace(value=Decimal("65000"), observed_at=start)
    requested_windows = []

    async def latest(*args):
        requested_windows.append(("current", args[1]))
        return current

    async def starting(*args):
        requested_windows.append(("baseline", args[1]))
        return baseline

    async def unexpected_price_to_beat(*args):
        raise AssertionError("exact Chainlink baseline must not call the fallback endpoint")

    monkeypatch.setattr(chainlink_twap, "latest_observation", latest)
    monkeypatch.setattr(chainlink_twap, "baseline_observation", starting)
    monkeypatch.setattr(chainlink_twap, "binance_baseline", lambda *args: async_value(None))
    monkeypatch.setattr(chainlink_twap, "polymarket_price_to_beat", unexpected_price_to_beat)

    context = await market_price_context(
        object(),
        market(start=start, interval="5m"),
        now=start,
    )

    assert context is not None
    assert context.quality == "exact"
    assert context.baseline and context.baseline.source == "chainlink"
    assert context.difference == Decimal("10")
    assert context.direction == "up"
    assert context.current and context.current.value == Decimal("65010")
    assert context.settlement_twap and context.settlement_twap.value == Decimal("65010")
    assert requested_windows == [("current", 30), ("baseline", 30)]


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


async def async_value(value):
    return value


def market(*, start: datetime, interval: str):
    minutes = 5 if interval == "5m" else 15
    return SimpleNamespace(
        id=f"btc-{interval}-{int(start.timestamp())}",
        interval=interval,
        start_time=start,
        end_time=start.replace(minute=start.minute + minutes),
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
