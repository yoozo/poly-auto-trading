from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ServiceEvent
from app.schemas.status import ServiceEventRecord


async def record_service_event(
    session: AsyncSession,
    *,
    service: str,
    level: str,
    message: str,
    payload: dict[str, Any] | None = None,
) -> None:
    session.add(
        ServiceEvent(
            service=service,
            level=level.lower(),
            message=message,
            payload=payload or {},
        )
    )
    await session.commit()


async def list_service_events(
    session: AsyncSession,
    *,
    service: str | None = None,
    level: str | None = None,
    limit: int = 100,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[ServiceEventRecord]:
    statement = select(ServiceEvent)
    if service:
        statement = statement.where(ServiceEvent.service == service)
    if level:
        statement = statement.where(ServiceEvent.level == level.lower())
    if start:
        statement = statement.where(ServiceEvent.created_at >= start)
    if end:
        statement = statement.where(ServiceEvent.created_at <= end)
    statement = statement.order_by(ServiceEvent.created_at.desc(), ServiceEvent.id.desc()).limit(limit)
    rows = (await session.scalars(statement)).all()
    return [serialize_service_event(row) for row in rows]


async def delete_service_events_before(session: AsyncSession, before: datetime) -> int:
    result = await session.execute(delete(ServiceEvent).where(ServiceEvent.created_at < before))
    await session.commit()
    return int(result.rowcount or 0)


def serialize_service_event(event: ServiceEvent) -> ServiceEventRecord:
    return ServiceEventRecord(
        id=event.id,
        service=event.service,
        level=event.level,
        message=event.message,
        payload=event.payload or {},
        created_at=event.created_at,
    )
