from __future__ import annotations

from datetime import datetime, timezone, timedelta
import logging
from typing import Any

import httpx
from sqlalchemy import desc, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import (
    AppSetting,
    NotificationDelivery as NotificationDeliveryModel,
    NotificationDeliverySignal,
    Signal as SignalModel,
)
from app.schemas.notification import DeliveryStatus, NotificationDelivery, TelegramStatus
from app.schemas.signal import SignalRecord
from app.services.external_http import with_retry
from app.services.service_events import record_service_event
from app.services.service_health import service_health_store
from app.services.signal_analysis import serialize_signal_record

logger = logging.getLogger(__name__)

TELEGRAM_ENABLED_KEY = "telegram.enabled"
TELEGRAM_API_BASE_URL = "https://api.telegram.org"


async def get_telegram_enabled(session: AsyncSession) -> bool:
    setting = await session.get(AppSetting, TELEGRAM_ENABLED_KEY)
    if setting is None:
        return settings.telegram_enabled_default
    return bool(setting.value.get("enabled", settings.telegram_enabled_default))


async def set_telegram_enabled(session: AsyncSession, enabled: bool) -> bool:
    statement = insert(AppSetting).values(key=TELEGRAM_ENABLED_KEY, value={"enabled": enabled})
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[AppSetting.key],
            set_={"value": statement.excluded.value},
        )
    )
    await session.commit()
    await refresh_telegram_service_health(session)
    return enabled


async def get_telegram_status(session: AsyncSession) -> TelegramStatus:
    enabled = await get_telegram_enabled(session)
    last_delivery = await get_latest_delivery(session)
    configured, missing = telegram_config_state()
    update_telegram_health(
        enabled=enabled,
        configured=configured,
        missing=missing,
        last_delivery=last_delivery,
    )
    return TelegramStatus(
        configured=configured,
        enabled=enabled,
        chat_id_masked=mask_chat_id(settings.telegram_chat_id)
        if settings.telegram_chat_id
        else None,
        missing=missing,
        last_delivery=last_delivery,
    )


async def refresh_telegram_service_health(session: AsyncSession) -> None:
    await get_telegram_status(session)


async def send_test_message(session: AsyncSession) -> None:
    enabled = await get_telegram_enabled(session)
    configured, missing = telegram_config_state()
    if not configured:
        service_health_store.set(
            "telegram",
            "error",
            last_error=f"Missing Telegram config: {', '.join(missing)}",
            metadata={"configured": False, "enabled": enabled, "missing": missing},
        )
        raise ValueError(f"Missing Telegram config: {', '.join(missing)}")
    if not enabled:
        service_health_store.set(
            "telegram",
            "idle",
            last_error=None,
            metadata={"configured": True, "enabled": False},
        )
        raise ValueError("Telegram notification is disabled")
    await send_telegram_message("Poly Auto 测试消息")
    await record_service_event(
        session,
        service="telegram",
        level="info",
        message="Telegram test message sent",
        payload={"enabled": True, "configured": True},
    )
    service_health_store.set(
        "telegram",
        "running",
        metadata={"configured": True, "enabled": True, "last_event": "test_sent"},
    )


async def process_signal_notifications(
    session: AsyncSession,
    signals: list[SignalRecord],
) -> list[NotificationDelivery]:
    if not signals:
        return []
    delivery = await process_telegram_delivery(session, signals)
    return [delivery] if delivery is not None else []


