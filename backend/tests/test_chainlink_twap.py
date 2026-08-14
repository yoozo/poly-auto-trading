from datetime import datetime, timezone
from decimal import Decimal
import json
from types import SimpleNamespace

import pytest

from app.schemas.chainlink_twap import MarketPricePoint
from app.services import chainlink_twap
from app.services.chainlink_twap import market_price_context, parse_twap_message, subscription_payload


def test_subscription_payload_subscribes_both_twap_windows() -> None:
    payload = subscription_payload()

    assert payload["action"] == "subscribe"
    assert {item["topic"] for item in payload["subscriptions"]} == {
        "crypto_prices_twap_thirty",
        "crypto_prices_twap_sixty",
    }
    assert all(
        item["filters"] == '{"symbol":"btc/usd"}'
        for item in payload["subscriptions"]
    )
    assert all(item["type"] == "update" for item in payload["subscriptions"])


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


@pytest.mark.asyncio
async def test_market_price_context_prefers_chainlink_baseline(monkeypatch) -> None:
    start = datetime(2026, 8, 14, 2, 0, tzinfo=timezone.utc)
    current = SimpleNamespace(value=Decimal("65010"), observed_at=start)
    baseline = SimpleNamespace(value=Decimal("65000"), observed_at=start)
    monkeypatch.setattr(chainlink_twap, "latest_observation", lambda *args: async_value(current))
    monkeypatch.setattr(chainlink_twap, "baseline_observation", lambda *args: async_value(baseline))
    monkeypatch.setattr(chainlink_twap, "binance_baseline", lambda *args: async_value(None))

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


@pytest.mark.asyncio
async def test_market_price_context_marks_binance_startup_fallback(monkeypatch) -> None:
    start = datetime(2026, 8, 14, 2, 0, tzinfo=timezone.utc)
    current = SimpleNamespace(value=Decimal("64990"), observed_at=start)
    fallback = MarketPricePoint(source="binance", value=Decimal("65000"), observed_at=start)
    monkeypatch.setattr(chainlink_twap, "latest_observation", lambda *args: async_value(current))
    monkeypatch.setattr(chainlink_twap, "baseline_observation", lambda *args: async_value(None))
    monkeypatch.setattr(chainlink_twap, "binance_baseline", lambda *args: async_value(fallback))

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
