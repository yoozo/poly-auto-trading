from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Protocol

from sqlalchemy import desc, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import Signal as SignalModel
from app.schemas.candle import Candle, IndicatorPoint
from app.schemas.market_signal import SignalInput
from app.schemas.signal import SignalRecord


@dataclass(frozen=True)
class AnalysisSignal:
    key: str
    label: str
    action: str
    direction: str
    score: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SignalAnalysisContext:
    signal_input: SignalInput
    now: datetime

    @property
    def candle(self) -> Candle:
        return self.signal_input.candle

    @property
    def indicator(self) -> IndicatorPoint | None:
        return self.signal_input.indicator


class SignalRuleStrategy(Protocol):
    """信号分析策略：只判断市场状态，不负责通知发送、冷却或落库。"""

    def evaluate(self, context: SignalAnalysisContext) -> list[AnalysisSignal]: ...


class RsiThresholdRule:
    def evaluate(self, context: SignalAnalysisContext) -> list[AnalysisSignal]:
        indicator = context.indicator
        if indicator is None or indicator.rsi is None:
            return []

        rsi = indicator.rsi
        base_score = settings.signal_interval_base_scores.get(context.candle.interval)
        if base_score is None:
            return []

        rsi_signal = rsi_threshold_signal(rsi)
        if rsi_signal is None:
            return []
        key, label, action, direction, threshold, rsi_bonus = rsi_signal
        return [
            AnalysisSignal(
                key,
                label,
                action=action,
                direction=direction,
                score=base_score + rsi_bonus,
                metadata={
                    "rsi": rsi,
                    "interval": context.candle.interval,
                    "base_score": base_score,
                    "rsi_bonus": rsi_bonus,
                    "threshold": threshold,
                    "score_rule": "interval_base_score + rsi_threshold_bonus",
                },
            )
        ]


class RsiEmaDiffRule:
    def evaluate(self, context: SignalAnalysisContext) -> list[AnalysisSignal]:
        indicator = context.indicator
        # RSI-EMA diff 只以收盘 K 线为准，避免盘中波动反复触发误报。
        if not context.candle.is_closed or indicator is None:
            return []
        diff = indicator.rsi_ema_diff
        base_score = settings.signal_interval_base_scores.get(context.candle.interval)
        if diff is None or base_score is None:
            return []
        abs_diff = abs(diff)
        diff_bonus = rsi_ema_diff_bonus(abs_diff)
        if diff_bonus is None:
            return []
        score = base_score + diff_bonus
        return [
            AnalysisSignal(
                "rsi_ema_diff",
                f"RSI-EMA diff = {diff:g}",
                action="sell" if diff > 0 else "buy",
                direction="short" if diff > 0 else "long",
                score=score,
                metadata={
                    "diff": diff,
                    "abs_diff": abs_diff,
                    "base_score": base_score,
                    "diff_bonus": diff_bonus,
                    "interval": context.candle.interval,
                    "score_rule": "interval_base_score + diff_threshold_bonus",
                },
            )
        ]


# 规则顺序就是分析结果顺序；后续 Polymarket 价格、盘口、链上因子应追加为新的分析策略。
SIGNAL_RULE_STRATEGIES: tuple[SignalRuleStrategy, ...] = (
    RsiThresholdRule(),
    RsiEmaDiffRule(),
)


def analyze_signal_input(
    signal_input: SignalInput, *, now: datetime | None = None
) -> list[AnalysisSignal]:
    context = SignalAnalysisContext(signal_input=signal_input, now=now or utc_now())
    return [rule for strategy in SIGNAL_RULE_STRATEGIES for rule in strategy.evaluate(context)]


def rsi_threshold_signal(rsi: float) -> tuple[str, str, str, str, float, float] | None:
    high_match = strongest_threshold_bonus(rsi, settings.signal_rsi_bonus)
    if high_match is not None:
        threshold, bonus = high_match
        key = rsi_high_key(bonus)
        return key, f"RSI > {threshold:g}", "sell", "short", threshold, bonus

    low_rsi = 100 - rsi
    low_match = strongest_threshold_bonus(low_rsi, settings.signal_rsi_bonus)
    if low_match is None:
        return None
    high_threshold, bonus = low_match
    threshold = 100 - high_threshold
    key = rsi_low_key(bonus)
    return key, f"RSI < {threshold:g}", "buy", "long", threshold, bonus


def strongest_threshold_bonus(
    value: float, thresholds: list[tuple[float, float]]
) -> tuple[float, float] | None:
    matched: tuple[float, float] | None = None
    for threshold, bonus in thresholds:
        if value > threshold:
            matched = (threshold, bonus)
    return matched


