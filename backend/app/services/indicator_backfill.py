from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Literal, cast

from pydantic import BaseModel, Field
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import SystemTask, SystemTaskStep
from app.db.session import AsyncSessionLocal
from app.schemas.candle import Candle, IndicatorPoint, Interval
from app.services.candle_backfill import SUPPORTED_INTERVALS, configured_intervals
from app.services.candle_intervals import CANDLE_INTERVAL_MS
from app.services.candle_store import list_candles_from
from app.services.indicator_store import get_latest_indicator_time, upsert_indicator_snapshots
from app.services.indicators import calculate_indicator_points
from app.services.service_events import record_service_event
from app.services.service_health import service_health_store
from app.services.system_task_store import system_task_store

logger = logging.getLogger(__name__)

INDICATOR_BATCH_CANDLES = 3000
INDICATOR_WARMUP_BARS = 120

IndicatorBackfillState = Literal["idle", "running", "completed", "error"]
ProgressState = Literal["pending", "running", "completed", "error"]


class IndicatorBackfillProgressStatus(BaseModel):
    interval: Interval
    status: ProgressState
    next_start_ms: int
    inserted_count: int
    last_error: str = ""
    started_at: datetime | None = None
    finished_at: datetime | None = None


class IndicatorBackfillStatus(BaseModel):
    state: IndicatorBackfillState = "idle"
    task_id: int | None = None
    symbol: str = Field(default_factory=lambda: settings.binance_symbol)
    intervals: list[Interval] = Field(default_factory=list)
    current_interval: Interval | None = None
    current_start_ms: int | None = None
    progress: list[IndicatorBackfillProgressStatus] = Field(default_factory=list)
    total_inserted: int = 0
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    message: str = ""


