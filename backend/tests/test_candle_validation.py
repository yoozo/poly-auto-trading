from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.schemas.candle import BollingerBands, Candle, IndicatorPoint
from app.services.binance_client import BinanceClient
from app.services.candle_store import upsert_candles


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


@pytest.mark.asyncio
async def test_upsert_revalidates_candles_before_writing() -> None:
    kwargs = _valid_candle_kwargs()
    kwargs["open"] = float("nan")
    bypassed = Candle.model_construct(**kwargs)

    with pytest.raises(ValueError, match="Invalid candle"):
        await upsert_candles(object(), [bypassed])  # type: ignore[arg-type]