def rsi_high_key(bonus: float) -> str:
    if bonus >= 3:
        return "rsi_super_high"
    if bonus >= 2:
        return "rsi_extreme_high"
    return "rsi_high"


def rsi_low_key(bonus: float) -> str:
    if bonus >= 3:
        return "rsi_super_low"
    if bonus >= 2:
        return "rsi_extreme_low"
    return "rsi_low"


def rsi_ema_diff_bonus(abs_diff: float) -> float | None:
    matched_bonus: float | None = None
    for threshold, bonus in settings.signal_rsi_ema_diff_diff_bonus:
        if abs_diff > threshold:
            matched_bonus = bonus
    return matched_bonus


async def record_signal_input_analysis(
    session: AsyncSession,
    signal_input: SignalInput,
    *,
    now: datetime | None = None,
) -> list[SignalRecord]:
    analysis_signals = analyze_signal_input(signal_input, now=now)
    records: list[SignalRecord] = []
    for signal in analysis_signals:
        records.append(
            await upsert_signal_record(session, signal_input=signal_input, signal=signal)
        )
    return records


async def upsert_signal_record(
    session: AsyncSession,
    *,
    signal_input: SignalInput,
    signal: AnalysisSignal,
) -> SignalRecord:
    target_type, target_key = signal_target(signal_input)
    dedupe_key = signal_dedupe_key(signal_input)
    statement = (
        insert(SignalModel)
        .values(
            signal_key=signal.key,
            signal_label=signal.label,
            action=signal.action,
            direction=signal.direction,
            target_type=target_type,
            target_key=target_key,
            dedupe_key=dedupe_key,
            occurred_at=signal_occurred_at(signal_input),
            score=decimal_or_none(signal.score),
            input_snapshot=signal_input_snapshot(signal_input),
            signal_metadata=signal.metadata,
        )
        .on_conflict_do_nothing(constraint="uq_signals_signal_dedupe")
        .returning(SignalModel.id)
    )
    signal_id = await session.scalar(statement)
    if signal_id is None:
        signal_id = await session.scalar(
            select(SignalModel.id).where(
                SignalModel.signal_key == signal.key,
                SignalModel.dedupe_key == dedupe_key,
            )
        )
    if signal_id is None:
        raise RuntimeError("Signal record was not created or found")
    await session.commit()
    model = await session.get(SignalModel, signal_id)
    if model is None:
        raise RuntimeError("Signal record disappeared before serialization")
    return serialize_signal_record(model)


async def list_signals(
    session: AsyncSession,
    *,
    target_type: str | None = None,
    target_key: str | None = None,
    signal_key: str | None = None,
    limit: int = 50,
) -> list[SignalRecord]:
    statement = select(SignalModel).order_by(desc(SignalModel.created_at)).limit(limit)
    if target_type:
        statement = statement.where(SignalModel.target_type == target_type)
    if target_key:
        statement = statement.where(SignalModel.target_key == target_key)
    if signal_key:
        statement = statement.where(SignalModel.signal_key == signal_key)
    result = await session.scalars(statement)
    return [serialize_signal_record(model) for model in result.all()]


def signal_target(signal_input: SignalInput) -> tuple[str, str]:
    candle = signal_input.candle
    return "candle", f"{candle.symbol.upper()}:{candle.interval}"


def signal_dedupe_key(signal_input: SignalInput) -> str:
    candle = signal_input.candle
    return f"candle:{candle.symbol.upper()}:{candle.interval}:{candle.open_time.isoformat()}"


def signal_occurred_at(signal_input: SignalInput) -> datetime:
    return signal_input.candle.open_time


def signal_input_snapshot(signal_input: SignalInput) -> dict[str, Any]:
    indicator = signal_input.indicator
    return {
        "candle": signal_input.candle.model_dump(mode="json"),
        "indicator": indicator.model_dump(mode="json") if indicator else None,
        "market_events": [event.model_dump(mode="json") for event in signal_input.market_events],
        "factors": signal_input.factors,
    }


def serialize_signal_record(model: SignalModel) -> SignalRecord:
    return SignalRecord(
        id=model.id,
        signal_key=model.signal_key,
        signal_label=model.signal_label,
        action=model.action,
        direction=model.direction,
        target_type=model.target_type,
        target_key=model.target_key,
        dedupe_key=model.dedupe_key,
        occurred_at=model.occurred_at,
        score=float(model.score) if model.score is not None else None,
        input_snapshot=model.input_snapshot,
        metadata=model.signal_metadata,
        created_at=model.created_at,
    )


def decimal_or_none(value: float | None) -> Decimal | None:
    return Decimal(str(value)) if value is not None else None


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
