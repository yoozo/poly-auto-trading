from __future__ import annotations

from statistics import fmean

from app.schemas.candle import BollingerBands, Candle, IndicatorPoint, Interval


RSI_PERIOD = 14
RSI_EMA_PERIOD = 14
BOLLINGER_PERIOD = 20
BOLLINGER_STDDEV_MULTIPLIER = 2.0


def calculate_indicator_points(candles: list[Candle], interval: Interval) -> list[IndicatorPoint]:
    if not candles:
        return []

    closes = [candle.close for candle in candles]
    rsi_values = calculate_rsi_series(closes, RSI_PERIOD)
    rsi_ema_values = calculate_nullable_ema_series(rsi_values, RSI_EMA_PERIOD)
    bollinger_values = calculate_bollinger_series(closes)

    points: list[IndicatorPoint] = []
    for index, candle in enumerate(candles):
        rsi = rsi_values[index]
        rsi_ema = rsi_ema_values[index]
        rsi_ema_diff = rsi - rsi_ema if rsi is not None and rsi_ema is not None else None
        points.append(
            IndicatorPoint(
                symbol=candle.symbol,
                interval=interval,
                candle_time=candle.open_time,
                rsi=round(rsi, 4) if rsi is not None else None,
                rsi_ema=round(rsi_ema, 4) if rsi_ema is not None else None,
                rsi_ema_diff=round(rsi_ema_diff, 4) if rsi_ema_diff is not None else None,
                bollinger=bollinger_values[index],
            )
        )
    return points


def calculate_rsi_series(closes: list[float], period: int) -> list[float | None]:
    if len(closes) <= period:
        return [None] * len(closes)

    values: list[float | None] = [None] * len(closes)
    gains: list[float] = []
    losses: list[float] = []

    for previous, current in zip(closes[:period], closes[1 : period + 1]):
        change = current - previous
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))

    average_gain = fmean(gains)
    average_loss = fmean(losses)
    values[period] = rsi_from_average_gain_loss(average_gain, average_loss)

    for index in range(period + 1, len(closes)):
        change = closes[index] - closes[index - 1]
        gain = max(change, 0.0)
        loss = max(-change, 0.0)
        average_gain = ((average_gain * (period - 1)) + gain) / period
        average_loss = ((average_loss * (period - 1)) + loss) / period
        values[index] = rsi_from_average_gain_loss(average_gain, average_loss)

    return values


def rsi_from_average_gain_loss(average_gain: float, average_loss: float) -> float:
    if average_loss == 0:
        return 100.0
    relative_strength = average_gain / average_loss
    return 100 - (100 / (1 + relative_strength))


def calculate_nullable_ema_series(values: list[float | None], period: int) -> list[float | None]:
    output: list[float | None] = [None] * len(values)
    seeded_values: list[float] = []
    ema: float | None = None
    multiplier = 2 / (period + 1)

    for index, value in enumerate(values):
        if value is None:
            continue
        if ema is None:
            seeded_values.append(value)
            if len(seeded_values) < period:
                continue
            ema = fmean(seeded_values[-period:])
        else:
            ema = (value - ema) * multiplier + ema
        output[index] = ema

    return output


def calculate_bollinger_series(closes: list[float]) -> list[BollingerBands]:
    values: list[BollingerBands] = []
    rolling_sum = 0.0
    rolling_square_sum = 0.0
    for index in range(len(closes)):
        close = closes[index]
        rolling_sum += close
        rolling_square_sum += close * close
        if index >= BOLLINGER_PERIOD:
            expired = closes[index - BOLLINGER_PERIOD]
            rolling_sum -= expired
            rolling_square_sum -= expired * expired
        if index + 1 < BOLLINGER_PERIOD:
            values.append(BollingerBands())
            continue
        # Bollinger 只需要固定窗口的均值和总体标准差，滚动累计避免大窗口重复切片。
        middle = rolling_sum / BOLLINGER_PERIOD
        variance = max(0.0, (rolling_square_sum / BOLLINGER_PERIOD) - (middle * middle))
        deviation = variance**0.5
        values.append(
            BollingerBands(
                upper=round(middle + deviation * BOLLINGER_STDDEV_MULTIPLIER, 4),
                middle=round(middle, 4),
                lower=round(middle - deviation * BOLLINGER_STDDEV_MULTIPLIER, 4),
            )
        )
    return values
