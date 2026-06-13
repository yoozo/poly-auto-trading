from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.signal import SignalRecord


DeliveryStatus = Literal["sent", "skipped_disabled", "error"]


class NotificationDelivery(BaseModel):
    id: int
    channel: str
    delivery_key: str
    target_type: str
    target_key: str
    status: DeliveryStatus
    title: str
    message: str
    error: str
    sent_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    signals: list[SignalRecord] = Field(default_factory=list)


class TelegramStatus(BaseModel):
    configured: bool
    enabled: bool
    chat_id_masked: str | None = None
    missing: list[str]
    last_delivery: NotificationDelivery | None = None


class UpdateTelegramStatusRequest(BaseModel):
    enabled: bool


class TelegramTestResponse(BaseModel):
    ok: bool
    message: str
