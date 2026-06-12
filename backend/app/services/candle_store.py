from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Candle as CandleModel
from app.schemas.candle import Candle, Interval


async def upsert_candles(session: AsyncSession, candles: list[Candle]) -> None:
    if not candles:
        return

    rows = [
        {
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
        for candle in candles
    ]
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
    await session.commit()


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
    return [
        Candle(
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
        for model in models
    ]

