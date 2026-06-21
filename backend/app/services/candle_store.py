from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
import logging
from typing import TypeVar

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Candle as CandleModel
from app.db.models import CandleUnavailableRange
from app.schemas.candle import Candle, Interval

logger = logging.getLogger(__name__)

CANDLE_UPSERT_BATCH_SIZE = 1000
T = TypeVar("T")


def _validate_candle(candle: Candle) -> Candle:
    try:
        return Candle.model_validate(candle.model_dump())
    except Exception as exc:
        logger.warning(
            "Rejecting invalid candle",
            extra={
                "symbol": getattr(candle, "symbol", None),
                "interval": getattr(candle, "interval", None),
                "open_time": getattr(candle, "open_time", None),
            },
            exc_info=exc,
        )
        raise ValueError("Invalid candle") from exc


def _model_to_candle(model: CandleModel) -> Candle:
    # 历史库里如果已有异常 K 线，接口出口也必须重新走 schema 校验。
    return Candle(
        symbol=model.symbol,
        interval=model.interval,  # type: ignore[arg-type]
        open_time=model.open_time,
        close_time=model.close_time,
        open=float(model.open),
        high=float(model.high),
        low=float(model.low),
        close=float(model.close),
        volume=float(model.volume),
        is_closed=model.is_closed,
    )


async def upsert_candles(session: AsyncSession, candles: list[Candle]) -> None:
    if not candles:
        return

    validated_candles = [_validate_candle(candle) for candle in candles]
    for batch in chunked(validated_candles, CANDLE_UPSERT_BATCH_SIZE):
        await upsert_candle_batch(session, batch)
    await session.commit()


async def upsert_candle_batch(session: AsyncSession, candles: list[Candle]) -> None:
    rows = [candle_to_row(candle) for candle in candles]
    statement = insert(CandleModel).values(rows)
    await session.execute(
        statement.on_conflict_do_update(
            constraint="uq_candles_symbol_interval_open_time",
            set_={
                "close_time": statement.excluded.close_time,
                "open": statement.excluded.open,
                "high": statement.excluded.high,
                "low": statement.excluded.low,
                "close": statement.excluded.close,
                "volume": statement.excluded.volume,
                "is_closed": statement.excluded.is_closed,
            },
        )
    )


def candle_to_row(candle: Candle) -> dict[str, object]:
    return {
        "symbol": candle.symbol.upper(),
        "interval": candle.interval,
        "open_time": candle.open_time,
        "close_time": candle.close_time,
        "open": Decimal(str(candle.open)),
        "high": Decimal(str(candle.high)),
        "low": Decimal(str(candle.low)),
        "close": Decimal(str(candle.close)),
        "volume": Decimal(str(candle.volume)),
        "is_closed": candle.is_closed,
    }


