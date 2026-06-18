from __future__ import annotations

from datetime import datetime
from decimal import Decimal
import logging

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Candle as CandleModel
from app.schemas.candle import Candle, Interval

logger = logging.getLogger(__name__)


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
        for candle in validated_candles
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