async def process_telegram_delivery(
    session: AsyncSession,
    signals: list[SignalRecord],
) -> NotificationDelivery | None:
    target_type, target_key = delivery_target(signals)
    delivery_key = build_delivery_key(signals)
    title = delivery_title(signals)
    message = delivery_message(signals)
    enabled = await get_telegram_enabled(session)
    configured, missing = telegram_config_state()
    status: DeliveryStatus
    error = ""

    if not enabled:
        status = "skipped_disabled"
    elif not configured:
        status = "error"
        error = f"Missing Telegram config: {', '.join(missing)}"
    else:
        status = "sent"

    # Delivery 只记录投递结果；触发依据通过 notification_delivery_signals 关联到 signals。
    delivery, created = await insert_notification_delivery(
        session,
        signals=signals,
        channel="telegram",
        delivery_key=delivery_key,
        target_type=target_type,
        target_key=target_key,
        status=status,
        title=title,
        message=message,
        error=error,
    )
    if delivery is None:
        return None

    if created and status == "sent":
        try:
            await send_telegram_message(message)
            delivery = await update_delivery_status(
                session,
                delivery.id,
                status="sent",
                error="",
                sent_at=utc_now(),
            )
        except Exception as exc:
            logger.warning("Telegram notification failed", exc_info=exc)
            delivery = await update_delivery_status(
                session,
                delivery.id,
                status="error",
                error=str(exc),
                sent_at=None,
            )

    await record_service_event(
        session,
        service="telegram",
        level="error" if delivery.status == "error" else "info",
        message=f"Notification delivery {delivery.status}: {title}",
        payload={
            "channel": delivery.channel,
            "target_type": target_type,
            "target_key": target_key,
            "signal_keys": [signal.signal_key for signal in signals],
            "status": delivery.status,
        },
    )
    configured_after, missing_after = telegram_config_state()
    update_telegram_health(
        enabled=enabled,
        configured=configured_after,
        missing=missing_after,
        last_delivery=delivery,
    )
    return delivery


async def insert_notification_delivery(
    session: AsyncSession,
    *,
    signals: list[SignalRecord],
    channel: str,
    delivery_key: str,
    target_type: str,
    target_key: str,
    status: DeliveryStatus,
    title: str,
    message: str,
    error: str,
) -> tuple[NotificationDelivery | None, bool]:
    statement = (
        insert(NotificationDeliveryModel)
        .values(
            channel=channel,
            delivery_key=delivery_key,
            target_type=target_type,
            target_key=target_key,
            status=status,
            title=title,
            message=message,
            error=error,
        )
        .on_conflict_do_nothing(constraint="uq_notification_deliveries_channel_key")
        .returning(NotificationDeliveryModel.id)
    )
    delivery_id = await session.scalar(statement)
    created = delivery_id is not None
    if not created:
        delivery_id = await session.scalar(
            select(NotificationDeliveryModel.id).where(
                NotificationDeliveryModel.channel == channel,
                NotificationDeliveryModel.delivery_key == delivery_key,
            )
        )
    if delivery_id is None:
        raise RuntimeError("Notification delivery was not created or found")
    await link_delivery_signals(session, delivery_id, signals)
    await session.commit()
    return await get_delivery(session, delivery_id), created


async def link_delivery_signals(
    session: AsyncSession,
    delivery_id: int,
    signals: list[SignalRecord],
) -> None:
    for signal in signals:
        statement = (
            insert(NotificationDeliverySignal)
            .values(notification_delivery_id=delivery_id, signal_id=signal.id)
            .on_conflict_do_nothing()
        )
        await session.execute(statement)


async def update_delivery_status(
    session: AsyncSession,
    delivery_id: int,
    *,
    status: DeliveryStatus,
    error: str,
    sent_at: datetime | None,
) -> NotificationDelivery:
    model = await session.get(NotificationDeliveryModel, delivery_id)
    if model is None:
        raise RuntimeError("Notification delivery disappeared before status update")
    model.status = status
    model.error = error
    model.sent_at = sent_at
    await session.commit()
    await session.refresh(model)
    delivery = await get_delivery(session, delivery_id)
    if delivery is None:
        raise RuntimeError("Notification delivery disappeared before serialization")
    return delivery


async def list_notification_deliveries(
    session: AsyncSession,
    *,
    target_type: str | None = None,
    target_key: str | None = None,
    limit: int = 50,
) -> list[NotificationDelivery]:
    statement = (
        select(NotificationDeliveryModel)
        .order_by(desc(NotificationDeliveryModel.created_at))
        .limit(limit)
    )
    if target_type:
        statement = statement.where(NotificationDeliveryModel.target_type == target_type)
    if target_key:
        statement = statement.where(NotificationDeliveryModel.target_key == target_key)
    result = await session.scalars(statement)
    deliveries: list[NotificationDelivery] = []
    for model in result.all():
        deliveries.append(await serialize_delivery(session, model))
    return deliveries


async def get_latest_delivery(session: AsyncSession) -> NotificationDelivery | None:
    model = await session.scalar(
        select(NotificationDeliveryModel)
        .order_by(desc(NotificationDeliveryModel.created_at))
        .limit(1)
    )
    return await serialize_delivery(session, model) if model else None


