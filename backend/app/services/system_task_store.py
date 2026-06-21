from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import SystemTask, SystemTaskStep


class SystemTaskStore:
    """统一系统任务存储：封装 task/step 查询和创建，业务 runner 只保留领域流程。"""

    async def latest_task(
        self,
        session: AsyncSession,
        *,
        task_type: str,
        symbol: str | None = None,
    ) -> SystemTask | None:
        filters = [SystemTask.task_type == task_type]
        if symbol is not None:
            filters.append(SystemTask.symbol == symbol)
        return await session.scalar(
            select(SystemTask)
            .where(*filters)
            .order_by(SystemTask.id.desc())
            .limit(1)
        )

    async def latest_resumable_task(
        self,
        session: AsyncSession,
        *,
        task_type: str,
        symbol: str,
    ) -> SystemTask | None:
        return await session.scalar(
            select(SystemTask)
            .where(
                SystemTask.task_type == task_type,
                SystemTask.symbol == symbol,
                SystemTask.status.in_(["running", "error"]),
            )
            .order_by(SystemTask.id.desc())
            .limit(1)
        )

    async def get_task(
        self,
        session: AsyncSession,
        *,
        task_id: int,
        task_type: str,
    ) -> SystemTask | None:
        task = await session.get(SystemTask, task_id)
        if task is None or task.task_type != task_type:
            return None
        return task

    async def get_step(self, session: AsyncSession, step_id: int) -> SystemTaskStep | None:
        return await session.get(SystemTaskStep, step_id)

    async def list_steps(self, session: AsyncSession, task_id: int) -> list[SystemTaskStep]:
        rows = await session.scalars(
            select(SystemTaskStep)
            .where(SystemTaskStep.task_id == task_id)
            .order_by(SystemTaskStep.id.asc())
        )
        return list(rows.all())

    async def sum_inserted_count(self, session: AsyncSession, task_id: int) -> int:
        steps = await self.list_steps(session, task_id)
        return sum(step.inserted_count for step in steps)

    def create_task(
        self,
        *,
        task_type: str,
        symbol: str,
        status: str,
        message: str,
        started_at: datetime,
        metadata: dict[str, Any] | None = None,
    ) -> SystemTask:
        return SystemTask(
            task_type=task_type,
            symbol=symbol,
            status=status,
            message=message,
            error="",
            total_inserted=0,
            started_at=started_at,
            finished_at=None,
            task_metadata=metadata or {},
        )

    def create_step(
        self,
        *,
        task_id: int,
        step_key: str,
        interval: str,
        start_ms: int,
        cursor_ms: int,
        end_ms: int | None,
    ) -> SystemTaskStep:
        return SystemTaskStep(
            task_id=task_id,
            step_key=step_key,
            interval=interval,
            status="pending",
            start_ms=start_ms,
            cursor_ms=cursor_ms,
            end_ms=end_ms,
            inserted_count=0,
            raw_count=0,
            last_error="",
        )


system_task_store = SystemTaskStore()
