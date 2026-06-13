from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class ServiceEventRecord(BaseModel):
    id: int
    service: str
    level: str
    message: str
    payload: dict[str, Any]
    created_at: datetime
