from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Signal as SignalModel
from app.schemas.chainlink_twap import MarketPriceContext
from app.services.notifications import process_signal_notifications
from app.services.signal_analysis import serialize_signal_record


async def notify_twap_direction(
    session: AsyncSession, context: MarketPriceContext
) -> None:
    """每个 market、方向和质量状态只推送一次，避免 TWAP 高频更新刷屏。"""
    # TG 方向优先采用结算 TWAP；页面顶部则继续展示 Polymarket 同款 spot 当前价。
    direction = context.settlement_direction or context.direction
    if direction is None or context.current is None or context.baseline is None:
        return
    dedupe_key = (
        f"chainlink-twap:{context.market_id}:{direction}:{context.quality}"
    )
    metadata = context.model_dump(mode="json")
    statement = (
        insert(SignalModel)
        .values(
            signal_key="chainlink_twap_direction",
            signal_label=f"Chainlink TWAP {direction.upper()}",
            action="buy" if direction == "up" else "sell",
            direction="long" if direction == "up" else "short",
            target_type="polymarket_market",
            target_key=context.market_id,
            dedupe_key=dedupe_key,
            occurred_at=context.current.observed_at,
            score=None,
            input_snapshot={"price_context": metadata},
            signal_metadata=metadata,
        )
        .on_conflict_do_nothing(constraint="uq_signals_signal_dedupe")
        .returning(SignalModel.id)
    )
    signal_id = await session.scalar(statement)
    if signal_id is None:
        signal_id = await session.scalar(
            select(SignalModel.id).where(
                SignalModel.signal_key == "chainlink_twap_direction",
                SignalModel.dedupe_key == dedupe_key,
            )
        )
    if signal_id is None:
        return
    await session.commit()
    model = await session.get(SignalModel, signal_id)
    if model is not None:
        await process_signal_notifications(session, [serialize_signal_record(model)])
