from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.schemas.candle import Interval

CANDLE_INTERVAL_MS: dict[Interval, int] = {
    "1m": 60_000,
    "5m": 5 * 60_000,
    "15m": 15 * 60_000,
    "30m": 30 * 60_000,
    "1h": 60 * 60_000,
    "4h": 4 * 60 * 60_000,
    "12h": 12 * 60 * 60_000,
    "1d": 24 * 60 * 60_000,
    "1w": 7 * 24 * 60 * 60_000,
}

BINANCE_WEEK_ANCHOR_MS = int(datetime(1970, 1, 5, tzinfo=timezone.utc).timestamp() * 1000)


def align_interval_open_ms(time_ms: int, interval: Interval) -> int:
    interval_ms = CANDLE_INTERVAL_MS[interval]
    if interval == "1w":
        return ((time_ms - BINANCE_WEEK_ANCHOR_MS) // interval_ms) * interval_ms + BINANCE_WEEK_ANCHOR_MS
    return (time_ms // interval_ms) * interval_ms


def latest_closed_open_ms(time_ms: int, interval: Interval) -> int:
    # REST 回填只落已闭合 K 线，任务终点使用最后一根已闭合 K 线的 open_time。
    return align_interval_open_ms(time_ms, interval) - CANDLE_INTERVAL_MS[interval]


def validate_aligned_open_time(open_ms: int, interval: Interval) -> None:
    if align_interval_open_ms(open_ms, interval) != open_ms:
        raise ValueError(f"kline open time is not aligned to {interval}: {open_ms}")


def standard_close_time(open_time: datetime, interval: Interval) -> datetime:
    return open_time + timedelta(milliseconds=CANDLE_INTERVAL_MS[interval] - 1)


def kline_open_ms(row: Any) -> int | None:
    if not isinstance(row, list) or not row:
        return None
    try:
        return int(row[0])
    except (TypeError, ValueError):
        return None
