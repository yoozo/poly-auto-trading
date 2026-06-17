from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Literal, cast

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import KlineBackfillProgress, KlineBackfillTask
from app.db.session import AsyncSessionLocal
from app.schemas.candle import Candle, Interval
from app.services.binance_client import BinanceClient
from app.services.candle_store import upsert_candles
from app.services.service_events import record_service_event
from app.services.service_health import service_health_store

logger = logging.getLogger(__name__)

BINANCE_KLINE_LIMIT = 1000
KLINE_BACKFILL_CONCURRENCY = 10
SUPPORTED_INTERVALS: tuple[Interval, ...] = ("1m", "5m", "15m", "30m", "1h", "4h", "1d")
INTERVAL_MS: dict[Interval, int] = {
    "1m": 60_000,
    "5m": 5 * 60_000,
    "15m": 15 * 60_000,
    "30m": 30 * 60_000,
    "1h": 60 * 60_000,
    "4h": 4 * 60 * 60_000,
    "1d": 24 * 60 * 60_000,
}

CandleBackfillState = Literal["idle", "running", "completed", "error"]
ProgressState = Literal["pending", "running", "completed", "error"]


class CandleBackfillProgressStatus(BaseModel):
    interval: Interval
    status: ProgressState
    next_start_ms: int
    end_ms: int
    inserted_count: int
    last_error: str = ""
    started_at: datetime | None = None
    finished_at: datetime | None = None


class CandleBackfillStatus(BaseModel):
    state: CandleBackfillState = "idle"
    task_id: int | None = None
    symbol: str = Field(default_factory=lambda: settings.binance_symbol)
    intervals: list[Interval] = Field(default_factory=list)
    current_interval: Interval | None = None
    current_start_ms: int | None = None
    end_ms: int | None = None
    fetched: dict[str, int] = Field(default_factory=dict)
    progress: list[CandleBackfillProgressStatus] = Field(default_factory=list)
    total_inserted: int = 0
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    message: str = ""


class KlineBackfillPageError(RuntimeError):
    def __init__(self, start_ms: int, original: BaseException) -> None:
        super().__init__(f"page {start_ms} failed: {original}")
        self.start_ms = start_ms
        self.original = original


