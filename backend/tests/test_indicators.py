from datetime import datetime, timedelta, timezone

from app.schemas.candle import Candle
from app.services.indicators import calculate_indicator_points


def test_calculate_indicator_points() -> None:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    candles = [
        Candle(
            symbol="BTCUSDT",
            interval="1m",
            open_time=start + timedelta(minutes=index),
            close_time=start + timedelta(minutes=index + 1),
            open=100 + index,
            high=101 + index,
            low=99 + index,
            close=100 + index,
            volume=1,
            is_closed=True,
        )
        for index in range(40)
    ]

    points = calculate_indicator_points(candles, "1m")

    assert len(points) == len(candles)
    assert points[-1].rsi is not None
    assert points[-1].rsi_ema is not None
    assert points[-1].rsi_ema_diff is not None
    assert points[-1].bollinger.upper is not None
