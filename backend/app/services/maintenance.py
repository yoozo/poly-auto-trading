from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.report_store import delete_finished_tasks_before
from app.services.service_events import delete_service_events_before


DEFAULT_SERVICE_EVENT_RETENTION_DAYS = 30
DEFAULT_FINISHED_TASK_RETENTION_DAYS = 14


@dataclass(frozen=True)
class CleanupResult:
    service_events_deleted: int
    analysis_tasks_deleted: int


async def cleanup_diagnostic_data(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    service_event_retention_days: int = DEFAULT_SERVICE_EVENT_RETENTION_DAYS,
    finished_task_retention_days: int = DEFAULT_FINISHED_TASK_RETENTION_DAYS,
) -> CleanupResult:
    current = now or datetime.now(timezone.utc)
    # 清理范围只限诊断和任务状态，不碰账户、activity、K 线等可复用业务数据。
    event_cutoff = current - timedelta(days=service_event_retention_days)
    task_cutoff = current - timedelta(days=finished_task_retention_days)
    return CleanupResult(
        service_events_deleted=await delete_service_events_before(session, event_cutoff),
        analysis_tasks_deleted=await delete_finished_tasks_before(session, task_cutoff),
    )
