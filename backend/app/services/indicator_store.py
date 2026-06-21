from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import IndicatorSnapshot
from app.schemas.candle import IndicatorPoint


async def upsert_indicator_snapshots(session: AsyncSession, points: list[IndicatorPoint]) -> None:
    if not points:
        return

    rows = [
        {
            "symbol": point.symbol.upper(),
            "interval": point.interval,
            "candle_time": point.candle_time,
            "rsi": decimal_or_none(point.rsi),
            "rsi_ema": decimal_or_none(point.rsi_ema),
            "rsi_ema_diff": decimal_or_none(point.rsi_ema_diff),
            "boll_upper": decimal_or_none(point.bollinger.upper),
            "boll_middle": decimal_or_none(point.bollinger.middle),
            "boll_lower": decimal_or_none(point.bollinger.lower),
            "payload": {},
        }
        for point in points
    ]
    statement = insert(IndicatorSnapshot).values(rows)
    await session.execute(
        statement.on_conflict_do_update(
            constraint="uq_indicator_symbol_interval_candle_time",
            set_={
                "rsi": statement.excluded.rsi,
                "rsi_ema": statement.excluded.rsi_ema,
                "rsi_ema_diff": statement.excluded.rsi_ema_diff,
                "boll_upper": statement.excluded.boll_upper,
                "boll_middle": statement.excluded.boll_middle,
                "boll_lower": statement.excluded.boll_lower,
                "payload": statement.excluded.payload,
            },
        )
    )
    await session.commit()


def decimal_or_none(value: float | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


async def get_latest_indicator_time(session: AsyncSession, *, symbol: str, interval: str) -> datetime | None:
    return await session.scalar(
        select(func.max(IndicatorSnapshot.candle_time)).where(
            IndicatorSnapshot.symbol == symbol.upper(),
            IndicatorSnapshot.interval == interval,
        )
    )
