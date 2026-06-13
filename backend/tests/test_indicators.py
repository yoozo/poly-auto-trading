from datetime import datetime, timedelta, timezone
from statistics import fmean, pstdev

from app.schemas.candle import BollingerBands
from app.schemas.candle import Candle
from app.services.indicators import BOLLINGER_PERIOD, calculate_bollinger_series, calculate_indicator_points


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
    assert points[-1].candle_time == candles[-1].open_time
    assert points[-1].rsi is not None
    assert points[-1].rsi_ema is not None
    assert points[-1].rsi_ema_diff is not None
    assert points[-1].bollinger.upper is not None


def test_bollinger_rolling_matches_previous_window_algorithm() -> None:
    closes = [100 + ((index % 7) * 1.3) - (index * 0.2) for index in range(80)]

    assert calculate_bollinger_series(closes) == previous_bollinger_series(closes)


def previous_bollinger_series(closes: list[float]) -> list[BollingerBands]:
    values: list[BollingerBands] = []
    for index in range(len(closes)):
        if index + 1 < BOLLINGER_PERIOD:
            values.append(BollingerBands())
            continue
        window = closes[index + 1 - BOLLINGER_PERIOD : index + 1]
        middle = fmean(window)
        deviation = pstdev(window)
        values.append(
            BollingerBands(
                upper=round(middle + deviation * 2.0, 4),
                middle=round(middle, 4),
                lower=round(middle - deviation * 2.0, 4),
            )
        )
    return values