class CandleBackfillRunner:
    """K 线全量补数后台任务：任务状态落库，下载页并发，写库和游标推进保持有界。"""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._active_task_id: int | None = None

    async def status(self) -> CandleBackfillStatus:
        try:
            async with AsyncSessionLocal() as session:
                task = await latest_task(session)
                if task is None:
                    return CandleBackfillStatus()
                progress = await list_task_progress(session, task.id)
                status = serialize_status(task, progress)
        except ProgrammingError as exc:
            if not is_missing_backfill_table(exc):
                raise
            status = migration_required_status(exc)
        service_health_store.set("kline_backfill", service_state(status), last_error=status.error, metadata=status_metadata(status))
        return status

    async def start_all(self, *, symbol: str | None = None) -> CandleBackfillStatus:
        if self._lock.locked():
            return await self.status()
        normalized_symbol = (symbol or settings.binance_symbol).upper()
        intervals = configured_intervals()
        end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        try:
            async with AsyncSessionLocal() as session:
                task = await latest_resumable_task(session, normalized_symbol)
                if task is None:
                    task = await create_task(session, symbol=normalized_symbol, intervals=intervals, end_ms=end_ms)
                else:
                    await resume_task(session, task, intervals=intervals)
                progress = await list_task_progress(session, task.id)
                status = serialize_status(task, progress)
                if self._active_task_id == task.id:
                    return status
                self._active_task_id = task.id
        except ProgrammingError as exc:
            if not is_missing_backfill_table(exc):
                raise
            status = migration_required_status(exc)
            service_health_store.set("kline_backfill", "error", last_error=status.error, metadata=status_metadata(status))
            return status
        service_health_store.set("kline_backfill", "running", metadata=status_metadata(status))
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
                        service="kline_backfill",
                        level="info",
                        message="K line backfill started",
                        payload={"task_id": task.id, "symbol": task.symbol, "intervals": [item.interval for item in progress]},
                    )
                client = BinanceClient()
                for interval_progress in progress:
                    if interval_progress.status == "completed":
                        continue
                    await self._backfill_interval(client, task_id=task_id, progress_id=interval_progress.id)
                async with AsyncSessionLocal() as session:
                    task = await get_task(session, task_id)
                    if task is None:
                        return
                    progress = await list_task_progress(session, task.id)
                    task.status = "completed"
                    task.message = "K line backfill completed"
                    task.error = ""
                    task.total_inserted = sum(item.inserted_count for item in progress)
                    task.finished_at = datetime.now(timezone.utc)
                    await session.commit()
                    status = serialize_status(task, progress)
                    await record_service_event(
                        session,
                        service="kline_backfill",
                        level="info",
                        message="K line backfill completed",
                        payload=status_metadata(status),
                    )
                service_health_store.set("kline_backfill", "idle", metadata=status_metadata(status))
            except Exception as exc:
                logger.exception("K line backfill failed")
                async with AsyncSessionLocal() as session:
                    task = await get_task(session, task_id)
                    if task is None:
                        return
                    task.status = "error"
                    task.error = str(exc)
                    task.message = "K line backfill failed"
                    task.total_inserted = await sum_inserted_count(session, task.id)
                    task.finished_at = datetime.now(timezone.utc)
                    await session.commit()
                    progress = await list_task_progress(session, task.id)
                    status = serialize_status(task, progress)
                    try:
                        await record_service_event(
                            session,
                            service="kline_backfill",
                            level="error",
                            message="K line backfill failed",
                            payload={**status_metadata(status), "error": str(exc)},
                        )
                    except Exception:
                        logger.warning("Failed to record K line backfill error event", exc_info=True)
                service_health_store.set("kline_backfill", "error", last_error=str(exc), metadata=status_metadata(status))
            finally:
                self._active_task_id = None

    async def _backfill_interval(self, client: BinanceClient, *, task_id: int, progress_id: int) -> None:
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
                if progress.next_start_ms > progress.end_ms:
                    progress.status = "completed"
                    progress.finished_at = datetime.now(timezone.utc)
                    await session.commit()
                    await self._refresh_health(session, task)
                    return
                page_starts = wave_page_starts(
                    progress.next_start_ms,
                    end_ms=progress.end_ms,
                    interval=cast(Interval, progress.interval),
                )
                await self._refresh_health(session, task)

            results = await fetch_wave(
                client,
                symbol=task.symbol,
                interval=cast(Interval, progress.interval),
                end_ms=progress.end_ms,
                page_starts=page_starts,
            )
            async with AsyncSessionLocal() as session:
                task = await get_task(session, task_id)
                progress = await get_progress(session, progress_id)
                if task is None or progress is None:
                    return
                failed_start_ms = await persist_wave(session, progress, results)
                task.total_inserted = await sum_inserted_count(session, task.id)
                if failed_start_ms is not None:
                    progress.status = "error"
                    progress.next_start_ms = failed_start_ms
                    progress.last_error = results[failed_start_ms] if isinstance(results[failed_start_ms], str) else "page failed"
                    task.status = "error"
                    task.error = progress.last_error
                    task.message = "K line backfill failed"
                    await session.commit()
                    await self._refresh_health(session, task)
                    raise KlineBackfillPageError(failed_start_ms, RuntimeError(progress.last_error))
                await session.commit()
                await self._refresh_health(session, task)

    async def _refresh_health(self, session: AsyncSession, task: KlineBackfillTask) -> None:
        progress = await list_task_progress(session, task.id)
        status = serialize_status(task, progress)
        service_health_store.set("kline_backfill", service_state(status), last_error=status.error, metadata=status_metadata(status))