class IndicatorBackfillRunner:
    """指标补算任务：从标准化 candles 分段计算，缺口两侧不共享指标状态。"""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._active_task_id: int | None = None

    async def status(self) -> IndicatorBackfillStatus:
        try:
            async with AsyncSessionLocal() as session:
                task = await latest_task(session)
                if task is None:
                    return IndicatorBackfillStatus()
                progress = await list_task_progress(session, task.id)
                status = serialize_status(task, progress)
        except ProgrammingError as exc:
            if not is_missing_indicator_table(exc):
                raise
            status = migration_required_status(exc)
        service_health_store.set("indicator_backfill", service_state(status), last_error=status.error, metadata=status_metadata(status))
        return status

    async def start_all(self, *, symbol: str | None = None) -> IndicatorBackfillStatus:
        if self._lock.locked():
            return await self.status()
        normalized_symbol = (symbol or settings.binance_symbol).upper()
        intervals = configured_intervals()
        try:
            async with AsyncSessionLocal() as session:
                task = await latest_resumable_task(session, normalized_symbol)
                if task is None:
                    task = await create_task(session, symbol=normalized_symbol, intervals=intervals)
                else:
                    await resume_task(session, task, intervals=intervals)
                progress = await list_task_progress(session, task.id)
                status = serialize_status(task, progress)
                if self._active_task_id == task.id:
                    return status
                self._active_task_id = task.id
        except ProgrammingError as exc:
            if not is_missing_indicator_table(exc):
                raise
            status = migration_required_status(exc)
            service_health_store.set("indicator_backfill", "error", last_error=status.error, metadata=status_metadata(status))
            return status
        service_health_store.set("indicator_backfill", "running", metadata=status_metadata(status))
        asyncio.create_task(self._run(task.id))
        return status

    async def _run(self, task_id: int) -> None:
        async with self._lock:
            try:
                async with AsyncSessionLocal() as session:
                    task = await get_task(session, task_id)
                    if task is None:
                        return
                    progress = await list_task_progress(session, task.id)
                    await record_service_event(
                        session,
                        service="indicator_backfill",
                        level="info",
                        message="Indicator backfill started",
                        payload={"task_id": task.id, "symbol": task.symbol, "intervals": [item.interval for item in progress]},
                    )
                for item in progress:
                    if item.status == "completed":
                        continue
                    await self._backfill_interval(task_id=task_id, progress_id=item.id)
                async with AsyncSessionLocal() as session:
                    task = await get_task(session, task_id)
                    if task is None:
                        return
                    progress = await list_task_progress(session, task.id)
                    task.status = "completed"
                    task.message = "Indicator backfill completed"
                    task.error = ""
                    task.total_inserted = sum(item.inserted_count for item in progress)
                    task.finished_at = datetime.now(timezone.utc)
                    await session.commit()
                    status = serialize_status(task, progress)
                    await record_service_event(
                        session,
                        service="indicator_backfill",
                        level="info",
                        message="Indicator backfill completed",
                        payload=status_metadata(status),
                    )
                service_health_store.set("indicator_backfill", "idle", metadata=status_metadata(status))
            except Exception as exc:
                logger.exception("Indicator backfill failed")
                async with AsyncSessionLocal() as session:
                    task = await get_task(session, task_id)
                    if task is None:
                        return
                    task.status = "error"
                    task.error = str(exc)
                    task.message = "Indicator backfill failed"
                    task.total_inserted = await sum_inserted_count(session, task.id)
                    task.finished_at = datetime.now(timezone.utc)
                    await session.commit()
                    progress = await list_task_progress(session, task.id)
                    status = serialize_status(task, progress)
                    await record_service_event(
                        session,
                        service="indicator_backfill",
                        level="error",
                        message="Indicator backfill failed",
                        payload={**status_metadata(status), "error": str(exc)},
                    )
                service_health_store.set("indicator_backfill", "error", last_error=str(exc), metadata=status_metadata(status))
            finally:
                self._active_task_id = None

    async def _backfill_interval(self, *, task_id: int, progress_id: int) -> None:
        async with AsyncSessionLocal() as session:
            task = await get_task(session, task_id)
            progress = await get_progress(session, progress_id)
            if task is None or progress is None:
                return
            progress.status = "running"
            progress.last_error = ""
            progress.started_at = progress.started_at or datetime.now(timezone.utc)
            await session.commit()

        while True:
            async with AsyncSessionLocal() as session:
                task = await get_task(session, task_id)
                progress = await get_progress(session, progress_id)
                if task is None or progress is None:
                    return
                interval = cast(Interval, progress.interval)
                warmup_start_ms = max(0, progress.next_start_ms - (CANDLE_INTERVAL_MS[interval] * INDICATOR_WARMUP_BARS))
                warmup_start = datetime.fromtimestamp(warmup_start_ms / 1000, tz=timezone.utc)
                candles = await list_candles_from(
                    session,
                    symbol=task.symbol,
                    interval=interval,
                    start=warmup_start,
                    limit=INDICATOR_BATCH_CANDLES + INDICATOR_WARMUP_BARS,
                )
                await self._refresh_health(session, task)
            if not candles:
                await self._complete_progress(task_id, progress_id)
                return

            target_start = datetime.fromtimestamp(progress.next_start_ms / 1000, tz=timezone.utc)
            points = calculate_indicator_segments(candles, cast(Interval, progress.interval))
            target_points = [point for point in points if point.candle_time >= target_start]
            if not target_points:
                await self._complete_progress(task_id, progress_id)
                return

            async with AsyncSessionLocal() as session:
                task = await get_task(session, task_id)
                progress = await get_progress(session, progress_id)
                if task is None or progress is None:
                    return
                await upsert_indicator_snapshots(session, target_points)
                progress.inserted_count += len(target_points)
                progress.next_start_ms = int(target_points[-1].candle_time.timestamp() * 1000) + CANDLE_INTERVAL_MS[cast(Interval, progress.interval)]
                task.total_inserted = await sum_inserted_count(session, task.id)
                await session.commit()
                await self._refresh_health(session, task)
                if len(target_points) < INDICATOR_BATCH_CANDLES:
                    await self._complete_progress(task_id, progress_id)
                    return

    async def _complete_progress(self, task_id: int, progress_id: int) -> None:
        async with AsyncSessionLocal() as session:
            task = await get_task(session, task_id)
            progress = await get_progress(session, progress_id)
            if task is None or progress is None:
                return
            progress.status = "completed"
            progress.finished_at = datetime.now(timezone.utc)
            task.total_inserted = await sum_inserted_count(session, task.id)
            await session.commit()
            await self._refresh_health(session, task)

    async def _refresh_health(self, session: AsyncSession, task: SystemTask) -> None:
        progress = await list_task_progress(session, task.id)
        status = serialize_status(task, progress)
        service_health_store.set("indicator_backfill", service_state(status), last_error=status.error, metadata=status_metadata(status))


async def create_task(session: AsyncSession, *, symbol: str, intervals: list[Interval]) -> SystemTask:
    now = datetime.now(timezone.utc)
    task = system_task_store.create_task(
        task_type="indicator_backfill",
        symbol=symbol,
        status="running",
        message="Indicator backfill started",
        started_at=now,
        metadata={"batch_candles": INDICATOR_BATCH_CANDLES, "warmup_bars": INDICATOR_WARMUP_BARS},
    )
    session.add(task)
    await session.flush()
    for interval in intervals:
        next_start_ms = await incremental_start_ms(session, symbol=symbol, interval=interval)
        session.add(
            system_task_store.create_step(
                task_id=task.id,
                step_key=interval,
                interval=interval,
                start_ms=next_start_ms,
                cursor_ms=next_start_ms,
                end_ms=None,
            )
        )
    await session.commit()
    await session.refresh(task)
    return task


