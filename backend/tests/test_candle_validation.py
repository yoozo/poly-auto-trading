from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.schemas.candle import BollingerBands, Candle, IndicatorPoint
from app.services.binance_client import BinanceClient
from app.services.candle_store import chunked, upsert_candles


def _valid_candle_kwargs() -> dict:
    open_time = datetime(2026, 6, 18, 10, 0, tzinfo=timezone.utc)
    return {
        "symbol": "BTCUSDT",
        "interval": "1m",
        "open_time": open_time,
        "close_time": open_time + timedelta(minutes=1),
        "open": 100.0,
        "high": 101.0,
        "low": 99.0,
        "close": 100.5,
        "volume": 12.3,
        "is_closed": True,
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("open", float("nan")),
        ("high", float("inf")),
        ("low", float("-inf")),
        ("close", None),
        ("volume", float("nan")),
    ],
)
def test_candle_rejects_invalid_ohlcv(field: str, value: float | None) -> None:
    kwargs = _valid_candle_kwargs()
    kwargs[field] = value

    with pytest.raises(ValidationError):
        Candle(**kwargs)


@pytest.mark.parametrize(
    "override",
    [
        {"open_time": datetime(2026, 6, 18, 10, 0, tzinfo=timezone.utc), "close_time": datetime(2026, 6, 18, 10, 0, tzinfo=timezone.utc)},
        {"high": 99.0},
        {"low": 101.0},
        {"volume": -1.0},
    ],
)
def test_candle_rejects_invalid_shape(override: dict) -> None:
    kwargs = _valid_candle_kwargs()
    kwargs.update(override)

    with pytest.raises(ValidationError):
        Candle(**kwargs)


def test_indicator_fields_allow_null_values() -> None:
    point = IndicatorPoint(
        symbol="BTCUSDT",
        interval="1m",
        candle_time=datetime(2026, 6, 18, 10, 0, tzinfo=timezone.utc),
        rsi=None,
        rsi_ema=None,
        rsi_ema_diff=None,
        bollinger=BollingerBands(upper=None, middle=None, lower=None),
    )

    assert point.rsi is None
    assert point.bollinger.upper is None


@pytest.mark.parametrize(
    ("model", "field", "value"),
    [
        (IndicatorPoint, "rsi", float("nan")),
        (IndicatorPoint, "rsi_ema", float("inf")),
        (IndicatorPoint, "rsi_ema_diff", float("-inf")),
        (BollingerBands, "upper", float("nan")),
        (BollingerBands, "middle", float("inf")),
        (BollingerBands, "lower", float("-inf")),
    ],
)
def test_indicator_fields_reject_nan_and_infinity(model, field: str, value: float) -> None:
    if model is IndicatorPoint:
        kwargs = {
            "symbol": "BTCUSDT",
            "interval": "1m",
            "candle_time": datetime(2026, 6, 18, 10, 0, tzinfo=timezone.utc),
        }
    else:
        kwargs = {}
    kwargs[field] = value

    with pytest.raises(ValidationError):
        model(**kwargs)


def test_binance_parse_rejects_invalid_ohlcv(caplog: pytest.LogCaptureFixture) -> None:
    row = [
        1767225600000,
        "nan",
        "101",
        "99",
        "100.5",
        "12.3",
        1767225659999,
    ]

    with pytest.raises(ValueError, match="Invalid Binance kline"):
        BinanceClient._parse_kline("BTCUSDT", "1m", row)

    assert "Rejecting invalid Binance kline" in caplog.text


def test_candle_accepts_zero_volume_placeholder_for_raw_storage() -> None:
    kwargs = _valid_candle_kwargs()
    kwargs["close_time"] = kwargs["open_time"]
    kwargs["volume"] = 0

    candle = Candle(**kwargs)

    assert candle.open_time == candle.close_time


def test_binance_parse_klines_keeps_zero_duration_placeholder() -> None:
    valid_row = [
        1504684800000,
        "4476.13000000",
        "4597.79000000",
        "4469.82000000",
        "4548.13000000",
        "175.29482000",
        1504699199999,
    ]
    placeholder_row = [
        1504713600000,
        "4619.43000000",
        "4619.43000000",
        "4619.43000000",
        "4619.43000000",
        "0.00000000",
        1504713600000,
    ]

    page = BinanceClient._parse_klines_page("BTCUSDT", "4h", [valid_row, placeholder_row])

    assert page.raw_count == 2
    assert page.next_start_ms == 1504713600000 + 4 * 60 * 60 * 1000
    assert len(page.candles) == 2
    assert page.candles[1].close_time > page.candles[1].open_time
    assert page.candles[1].volume == 0


@pytest.mark.asyncio
async def test_upsert_revalidates_candles_before_writing() -> None:
    kwargs = _valid_candle_kwargs()
    kwargs["open"] = float("nan")
    bypassed = Candle.model_construct(**kwargs)

    with pytest.raises(ValueError, match="Invalid candle"):
        await upsert_candles(object(), [bypassed])  # type: ignore[arg-type]


def test_chunked_splits_large_candle_batches() -> None:
    assert chunked(list(range(2501)), 1000) == [
        list(range(0, 1000)),
        list(range(1000, 2000)),
        list(range(2000, 2501)),
    ]