async def fetch_wave(
    client: BinanceClient,
    *,
    symbol: str,
    interval: Interval,
    end_ms: int,
    page_starts: list[int],
) -> dict[int, list[Candle] | str]:
    async def fetch_page(start_ms: int) -> tuple[int, list[Candle] | str]:
        try:
            candles = await client.fetch_klines(
                symbol=symbol,
                interval=interval,
                limit=BINANCE_KLINE_LIMIT,
                start_ms=start_ms,
                end_ms=end_ms,
            )
            return start_ms, candles
        except Exception as exc:
            return start_ms, f"{type(exc).__name__}: {exc or 'request failed'}"

    pairs = await asyncio.gather(*(fetch_page(start_ms) for start_ms in page_starts))
    return dict(pairs)


async def persist_wave(
    session: AsyncSession,
    progress: KlineBackfillProgress,
    results: dict[int, list[Candle] | str],
) -> int | None:
    interval = cast(Interval, progress.interval)
    interval_ms = INTERVAL_MS[interval]
    failed_start_ms: int | None = None
    completed = False
    for start_ms in sorted(results):
        value = results[start_ms]
        if isinstance(value, str):
            failed_start_ms = start_ms
            break
        candles = value
        if not candles:
            progress.next_start_ms = progress.end_ms + interval_ms
            completed = True
            break
        await upsert_candles(session, candles)
        progress.inserted_count += len(candles)
        progress.next_start_ms = int(candles[-1].open_time.timestamp() * 1000) + interval_ms
        if len(candles) < BINANCE_KLINE_LIMIT:
            completed = True
            break
    if completed:
        progress.status = "completed"
        progress.finished_at = datetime.now(timezone.utc)
    return failed_start_ms


def wave_page_starts(start_ms: int, *, end_ms: int, interval: Interval) -> list[int]:
    if start_ms <= 0:
        # 第一次请求需要让 Binance 返回真实上市起点；1970 附近直接并发会拿到重复首页。
        return [0]
    page_width_ms = BINANCE_KLINE_LIMIT * INTERVAL_MS[interval]
    return [
        page_start
        for page_start in (start_ms + index * page_width_ms for index in range(KLINE_BACKFILL_CONCURRENCY))
        if page_start <= end_ms
    ]


async def create_task(
    session: AsyncSession,
    *,
    symbol: str,
    intervals: list[Interval],
    end_ms: int,
) -> KlineBackfillTask:
    now = datetime.now(timezone.utc)
    task = KlineBackfillTask(
        symbol=symbol,
        status="running",
        message="K line backfill started",
        error="",
        total_inserted=0,
        started_at=now,
        finished_at=None,
        task_metadata={"concurrency": KLINE_BACKFILL_CONCURRENCY, "limit": BINANCE_KLINE_LIMIT},
    )
    session.add(task)
    await session.flush()
    for interval in intervals:
        session.add(
            KlineBackfillProgress(
                task_id=task.id,
                interval=interval,
                status="pending",
                next_start_ms=0,
                end_ms=end_ms,
                inserted_count=0,
                last_error="",
                started_at=None,
                finished_at=None,
            )
        )
    await session.commit()
    await session.refresh(task)
    return task


async def resume_task(
    session: AsyncSession,
    task: KlineBackfillTask,
    *,
    intervals: list[Interval],
) -> None:
    task.status = "running"
    task.error = ""
    task.message = "K line backfill resumed"
    task.finished_at = None
    progress = await list_task_progress(session, task.id)
    existing = {item.interval for item in progress}
    for item in progress:
        if item.status == "error":
            item.status = "pending"
            item.last_error = ""
            item.finished_at = None
    for interval in intervals:
        if interval not in existing:
            session.add(
                KlineBackfillProgress(
                    task_id=task.id,
                    interval=interval,
                    status="pending",
                    next_start_ms=0,
                    end_ms=max((item.end_ms for item in progress), default=int(datetime.now(timezone.utc).timestamp() * 1000)),
                    inserted_count=0,
                    last_error="",
                )
            )
    await session.commit()