async def resume_task(session: AsyncSession, task: SystemTask, *, intervals: list[Interval]) -> None:
    task.status = "running"
    task.error = ""
    task.message = "Indicator backfill resumed"
    task.finished_at = None
    progress = await list_task_progress(session, task.id)
    existing = {item.interval for item in progress}
    for item in progress:
        if item.inserted_count <= 0 and item.next_start_ms <= 0:
            item.next_start_ms = await incremental_start_ms(
                session,
                symbol=task.symbol,
                interval=cast(Interval, item.interval),
            )
        if item.status == "error":
            item.status = "pending"
            item.last_error = ""
            item.finished_at = None
    for interval in intervals:
        if interval not in existing:
            next_start_ms = await incremental_start_ms(session, symbol=task.symbol, interval=interval)
            session.add(
                system_task_store.create_step(
                    task_id=task.id,
                    step_key=interval,
                    interval=interval,
                    start_ms=next_start_ms,
                    cursor_ms=next_start_ms,
                    end_ms=None,
                )
            )
    await session.commit()


async def incremental_start_ms(session: AsyncSession, *, symbol: str, interval: Interval) -> int:
    latest = await get_latest_indicator_time(session, symbol=symbol, interval=interval)
    if latest is None:
        return 0
    return int(latest.timestamp() * 1000) + CANDLE_INTERVAL_MS[interval]


async def latest_task(session: AsyncSession) -> SystemTask | None:
    return await system_task_store.latest_task(session, task_type="indicator_backfill")


async def latest_resumable_task(session: AsyncSession, symbol: str) -> SystemTask | None:
    return await system_task_store.latest_resumable_task(
        session,
        task_type="indicator_backfill",
        symbol=symbol,
    )


async def get_task(session: AsyncSession, task_id: int) -> SystemTask | None:
    return await system_task_store.get_task(session, task_id=task_id, task_type="indicator_backfill")


async def get_progress(session: AsyncSession, progress_id: int) -> SystemTaskStep | None:
    return await system_task_store.get_step(session, progress_id)


async def list_task_progress(session: AsyncSession, task_id: int) -> list[SystemTaskStep]:
    return await system_task_store.list_steps(session, task_id)


async def sum_inserted_count(session: AsyncSession, task_id: int) -> int:
    return await system_task_store.sum_inserted_count(session, task_id)


def serialize_status(task: SystemTask, progress: list[SystemTaskStep]) -> IndicatorBackfillStatus:
    progress_status = [
        IndicatorBackfillProgressStatus(
            interval=cast(Interval, item.interval),
            status=cast(ProgressState, item.status),
            next_start_ms=item.next_start_ms,
            inserted_count=item.inserted_count,
            last_error=item.last_error,
            started_at=item.started_at,
            finished_at=item.finished_at,
        )
        for item in progress
        if item.interval in SUPPORTED_INTERVALS
    ]
    current = next((item for item in progress_status if item.status == "running"), None)
    if current is None:
        current = next((item for item in progress_status if item.status in {"pending", "error"}), None)
    return IndicatorBackfillStatus(
        state=cast(IndicatorBackfillState, "error" if task.status == "error" else task.status),
        task_id=task.id,
        symbol=task.symbol,
        intervals=[item.interval for item in progress_status],
        current_interval=current.interval if current else None,
        current_start_ms=current.next_start_ms if current else None,
        progress=progress_status,
        total_inserted=sum(item.inserted_count for item in progress_status),
        started_at=task.started_at,
        finished_at=task.finished_at,
        error=task.error or None,
        message=task.message,
    )


def calculate_indicator_segments(candles: list[Candle], interval: Interval) -> list[IndicatorPoint]:
    if not candles:
        return []
    interval_ms = CANDLE_INTERVAL_MS[interval]
    segments: list[list[Candle]] = []
    current = [candles[0]]
    for candle in candles[1:]:
        previous = current[-1]
        delta_ms = int((candle.open_time - previous.open_time).total_seconds() * 1000)
        if delta_ms == interval_ms:
            current.append(candle)
            continue
        segments.append(current)
        current = [candle]
    segments.append(current)

    points: list[IndicatorPoint] = []
    for segment in segments:
        # 缺口两侧不能共享 RSI/EMA/BOLL 状态；每段独立计算，避免历史断点污染后续指标。
        points.extend(calculate_indicator_points(segment, interval))
    return points


def service_state(status: IndicatorBackfillStatus) -> str:
    if status.state == "running":
        return "running"
    if status.state == "error":
        return "error"
    return "idle"


def status_metadata(status: IndicatorBackfillStatus) -> dict[str, object]:
    return status.model_dump(mode="json")


def is_missing_indicator_table(exc: ProgrammingError) -> bool:
    message = str(exc)
    return "system_tasks" in message and "UndefinedTableError" in message


def migration_required_status(exc: ProgrammingError) -> IndicatorBackfillStatus:
    message = "指标任务表不存在，请先运行数据库迁移：alembic upgrade head"
    return IndicatorBackfillStatus(
        state="error",
        symbol=settings.binance_symbol,
        error=message,
        message=f"{message}; {exc.__class__.__name__}",
    )


indicator_backfill_runner = IndicatorBackfillRunner()