async def get_delivery(session: AsyncSession, delivery_id: int) -> NotificationDelivery | None:
    model = await session.get(NotificationDeliveryModel, delivery_id)
    return await serialize_delivery(session, model) if model else None


async def serialize_delivery(
    session: AsyncSession,
    model: NotificationDeliveryModel,
) -> NotificationDelivery:
    signal_ids = await session.scalars(
        select(NotificationDeliverySignal.signal_id).where(
            NotificationDeliverySignal.notification_delivery_id == model.id
        )
    )
    ids = list(signal_ids.all())
    signal_records: list[SignalRecord] = []
    if ids:
        signal_models = await session.scalars(
            select(SignalModel).where(SignalModel.id.in_(ids)).order_by(SignalModel.created_at)
        )
        signal_records = [
            serialize_signal_record(signal_model) for signal_model in signal_models.all()
        ]
    return NotificationDelivery(
        id=model.id,
        channel=model.channel,
        delivery_key=model.delivery_key,
        target_type=model.target_type,
        target_key=model.target_key,
        status=model.status,  # type: ignore[arg-type]
        title=model.title,
        message=model.message,
        error=model.error,
        sent_at=model.sent_at,
        created_at=model.created_at,
        updated_at=model.updated_at,
        signals=signal_records,
    )


async def send_telegram_message(message: str) -> None:
    if len(message) > 3900:
        message = f"{message[:3890]}..."
    url = f"{TELEGRAM_API_BASE_URL}/bot{settings.telegram_bot_token}/sendMessage"
    async with httpx.AsyncClient(timeout=10.0) as client:
        async def post_message() -> httpx.Response:
            response = await client.post(
                url,
                json={
                    "chat_id": settings.telegram_chat_id,
                    "text": message,
                    "disable_web_page_preview": True,
                },
            )
            response.raise_for_status()
            return response

        await with_retry(post_message)


def delivery_target(signals: list[SignalRecord]) -> tuple[str, str]:
    first = signals[0]
    return first.target_type, first.target_key


def build_delivery_key(signals: list[SignalRecord]) -> str:
    first = signals[0]
    return f"telegram:{first.dedupe_key}"


def delivery_title(signals: list[SignalRecord]) -> str:
    labels = ", ".join(signal.signal_label for signal in signals)
    total_score = delivery_total_score(signals)
    return f"{score_marker(total_score)} 信号提醒：{labels}"


def delivery_message(signals: list[SignalRecord]) -> str:
    _ = delivery_target(signals)
    total_score = delivery_total_score(signals)
    market_name = delivery_market_name(signals)
    direction_emoji, direction_name = delivery_direction(signals)
    reminders = [delivery_signal_reminder(signal) for signal in signals]
    lines = [
        f"{score_marker(total_score)}市场：{market_name}",
        f"总分：{format_optional(total_score)}",
        f"方向：{direction_emoji}{direction_name}",
        f"信号提醒：{'，'.join(reminders)}",
    ]
    for signal, reminder in zip(signals, reminders):
        lines.append(
            f"- {reminder}  Score={format_score(signal.score)}"
        )
    return "\n".join(lines)


def delivery_market_name(signals: list[SignalRecord]) -> str:
    first = signals[0]
    return f"{first.target_type}:{first.target_key}"


def parse_target_key(target_key: str) -> tuple[str, str | None]:
    if ":" not in target_key:
        return target_key, None
    parts = target_key.split(":")
    if len(parts) >= 2:
        return ":".join(parts[:-1]), parts[-1]
    return target_key, None


def parse_signal_open_time(signal: SignalRecord) -> datetime | None:
    candle = signal.input_snapshot.get("candle", {})
    open_time = candle.get("open_time")
    if not isinstance(open_time, str):
        return None
    try:
        if open_time.endswith("Z"):
            open_time = open_time[:-1] + "+00:00"
        return datetime.fromisoformat(open_time)
    except ValueError:
        return None


def format_market_window(open_time: datetime, interval: str | None) -> str:
    if interval is None:
        return open_time.strftime("%B %d, %-I:%M%p")
    end_time = open_time + interval_duration(interval)
    return (
        f"{format_window_timestamp(open_time)}-"
        f"{format_window_timestamp(end_time)}"
    )