def chunked(items: list[T], size: int) -> list[list[T]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


async def list_candles(
    session: AsyncSession,
    symbol: str,
    interval: Interval,
    limit: int,
) -> list[Candle]:
    statement = (
        select(CandleModel)
        .where(CandleModel.symbol == symbol.upper(), CandleModel.interval == interval)
        .order_by(CandleModel.open_time.desc())
        .limit(limit)
    )
    result = await session.scalars(statement)
    models = list(reversed(result.all()))
    return [_model_to_candle(model) for model in models]


async def list_candles_between(
    session: AsyncSession,
    symbol: str,
    interval: Interval,
    start: datetime,
    end: datetime,
) -> list[Candle]:
    statement = (
        select(CandleModel)
        .where(
            CandleModel.symbol == symbol.upper(),
            CandleModel.interval == interval,
            CandleModel.open_time >= start,
            CandleModel.open_time <= end,
        )
        .order_by(CandleModel.open_time.asc())
    )
    result = await session.scalars(statement)
    return [_model_to_candle(model) for model in result.all()]


async def list_candles_from(
    session: AsyncSession,
    symbol: str,
    interval: Interval,
    start: datetime,
    limit: int,
) -> list[Candle]:
    statement = (
        select(CandleModel)
        .where(
            CandleModel.symbol == symbol.upper(),
            CandleModel.interval == interval,
            CandleModel.open_time >= start,
        )
        .order_by(CandleModel.open_time.asc())
        .limit(limit)
    )
    result = await session.scalars(statement)
    return [_model_to_candle(model) for model in result.all()]


async def get_latest_candle(
    session: AsyncSession,
    symbol: str,
    interval: Interval,
) -> Candle | None:
    statement = (
        select(CandleModel)
        .where(CandleModel.symbol == symbol.upper(), CandleModel.interval == interval)
        .order_by(CandleModel.open_time.desc())
        .limit(1)
    )
    model = await session.scalar(statement)
    if model is None:
        return None
    return _model_to_candle(model)


async def get_earliest_candle_time(
    session: AsyncSession,
    symbol: str,
    interval: Interval | None = None,
) -> datetime | None:
    filters = [CandleModel.symbol == symbol.upper()]
    if interval is not None:
        filters.append(CandleModel.interval == interval)
    return await session.scalar(select(func.min(CandleModel.open_time)).where(*filters))


async def list_candle_missing_ranges(
    session: AsyncSession,
    *,
    symbol: str,
    interval: Interval,
    start: datetime,
    end: datetime,
) -> list[tuple[datetime, datetime]]:
    if start > end:
        return []
    interval_delta = timedelta(milliseconds=interval_to_ms(interval))
    previous_open_time = func.lag(CandleModel.open_time).over(order_by=CandleModel.open_time.asc()).label("prev_open_time")
    ordered = (
        select(
            CandleModel.open_time.label("open_time"),
            previous_open_time,
        )
        .where(
            CandleModel.symbol == symbol.upper(),
            CandleModel.interval == interval,
            CandleModel.open_time >= start,
            CandleModel.open_time <= end,
        )
        .order_by(CandleModel.open_time.asc())
        .subquery()
    )
    rows = await session.execute(
        select(ordered.c.prev_open_time, ordered.c.open_time)
        .where(
            ordered.c.prev_open_time.is_not(None),
            func.extract("epoch", ordered.c.open_time - ordered.c.prev_open_time) > interval_delta.total_seconds(),
        )
        .order_by(ordered.c.prev_open_time.asc())
    )
    missing_ranges: list[tuple[datetime, datetime]] = []
    for prev_open_time, next_open_time in rows.all():
        missing_start = prev_open_time + interval_delta
        missing_end = next_open_time - interval_delta
        if missing_start <= missing_end:
            missing_ranges.append((missing_start, missing_end))
    return missing_ranges


async def list_candle_ranges(session: AsyncSession, symbol: str) -> dict[str, dict[str, datetime | int | None]]:
    rows = await session.execute(
        select(
            CandleModel.interval,
            func.count(CandleModel.id),
            func.min(CandleModel.open_time),
            func.max(CandleModel.open_time),
        )
        .where(CandleModel.symbol == symbol.upper())
        .group_by(CandleModel.interval)
    )
    return {
        str(interval): {
            "count": int(count),
            "min_open_time": min_open,
            "max_open_time": max_open,
        }
        for interval, count, min_open, max_open in rows.all()
    }


async def upsert_candle_unavailable_range(
    session: AsyncSession,
    *,
    symbol: str,
    interval: Interval,
    start_ms: int,
    end_ms: int,
    source: str = "binance_rest",
    reason: str = "",
) -> None:
    if start_ms > end_ms:
        return
    statement = insert(CandleUnavailableRange).values(
        {
            "source": source,
            "symbol": symbol.upper(),
            "interval": interval,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "reason": reason,
        }
    )
    await session.execute(
        statement.on_conflict_do_update(
            constraint="uq_candle_unavailable_range",
            set_={
                "reason": statement.excluded.reason,
                "updated_at": func.now(),
            },
        )
    )


async def list_candle_unavailable_ranges(
    session: AsyncSession,
    *,
    symbol: str,
    interval: Interval,
    start_ms: int,
    end_ms: int,
    source: str = "binance_rest",
) -> list[tuple[int, int]]:
    if start_ms > end_ms:
        return []
    rows = await session.execute(
        select(CandleUnavailableRange.start_ms, CandleUnavailableRange.end_ms)
        .where(
            CandleUnavailableRange.source == source,
            CandleUnavailableRange.symbol == symbol.upper(),
            CandleUnavailableRange.interval == interval,
            CandleUnavailableRange.start_ms <= end_ms,
            CandleUnavailableRange.end_ms >= start_ms,
        )
        .order_by(CandleUnavailableRange.start_ms.asc())
    )
    return [(int(start), int(end)) for start, end in rows.all()]


def interval_to_ms(interval: Interval) -> int:
    multipliers = {
        "m": 60_000,
        "h": 60 * 60_000,
        "d": 24 * 60 * 60_000,
        "w": 7 * 24 * 60 * 60_000,
    }
    unit = interval[-1]
    return int(interval[:-1]) * multipliers[unit]
