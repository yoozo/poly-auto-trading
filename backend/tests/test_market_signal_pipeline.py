from datetime import datetime, timedelta, timezone

import pytest

from app.schemas.candle import Candle
from app.schemas.market_signal import MarketDataEvent
from app.services.market_signal_pipeline import MarketSignalPipeline


def make_candle(index: int, close: float | None = None) -> Candle:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=index)
    price = close if close is not None else 100 + index
    return Candle(
        symbol="BTCUSDT",
        interval="1m",
        open_time=start,
        close_time=start + timedelta(minutes=1),
        open=price,
        high=price + 1,
        low=price - 1,
        close=price,
        volume=1,
        is_closed=True,
    )


def test_build_signal_input_preserves_event_source_and_indicator_context() -> None:
    pipeline = MarketSignalPipeline()
    candles = [make_candle(index) for index in range(40)]
    event = MarketDataEvent(source="binance_ws", candle=candles[-1])

    signal_input = pipeline.build_signal_input(event, candles)

    assert signal_input.candle == candles[-1]
    assert signal_input.indicator is not None
    assert signal_input.indicator.candle_time == candles[-1].open_time
    assert signal_input.market_events == [event]
    assert signal_input.factors["sources"] == ["binance_ws"]
    assert signal_input.factors["technical_indicators"] is not None


def test_latest_market_payload_uses_live_window_snapshot() -> None:
    pipeline = MarketSignalPipeline()
    pipeline.replace_live_candles("BTCUSDT", "1m", [make_candle(index) for index in range(40)])

    payload = pipeline.latest_market_payload("BTCUSDT", "1m")

    assert payload is not None
    assert payload["type"] == "market.candle"
    assert payload["symbol"] == "BTCUSDT"
    assert payload["interval"] == "1m"
    assert payload["candle"]["open_time"] == "2026-01-01T00:39:00Z"  # type: ignore[index]
    assert set(payload) == {"type", "symbol", "interval", "candle"}


@pytest.mark.asyncio
async def test_handle_market_event_merges_candle_window_then_dispatches(monkeypatch) -> None:
    dispatched = []
    pipeline = MarketSignalPipeline()
    pipeline.replace_live_candles("BTCUSDT", "1m", [make_candle(index) for index in range(39)])

    async def fake_dispatch(signal_input):
        dispatched.append(signal_input)

    monkeypatch.setattr(pipeline, "dispatch", fake_dispatch)

    signal_input = await pipeline.handle_market_event(
        MarketDataEvent(source="binance_ws", candle=make_candle(39))
    )

    assert dispatched == [signal_input]
    assert signal_input.indicator is not None
    assert signal_input.indicator.candle_time == make_candle(39).open_time


@pytest.mark.asyncio
async def test_check_notifications_records_signals_before_delivery(monkeypatch) -> None:
    calls = []
    pipeline = MarketSignalPipeline()
    candle = make_candle(39)
    signal_input = pipeline.build_signal_input(
        MarketDataEvent(source="binance_ws", candle=candle),
        [make_candle(index) for index in range(40)],
    )
    signal_records = [object()]

    class FakeSession:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return None

    async def fake_record_signal_input_analysis(session, received_signal_input):
        calls.append(("record", received_signal_input))
        return signal_records

    async def fake_process_signal_notifications(session, received_signals):
        calls.append(("notify", received_signals))

    monkeypatch.setattr("app.services.market_signal_pipeline.AsyncSessionLocal", FakeSession)
    monkeypatch.setattr(
        "app.services.market_signal_pipeline.record_signal_input_analysis",
        fake_record_signal_input_analysis,
    )
    monkeypatch.setattr(
        "app.services.market_signal_pipeline.process_signal_notifications",
        fake_process_signal_notifications,
    )

    await pipeline._check_notifications(signal_input)

    assert calls == [("record", signal_input), ("notify", signal_records)]
