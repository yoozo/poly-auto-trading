from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

SignalAction = Literal["buy", "sell", "hold"]
SignalDirection = Literal["long", "short", "neutral"]


class SignalRecord(BaseModel):
    id: int
    signal_key: str
    signal_label: str
    action: SignalAction
    direction: SignalDirection
    target_type: str
    target_key: str
    dedupe_key: str
    occurred_at: datetime
    score: float | None = None
    input_snapshot: dict[str, Any]
    metadata: dict[str, Any]
    created_at: datetime