async def latest_task(session: AsyncSession) -> KlineBackfillTask | None:
    return await session.scalar(select(KlineBackfillTask).order_by(KlineBackfillTask.id.desc()).limit(1))


async def latest_resumable_task(session: AsyncSession, symbol: str) -> KlineBackfillTask | None:
    return await session.scalar(
        select(KlineBackfillTask)
        .where(KlineBackfillTask.symbol == symbol, KlineBackfillTask.status.in_(["running", "error"]))
        .order_by(KlineBackfillTask.id.desc())
        .limit(1)
    )


async def get_task(session: AsyncSession, task_id: int) -> KlineBackfillTask | None:
    return await session.get(KlineBackfillTask, task_id)


async def get_progress(session: AsyncSession, progress_id: int) -> KlineBackfillProgress | None:
    return await session.get(KlineBackfillProgress, progress_id)


async def list_task_progress(session: AsyncSession, task_id: int) -> list[KlineBackfillProgress]:
    rows = await session.scalars(
        select(KlineBackfillProgress)
        .where(KlineBackfillProgress.task_id == task_id)
        .order_by(KlineBackfillProgress.id.asc())
    )
    return list(rows.all())


async def sum_inserted_count(session: AsyncSession, task_id: int) -> int:
    progress = await list_task_progress(session, task_id)
    return sum(item.inserted_count for item in progress)


def serialize_status(task: KlineBackfillTask, progress: list[KlineBackfillProgress]) -> CandleBackfillStatus:
    progress_status = [
        CandleBackfillProgressStatus(
            interval=cast(Interval, item.interval),
            status=cast(ProgressState, item.status),
            next_start_ms=item.next_start_ms,
            end_ms=item.end_ms,
            inserted_count=item.inserted_count,
            last_error=item.last_error,
            started_at=item.started_at,
            finished_at=item.finished_at,
        )
        for item in progress
    ]
    current = next((item for item in progress_status if item.status == "running"), None)
    if current is None:
        current = next((item for item in progress_status if item.status in {"pending", "error"}), None)
    return CandleBackfillStatus(
        state=cast(CandleBackfillState, "error" if task.status == "error" else task.status),
        task_id=task.id,
        symbol=task.symbol,
        intervals=[item.interval for item in progress_status],
        current_interval=current.interval if current else None,
        current_start_ms=current.next_start_ms if current else None,
        end_ms=max((item.end_ms for item in progress_status), default=None),
        fetched={item.interval: item.inserted_count for item in progress_status},
        progress=progress_status,
        total_inserted=sum(item.inserted_count for item in progress_status),
        started_at=task.started_at,
        finished_at=task.finished_at,
        error=task.error or None,
        message=task.message,
    )


def service_state(status: CandleBackfillStatus) -> str:
    if status.state == "running":
        return "running"
    if status.state == "error":
        return "error"
    return "idle"


def status_metadata(status: CandleBackfillStatus) -> dict[str, Any]:
    return status.model_dump(mode="json")


def is_missing_backfill_table(exc: ProgrammingError) -> bool:
    message = str(exc)
    return "kline_backfill_tasks" in message and "UndefinedTableError" in message


def migration_required_status(exc: ProgrammingError) -> CandleBackfillStatus:
    message = "K 线任务表不存在，请先运行数据库迁移：alembic upgrade head"
    return CandleBackfillStatus(
        state="error",
        symbol=settings.binance_symbol,
        error=message,
        message=f"{message}; {exc.__class__.__name__}",
    )


def configured_intervals() -> list[Interval]:
    intervals: list[Interval] = []
    for interval in settings.binance_intervals:
        if interval in SUPPORTED_INTERVALS:
            intervals.append(cast(Interval, interval))
    return intervals or list(SUPPORTED_INTERVALS)


candle_backfill_runner = CandleBackfillRunner()
