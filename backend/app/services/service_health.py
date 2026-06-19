from __future__ import annotations

from datetime import datetime, timezone
from threading import RLock
from typing import Any

from pydantic import BaseModel, Field


class ServiceHealth(BaseModel):
    name: str
    state: str
    last_update: datetime
    last_error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ServiceHealthStore:
    def __init__(self) -> None:
        self._lock = RLock()
        now = utc_now()
        self._services: dict[str, ServiceHealth] = {
            "api": ServiceHealth(name="api", state="running", last_update=now),
            "database": ServiceHealth(name="database", state="unknown", last_update=now),
            "binance_rest": ServiceHealth(name="binance_rest", state="idle", last_update=now),
            "binance_ws": ServiceHealth(name="binance_ws", state="idle", last_update=now),
            "polymarket": ServiceHealth(name="polymarket", state="idle", last_update=now),
            "polymarket_ws": ServiceHealth(name="polymarket_ws", state="idle", last_update=now),
            "polymarket_user_ws": ServiceHealth(name="polymarket_user_ws", state="idle", last_update=now),
            "telegram": ServiceHealth(name="telegram", state="idle", last_update=now),
        }

    def set(
        self,
        name: str,
        state: str,
        last_error: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ServiceHealth:
        health = ServiceHealth(
            name=name,
            state=state,
            last_update=utc_now(),
            last_error=last_error,
            metadata=metadata or {},
        )
        with self._lock:
            self._services[name] = health
        return health

    def list(self) -> list[ServiceHealth]:
        with self._lock:
            return sorted(self._services.values(), key=lambda item: item.name)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


service_health_store = ServiceHealthStore()
