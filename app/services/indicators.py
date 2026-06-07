from __future__ import annotations

from datetime import datetime, timezone
from statistics import fmean, pstdev

from app.schemas import BollingerBands, Candle, IndicatorInterval, IndicatorSnapshot, Interval


RSI_PERIOD = 14
BOLLINGER_PERIOD = 20
BOLLINGER_STDDEV_MULTIPLIER = 2.0
TREND_LOOKBACK = 5


def build_indicator_snapshot(symbol: str, candles_by_interval: dict[Interval, list[Candle]]) -> IndicatorSnapshot:
    intervals: dict[Interval, IndicatorInterval] = {}
    updated_at = datetime.now(timezone.utc)

    for interval, candles in candles_by_interval.items():
        closed_candles = [candle for candle in candles if candle.is_closed]
        intervals[interval] = calculate_interval_indicators(interval, closed_candles, updated_at=updated_at)

    return IndicatorSnapshot(symbol=symbol.upper(), updated_at=updated_at, intervals=intervals)


def calculate_interval_indicators(
    interval: Interval,
    candles: list[Candle],
    updated_at: datetime | None = None,
) -> IndicatorInterval:
    closes = [candle.close for candle in candles]
    rsi = calculate_rsi(closes)
    bollinger = calculate_bollinger_bands(closes)
    trend = calculate_trend(closes)
    return IndicatorInterval(
        interval=interval,
        rsi=rsi,
        bollinger=bollinger,
        trend=trend,
        updated_at=updated_at or datetime.now(timezone.utc),
    )


def calculate_rsi(closes: list[float], period: int = RSI_PERIOD) -> float | None:
    if len(closes) <= period:
        return None

    gains: list[float] = []
    losses: list[float] = []
    for previous, current in zip(closes[-period - 1 : -1], closes[-period:]):
        change = current - previous
        gains.append(max(change, 0.0))
        losses.append(abs(min(change, 0.0)))

    average_gain = fmean(gains)
    average_loss = fmean(losses)
    if average_loss == 0:
        return 100.0

    relative_strength = average_gain / average_loss
    return round(100 - (100 / (1 + relative_strength)), 2)


def calculate_bollinger_bands(
    closes: list[float],
    period: int = BOLLINGER_PERIOD,
    stddev_multiplier: float = BOLLINGER_STDDEV_MULTIPLIER,
) -> BollingerBands:
    if len(closes) < period:
        return BollingerBands()

    window = closes[-period:]
    middle = fmean(window)
    deviation = pstdev(window)
    return BollingerBands(
        upper=round(middle + stddev_multiplier * deviation, 2),
        middle=round(middle, 2),
        lower=round(middle - stddev_multiplier * deviation, 2),
    )


def calculate_trend(closes: list[float], lookback: int = TREND_LOOKBACK) -> str:
    if len(closes) <= lookback:
        return "insufficient_data"

    current = closes[-1]
    previous = closes[-lookback - 1]
    change_ratio = (current - previous) / previous if previous else 0.0
    if change_ratio > 0.001:
        return "up"
    if change_ratio < -0.001:
        return "down"
    return "flat"