def format_window_timestamp(value: datetime) -> str:
    day = int(value.strftime("%d"))
    time_text = value.strftime("%I:%M%p").lstrip("0")
    return f"{value.strftime('%B')} {day}, {time_text}"


def interval_duration(interval: str) -> timedelta:
    seconds_map = {
        "1m": 60,
        "5m": 5 * 60,
        "15m": 15 * 60,
        "30m": 30 * 60,
        "1h": 60 * 60,
        "4h": 4 * 60 * 60,
        "1d": 24 * 60 * 60,
    }
    return timedelta(seconds=seconds_map.get(interval, 60))


def delivery_direction(signals: list[SignalRecord]) -> tuple[str, str]:
    direction = signals[0].direction
    if direction == "short":
        return "🔴", "DOWN"
    if direction == "long":
        return "🟢", "UP"
    return "⚪", "NONE"


def delivery_signal_reminder(signal: SignalRecord) -> str:
    if signal.signal_key == "rsi_ema_diff":
        value = signal.metadata.get("diff")
        return f"RSI-Diff = {format_number(value)}"
    if signal.signal_key.startswith("rsi_") and signal.signal_label.startswith("RSI"):
        if signal.signal_label.startswith("RSI-EMA"):
            return signal.signal_label.replace("RSI-EMA diff =", "RSI-Diff =")
        if signal.metadata.get("threshold") is not None:
            threshold = signal.metadata.get("threshold")
            op = ">"
            if signal.action == "buy":
                op = "<"
            return f"RSI {op} {format_number(threshold)}"
        return signal.signal_label.replace("RSI-EMA diff =", "RSI-Diff =")
    return signal.signal_label


def format_score(value: float | None) -> str:
    if value is None:
        return "-"
    if value == int(value):
        return str(int(value))
    return f"{value:.2f}"


def format_number(value: Any) -> str:
    if value is None:
        return "-"
    if not isinstance(value, (int, float)):
        return str(value)
    if float(value) == int(float(value)):
        return str(int(float(value)))
    return f"{float(value):.2f}"


def delivery_total_score(signals: list[SignalRecord]) -> float:
    return sum(signal.score or 0 for signal in signals)


def score_marker(score: float | None) -> str:
    value = score or 0
    if value >= 10:
        return "🔥🔥🔥"
    if value >= 6:
        return "🔥🔥"
    if value >= 3:
        return "🔥"
    if value >= 1:
        return "✨"
    return ""


def action_marker(signal: SignalRecord) -> str:
    if signal.action == "buy":
        return "🟢 买入 做多"
    if signal.action == "sell":
        return "🔴 卖出 做空"
    return "⚪ 观望"


def telegram_config_state() -> tuple[bool, list[str]]:
    missing = []
    if not settings.telegram_bot_token.strip():
        missing.append("telegram_bot_token")
    if not settings.telegram_chat_id.strip():
        missing.append("telegram_chat_id")
    return not missing, missing


def update_telegram_health(
    *,
    enabled: bool,
    configured: bool,
    missing: list[str],
    last_delivery: NotificationDelivery | None,
) -> None:
    metadata: dict[str, Any] = {
        "configured": configured,
        "enabled": enabled,
        "missing": missing,
    }
    if last_delivery:
        # service health 只保存最近一次通知摘要，完整历史由 notification_deliveries 表承载。
        metadata["last_delivery"] = {
            "status": last_delivery.status,
            "title": last_delivery.title,
            "signal_keys": [signal.signal_key for signal in last_delivery.signals],
            "created_at": last_delivery.created_at.isoformat(),
        }
    if not configured:
        state = "error" if enabled else "idle"
        last_error = f"Missing Telegram config: {', '.join(missing)}" if enabled else None
    else:
        state = "running" if enabled else "idle"
        last_error = (
            last_delivery.error if last_delivery and last_delivery.status == "error" else None
        )
    service_health_store.set("telegram", state, last_error=last_error, metadata=metadata)


def mask_chat_id(value: str) -> str:
    if len(value) <= 4:
        return "*" * len(value)
    return f"{value[:2]}***{value[-2:]}"


def format_optional(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
