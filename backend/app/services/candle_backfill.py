from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Literal, cast

from pydantic import BaseModel, Field
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import SystemTask, SystemTaskStep
from app.db.session import AsyncSessionLocal
from app.schemas.candle import Interval
from app.services.binance_archive_client import (
    ArchiveKlineBatch,
    ArchivePeriod,
    BinanceArchiveClient,
    BinanceArchiveFileNotFound,
    select_archive_period,
)
from app.services.binance_client import BinanceClient, KlinePage
from app.services.candle_intervals import CANDLE_INTERVAL_MS, align_interval_open_ms, latest_closed_open_ms
from app.services.candle_store import (
    get_earliest_candle_time,
    get_latest_candle,
    list_candle_missing_ranges,
    list_candle_ranges,
    list_candle_unavailable_ranges,
    upsert_candle_unavailable_range,
    upsert_candles,
)
from app.services.service_events import record_service_event
from app.services.service_health import service_health_store
from app.services.system_task_store import system_task_store

logger = logging.getLogger(__name__)

BINANCE_KLINE_LIMIT = 1000
KLINE_BACKFILL_CONCURRENCY = 10
ARCHIVE_BACKFILL_CONCURRENCY = 3
API_SYNC_MAX_PAGES = 2
MISSING_RANGE_MERGE_PAGES = KLINE_BACKFILL_CONCURRENCY
SUPPORTED_INTERVALS: tuple[Interval, ...] = ("1m", "5m", "15m", "30m", "1h", "4h", "12h", "1d", "1w")
INTERVAL_EXECUTION_ORDER: tuple[Interval, ...] = ("1w", "1d", "12h", "4h", "1h", "30m", "15m", "5m", "1m")
BINANCE_SPOT_HISTORY_START_MS = int(datetime(2017, 8, 17, 4, tzinfo=timezone.utc).timestamp() * 1000)
INTERVAL_MS = CANDLE_INTERVAL_MS

CandleBackfillState = Literal["idle", "running", "completed", "error"]
ProgressState = Literal["pending", "running", "completed", "error"]


@dataclass(frozen=True)
class CandleMissingRange:
    interval: Interval
    start_ms: int
    end_ms: int
    index: int = 0

    @property
    def step_key(self) -> str:
        return f"{self.interval}:{self.start_ms}"


class CandleBackfillProgressStatus(BaseModel):
    interval: Interval
    status: ProgressState
    next_start_ms: int
    end_ms: int
    inserted_count: int
    last_error: str = ""
    started_at: datetime | None = None
    finished_at: datetime | None = None
    raw_count: int = 0
    range: dict[str, Any] | None = None


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
    candle_ranges: dict[str, dict[str, Any]] = Field(default_factory=dict)


class KlineBackfillPageError(RuntimeError):
    def __init__(self, start_ms: int, original: BaseException) -> None:
        super().__init__(f"page {start_ms} failed: {original}")
        self.start_ms = start_ms
        self.original = original


@dataclass(frozen=True)
class ArchiveBackfillPeriodResult:
    period: ArchivePeriod
    next_start_ms: int
    raw_count: int = 0
    inserted_count: int = 0
    skipped_invalid_count: int = 0
    error: str = ""


class CandleBackfillRunner:
    """K 线全量补数后台任务：任务状态落库，下载页并发，写库和游标推进保持有界。"""

    def __init__(self) -> None:
        self._start_lock = asyncio.Lock()
        self._lock = asyncio.Lock()
        self._active_task_id: int | None = None

    async def status(self) -> CandleBackfillStatus:
        try:
            async with AsyncSessionLocal() as session:
                task = await latest_task(session)
                if task is None:
                    return CandleBackfillStatus()
                progress = await list_task_progress(session, task.id)
                ranges = await list_candle_ranges(session, task.symbol)
                status = serialize_status(task, progress, candle_ranges=ranges)
        except ProgrammingError as exc:
            if not is_missing_backfill_table(exc):
                raise
            status = migration_required_status(exc)
        service_health_store.set("kline_backfill", service_state(status), last_error=status.error, metadata=status_metadata(status))
        return status

    async def start_all(self, *, symbol: str | None = None) -> CandleBackfillStatus:
        if self._lock.locked() or self._active_task_id is not None:
            return await self.status()
        async with self._start_lock:
            if self._lock.locked() or self._active_task_id is not None:
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
                    ranges = await list_candle_ranges(session, task.symbol)
                    status = serialize_status(task, progress, candle_ranges=ranges)
                    self._active_task_id = task.id
            except ProgrammingError as exc:
                if not is_missing_backfill_table(exc):
                    raise
                status = migration_required_status(exc)
                service_health_store.set(
                    "kline_backfill",
                    "error",
                    last_error=status.error,
                    metadata=status_metadata(status),
                )
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
                archive_client = BinanceArchiveClient()
                for interval_progress in sort_progress_for_execution(progress):
                    if interval_progress.status == "completed":
                        continue
                    await self._backfill_interval(
                        client,
                        archive_client,
                        task_id=task_id,
                        progress_id=interval_progress.id,
                    )
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
                    ranges = await list_candle_ranges(session, task.symbol)
                    status = serialize_status(task, progress, candle_ranges=ranges)
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
                    ranges = await list_candle_ranges(session, task.symbol)
                    status = serialize_status(task, progress, candle_ranges=ranges)
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

    async def _backfill_interval(
        self,
        client: BinanceClient,
        archive_client: BinanceArchiveClient,
        *,
        task_id: int,
        progress_id: int,
    ) -> None:
        async with AsyncSessionLocal() as session:
            task = await get_task(session, task_id)
            progress = await get_progress(session, progress_id)
            if task is None or progress is None:
                return
            normalized_end_ms = normalize_step_end_ms(progress.end_ms, cast(Interval, progress.interval))
            if normalized_end_ms != progress.end_ms:
                progress.end_ms = normalized_end_ms
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
                interval = cast(Interval, progress.interval)
                archive_periods = collect_archive_periods(
                    symbol=task.symbol,
                    interval=interval,
                    start_ms=progress.next_start_ms,
                    end_ms=progress.end_ms,
                    limit=ARCHIVE_BACKFILL_CONCURRENCY,
                ) if settings.binance_archive_enabled else None
                page_starts = [] if archive_periods else wave_page_starts(
                    progress.next_start_ms,
                    end_ms=progress.end_ms,
                    interval=interval,
                )
                await self._refresh_health(session, task)

            if archive_periods:
                logger.info(
                    "K line backfill using Binance archive",
                    extra={
                        "symbol": task.symbol,
                        "interval": interval,
                        "period_count": len(archive_periods),
                        "concurrency": ARCHIVE_BACKFILL_CONCURRENCY,
                        "start_ms": archive_periods[0].start_ms,
                        "end_ms": archive_periods[-1].end_ms,
                    },
                )
                results = await self._persist_archive_periods(
                    client,
                    archive_client,
                    task_id=task_id,
                    progress_id=progress_id,
                    symbol=task.symbol,
                    interval=interval,
                    periods=archive_periods,
                )
                async with AsyncSessionLocal() as session:
                    task = await get_task(session, task_id)
                    progress = await get_progress(session, progress_id)
                    if task is None or progress is None:
                        return
                    failed = await apply_archive_period_results(session, task, progress, results)
                    task.total_inserted = await sum_inserted_count(session, task.id)
                    if failed is not None:
                        progress.status = "error"
                        progress.next_start_ms = failed.period.start_ms
                        progress.last_error = failed.error or "archive period failed"
                        task.status = "error"
                        task.error = progress.last_error
                        task.message = "K line backfill failed"
                        await session.commit()
                        await self._refresh_health(session, task)
                        raise KlineBackfillPageError(failed.period.start_ms, RuntimeError(progress.last_error))
                    await session.commit()
                    await self._refresh_health(session, task)
                    logger.info(
                        "K line backfill archive wave persisted",
                        extra={
                            "symbol": task.symbol,
                            "interval": interval,
                            "period_count": len(results),
                            "next_start_ms": progress.next_start_ms,
                        },
                    )
                continue

            results = await fetch_wave(
                client,
                symbol=task.symbol,
                interval=interval,
                end_ms=progress.end_ms,
                page_starts=page_starts,
            )
            async with AsyncSessionLocal() as session:
                task = await get_task(session, task_id)
                progress = await get_progress(session, progress_id)
                if task is None or progress is None:
                    return
                failed_start_ms = await persist_wave(session, task, progress, results)
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

    async def _persist_archive_periods(
        self,
        rest_client: BinanceClient,
        archive_client: BinanceArchiveClient,
        *,
        task_id: int,
        progress_id: int,
        symbol: str,
        interval: Interval,
        periods: list[ArchivePeriod],
    ) -> list[ArchiveBackfillPeriodResult]:
        semaphore = asyncio.Semaphore(ARCHIVE_BACKFILL_CONCURRENCY)

        async def run_period(period: ArchivePeriod) -> ArchiveBackfillPeriodResult:
            async with semaphore:
                return await self._persist_archive_period(
                    rest_client,
                    archive_client,
                    task_id=task_id,
                    progress_id=progress_id,
                    symbol=symbol,
                    interval=interval,
                    period=period,
                )

        return list(await asyncio.gather(*(run_period(period) for period in periods)))

    async def _persist_archive_period(
        self,
        rest_client: BinanceClient,
        archive_client: BinanceArchiveClient,
        *,
        task_id: int,
        progress_id: int,
        symbol: str,
        interval: Interval,
        period: ArchivePeriod,
    ) -> ArchiveBackfillPeriodResult:
        try:
            archive_file_path = await archive_client.download_klines_period_file(
                symbol=symbol,
                interval=interval,
                period=period,
            )
            return await self._persist_archive_file(
                archive_client,
                task_id=task_id,
                progress_id=progress_id,
                file_path=archive_file_path,
                symbol=symbol,
                interval=interval,
                period=period,
            )
        except BinanceArchiveFileNotFound:
            logger.info("Binance archive file not found, falling back to REST", extra={"path": period.path_suffix})
        except Exception as exc:
            logger.warning(
                "Binance archive stream failed, falling back to REST",
                extra={"path": period.path_suffix, "symbol": symbol, "interval": interval},
                exc_info=exc,
            )
        try:
            results = await fetch_wave(
                rest_client,
                symbol=symbol,
                interval=interval,
                end_ms=period.end_ms,
                page_starts=wave_page_starts(period.start_ms, end_ms=period.end_ms, interval=interval),
            )
            progress = SimpleNamespace(
                interval=interval,
                end_ms=period.end_ms,
                raw_count=0,
                next_start_ms=period.start_ms,
                inserted_count=0,
                status="running",
                finished_at=None,
            )
            async with AsyncSessionLocal() as session:
                failed_start_ms = await persist_wave(session, SimpleNamespace(symbol=symbol), progress, results)
                await session.commit()
            if failed_start_ms is not None:
                value = results[failed_start_ms]
                return ArchiveBackfillPeriodResult(
                    period=period,
                    next_start_ms=failed_start_ms,
                    error=value if isinstance(value, str) else "REST fallback page failed",
                )
            return ArchiveBackfillPeriodResult(
                period=period,
                next_start_ms=max(period.next_start_ms, progress.next_start_ms),
                raw_count=progress.raw_count,
                inserted_count=progress.inserted_count,
            )
        except Exception as exc:
            return ArchiveBackfillPeriodResult(
                period=period,
                next_start_ms=period.start_ms,
                error=f"{type(exc).__name__}: {exc or 'REST fallback failed'}",
            )

    async def _persist_archive_file(
        self,
        archive_client: BinanceArchiveClient,
        *,
        task_id: int,
        progress_id: int,
        file_path: str,
        symbol: str,
        interval: Interval,
        period,
    ) -> ArchiveBackfillPeriodResult:
        queue: asyncio.Queue[ArchiveKlineBatch | None] = asyncio.Queue(maxsize=2)

        async def produce_batches() -> None:
            iterator = archive_client.iter_klines_period_batches(
                file_path,
                symbol=symbol,
                interval=interval,
                period=period,
            )
            try:
                while True:
                    batch = await asyncio.to_thread(next, iterator, None)
                    if batch is None:
                        break
                    await queue.put(batch)
            finally:
                await queue.put(None)

        producer = asyncio.create_task(produce_batches())
        raw_count = 0
        skipped_invalid_count = 0
        inserted_count = 0
        last_open_ms: int | None = None
        try:
            while True:
                batch = await queue.get()
                if batch is None:
                    break
                raw_count += batch.raw_count
                skipped_invalid_count += batch.skipped_invalid_count
                candles = [
                    candle
                    for candle in batch.candles
                    if candle.is_closed and int(candle.open_time.timestamp() * 1000) <= period.end_ms
                ]
                if not candles:
                    continue
                last_open_ms = int(candles[-1].open_time.timestamp() * 1000)
                async with AsyncSessionLocal() as session:
                    await upsert_candles(session, candles)
                    inserted_count += len(candles)
            await producer
            async with AsyncSessionLocal() as session:
                if raw_count == 0 or inserted_count == 0:
                    await upsert_candle_unavailable_range(
                        session,
                        symbol=symbol,
                        interval=interval,
                        start_ms=period.start_ms,
                        end_ms=period.end_ms,
                        source="binance_archive",
                        reason="Binance archive returned no klines for this closed range",
                    )
                    await session.commit()
            if skipped_invalid_count:
                logger.warning(
                    "Skipped invalid Binance archive klines",
                    extra={
                        "symbol": symbol,
                        "interval": interval,
                        "period": period.kind,
                        "path": period.path_suffix,
                        "skipped_invalid_count": skipped_invalid_count,
                    },
                )
            next_start_ms = period.next_start_ms
            if last_open_ms is not None:
                next_start_ms = max(period.next_start_ms, last_open_ms + INTERVAL_MS[interval])
            logger.info(
                "Binance archive stream persisted",
                extra={
                    "symbol": symbol,
                    "interval": interval,
                    "period": period.kind,
                    "path": period.path_suffix,
                    "raw_count": raw_count,
                    "inserted_count": inserted_count,
                    "skipped_invalid_count": skipped_invalid_count,
                    "next_start_ms": next_start_ms,
                },
            )
            return ArchiveBackfillPeriodResult(
                period=period,
                next_start_ms=next_start_ms,
                raw_count=raw_count,
                inserted_count=inserted_count,
                skipped_invalid_count=skipped_invalid_count,
            )
        finally:
            producer.cancel()
            try:
                await producer
            except asyncio.CancelledError:
                pass
            try:
                os.unlink(file_path)
            except FileNotFoundError:
                pass

    async def _refresh_health(self, session: AsyncSession, task: SystemTask) -> None:
        progress = await list_task_progress(session, task.id)
        ranges = await list_candle_ranges(session, task.symbol)
        status = serialize_status(task, progress, candle_ranges=ranges)
        service_health_store.set("kline_backfill", service_state(status), last_error=status.error, metadata=status_metadata(status))


class CandleSyncService:
    """K 线小窗口同步补齐：API/monitor 只补当前需要的窗口，大范围历史仍交给 system_task。"""

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}

    async def ensure_latest_window(self, session: AsyncSession, *, symbol: str, interval: Interval, limit: int) -> None:
        end_ms = latest_closed_open_ms(int(datetime.now(timezone.utc).timestamp() * 1000), interval)
        start_ms = max(0, end_ms - ((limit - 1) * INTERVAL_MS[interval]))
        await self.ensure_range(session, symbol=symbol, interval=interval, start_ms=start_ms, end_ms=end_ms)

    async def ensure_range(
        self,
        session: AsyncSession,
        *,
        symbol: str,
        interval: Interval,
        start_ms: int,
        end_ms: int,
        max_pages: int = API_SYNC_MAX_PAGES,
    ) -> None:
        normalized_symbol = symbol.upper()
        range_start_ms, range_end_ms = normalize_sync_range(start_ms, end_ms, interval)
        if range_start_ms is None or range_end_ms is None:
            return
        max_candles = max_pages * BINANCE_KLINE_LIMIT
        if expected_candle_count(range_start_ms, range_end_ms, interval) > max_candles:
            await schedule_large_backfill(normalized_symbol)
            return

        lock_key = f"{normalized_symbol}:{interval}:{range_start_ms}:{range_end_ms}"
        lock = self._locks.setdefault(lock_key, asyncio.Lock())
        async with lock:
            try:
                missing_ranges = await missing_ranges_for_window(
                    session,
                    symbol=normalized_symbol,
                    interval=interval,
                    start_ms=range_start_ms,
                    end_ms=range_end_ms,
                )
                if not missing_ranges:
                    return
                missing_ranges = merge_missing_range_windows(missing_ranges, interval=interval)
                unavailable_ranges = await list_candle_unavailable_ranges(
                    session,
                    symbol=normalized_symbol,
                    interval=interval,
                    start_ms=range_start_ms,
                    end_ms=range_end_ms,
                )
                missing_ranges = subtract_unavailable_ranges(missing_ranges, unavailable_ranges, interval=interval)
                missing_candles = sum(expected_candle_count(start, end, interval) for start, end in missing_ranges)
                if missing_candles <= 0:
                    return
                if missing_candles > max_candles:
                    await schedule_large_backfill(normalized_symbol)
                    return
                await session.commit()
                await fetch_and_persist_ranges(
                    session,
                    symbol=normalized_symbol,
                    interval=interval,
                    ranges=missing_ranges,
                )
            except Exception as exc:
                await session.rollback()
                logger.warning(
                    "Bounded K line sync failed; scheduling background backfill",
                    extra={"symbol": normalized_symbol, "interval": interval},
                    exc_info=exc,
                )
                await schedule_large_backfill(normalized_symbol)


def normalize_sync_range(start_ms: int, end_ms: int, interval: Interval) -> tuple[int | None, int | None]:
    interval_ms = INTERVAL_MS[interval]
    start = align_interval_open_ms(max(0, start_ms), interval)
    end = latest_closed_open_ms(end_ms, interval)
    latest_closed = latest_closed_open_ms(int(datetime.now(timezone.utc).timestamp() * 1000), interval)
    end = min(end, latest_closed)
    if start > end:
        return None, None
    # 如果调用方给的是非对齐 start，向前扩一根，避免边界 candle 被漏补。
    if start > start_ms and start >= interval_ms:
        start -= interval_ms
    return start, end


def expected_candle_count(start_ms: int, end_ms: int, interval: Interval) -> int:
    if start_ms > end_ms:
        return 0
    return ((end_ms - start_ms) // INTERVAL_MS[interval]) + 1


async def missing_ranges_for_window(
    session: AsyncSession,
    *,
    symbol: str,
    interval: Interval,
    start_ms: int,
    end_ms: int,
) -> list[tuple[int, int]]:
    rows = await list_candles_between_ms(session, symbol=symbol, interval=interval, start_ms=start_ms, end_ms=end_ms)
    existing = {int(candle.open_time.timestamp() * 1000) for candle in rows}
    ranges: list[tuple[int, int]] = []
    missing_start: int | None = None
    current = start_ms
    interval_ms = INTERVAL_MS[interval]
    while current <= end_ms:
        if current not in existing:
            missing_start = current if missing_start is None else missing_start
        elif missing_start is not None:
            ranges.append((missing_start, current - interval_ms))
            missing_start = None
        current += interval_ms
    if missing_start is not None:
        ranges.append((missing_start, end_ms))
    return ranges


async def list_candles_between_ms(
    session: AsyncSession,
    *,
    symbol: str,
    interval: Interval,
    start_ms: int,
    end_ms: int,
):
    from app.services.candle_store import list_candles_between

    return await list_candles_between(
        session,
        symbol=symbol,
        interval=interval,
        start=ms_to_datetime(start_ms),
        end=ms_to_datetime(end_ms),
    )


async def fetch_and_persist_ranges(
    session: AsyncSession,
    *,
    symbol: str,
    interval: Interval,
    ranges: list[tuple[int, int]],
) -> None:
    client = BinanceClient()
    for start_ms, end_ms in sorted(ranges, key=lambda item: (item[1], item[0]), reverse=True):
        page_starts = wave_page_starts(start_ms, end_ms=end_ms, interval=interval)
        results = await fetch_wave(client, symbol=symbol, interval=interval, end_ms=end_ms, page_starts=page_starts)
        task = SimpleNamespace(symbol=symbol)
        progress = SimpleNamespace(
            interval=interval,
            end_ms=end_ms,
            raw_count=0,
            next_start_ms=start_ms,
            inserted_count=0,
            status="running",
            finished_at=None,
        )
        failed_start_ms = await persist_wave(session, task, progress, results)
        if failed_start_ms is not None:
            value = results[failed_start_ms]
            raise KlineBackfillPageError(
                failed_start_ms,
                RuntimeError(value if isinstance(value, str) else "page failed"),
            )


async def schedule_large_backfill(symbol: str) -> None:
    await candle_backfill_runner.start_all(symbol=symbol)


async def fetch_wave(
    client: BinanceClient,
    *,
    symbol: str,
    interval: Interval,
    end_ms: int,
    page_starts: list[int],
) -> dict[int, KlinePage | str]:
    async def fetch_page(start_ms: int) -> tuple[int, KlinePage | str]:
        try:
            page = await client.fetch_klines_page(
                symbol=symbol,
                interval=interval,
                limit=BINANCE_KLINE_LIMIT,
                start_ms=start_ms,
                end_ms=end_ms,
            )
            return start_ms, page
        except Exception as exc:
            return start_ms, f"{type(exc).__name__}: {exc or 'request failed'}"

    pairs = await asyncio.gather(*(fetch_page(start_ms) for start_ms in page_starts))
    return dict(pairs)


async def fetch_archive_period(
    client: BinanceArchiveClient,
    *,
    rest_client: BinanceClient,
    symbol: str,
    interval: Interval,
    period,
) -> dict[int, KlinePage | str]:
    try:
        page = await client.fetch_klines_period(symbol=symbol, interval=interval, period=period)
        logger.info(
            "Binance archive download completed",
            extra={
                "symbol": symbol,
                "interval": interval,
                "period": period.kind,
                "path": period.path_suffix,
                "raw_count": page.raw_count,
            },
        )
        return {period.start_ms: page}
    except BinanceArchiveFileNotFound:
        logger.info("Binance archive file not found, falling back to REST", extra={"path": period.path_suffix})
    except Exception as exc:
        logger.warning(
            "Binance archive download failed, falling back to REST",
            extra={"path": period.path_suffix, "symbol": symbol, "interval": interval},
            exc_info=exc,
        )
    page = await rest_client.fetch_klines_page(
        symbol=symbol,
        interval=interval,
        limit=BINANCE_KLINE_LIMIT,
        start_ms=period.start_ms,
        end_ms=period.end_ms,
    )
    return {period.start_ms: page}


def collect_archive_periods(
    *,
    symbol: str,
    interval: Interval,
    start_ms: int,
    end_ms: int,
    limit: int,
) -> list[ArchivePeriod]:
    periods: list[ArchivePeriod] = []
    next_start_ms = start_ms
    while len(periods) < limit:
        period = select_archive_period(
            symbol=symbol,
            interval=interval,
            start_ms=next_start_ms,
            end_ms=end_ms,
        )
        if period is None:
            break
        periods.append(period)
        if period.next_start_ms <= next_start_ms:
            break
        next_start_ms = period.next_start_ms
    return periods


async def apply_archive_period_results(
    session: AsyncSession,
    task: SystemTask,
    progress: SystemTaskStep,
    results: list[ArchiveBackfillPeriodResult],
) -> ArchiveBackfillPeriodResult | None:
    # archive period 会并发完成，但 task cursor 只能按连续前缀推进，避免失败重试时跳过中间月份。
    interval = cast(Interval, progress.interval)
    for result in sorted(results, key=lambda item: item.period.start_ms):
        if result.error:
            return result
        progress.raw_count += result.raw_count
        progress.inserted_count += result.inserted_count
        progress.next_start_ms = max(progress.next_start_ms, result.next_start_ms)
        if progress.next_start_ms > progress.end_ms:
            progress.status = "completed"
            progress.finished_at = datetime.now(timezone.utc)
            break
        if result.next_start_ms <= result.period.start_ms:
            return ArchiveBackfillPeriodResult(
                period=result.period,
                next_start_ms=result.period.start_ms,
                error=f"archive period did not advance cursor for {interval}",
            )
    if progress.next_start_ms > progress.end_ms:
        progress.status = "completed"
        progress.finished_at = datetime.now(timezone.utc)
    task.total_inserted = await sum_inserted_count(session, task.id)
    return None


async def persist_wave(
    session: AsyncSession,
    task: SystemTask,
    progress: SystemTaskStep,
    results: dict[int, KlinePage | str],
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
        page = value
        candles = [
            candle
            for candle in page.candles
            if candle.is_closed and int(candle.open_time.timestamp() * 1000) <= progress.end_ms
        ]
        progress.raw_count = int(getattr(progress, "raw_count", 0) or 0) + page.raw_count
        if not candles:
            if page.next_start_ms is not None and page.raw_count >= BINANCE_KLINE_LIMIT:
                progress.next_start_ms = page.next_start_ms
                continue
            if page.raw_count == 0:
                unavailable_end_ms = progress.end_ms
                if page.next_start_ms is not None:
                    unavailable_end_ms = min(progress.end_ms, page.next_start_ms - interval_ms)
                await upsert_candle_unavailable_range(
                    session,
                    symbol=task.symbol,
                    interval=interval,
                    start_ms=start_ms,
                    end_ms=unavailable_end_ms,
                    reason="Binance returned no klines for this closed range",
                )
            progress.next_start_ms = page.next_start_ms or progress.end_ms + interval_ms
            completed = progress.next_start_ms > progress.end_ms
            break
        await upsert_candles(session, candles)
        progress.inserted_count += len(candles)
        progress.next_start_ms = page.next_start_ms or int(candles[-1].open_time.timestamp() * 1000) + interval_ms
        if page.raw_count < BINANCE_KLINE_LIMIT:
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


def normalize_step_end_ms(end_ms: int, interval: Interval) -> int:
    aligned = align_interval_open_ms(end_ms, interval)
    if aligned == end_ms:
        return end_ms
    return latest_closed_open_ms(end_ms, interval)


async def create_task(
    session: AsyncSession,
    *,
    symbol: str,
    intervals: list[Interval],
    end_ms: int,
) -> SystemTask:
    now = datetime.now(timezone.utc)
    task = system_task_store.create_task(
        task_type="kline_backfill",
        symbol=symbol,
        status="running",
        message="K line backfill started",
        started_at=now,
        metadata={
            "concurrency": KLINE_BACKFILL_CONCURRENCY,
            "limit": BINANCE_KLINE_LIMIT,
            "target_end_ms": end_ms,
            "intervals": list(intervals),
        },
    )
    session.add(task)
    await session.flush()
    missing_ranges = await plan_candle_missing_ranges(session, symbol=symbol, intervals=intervals, end_ms=end_ms)
    for missing_range in missing_ranges:
        session.add(create_missing_range_step(task.id, missing_range))
    await session.commit()
    await session.refresh(task)
    return task


async def resume_task(
    session: AsyncSession,
    task: SystemTask,
    *,
    intervals: list[Interval],
) -> None:
    task.status = "running"
    task.error = ""
    task.message = "K line backfill resumed"
    task.finished_at = None
    progress = await list_task_progress(session, task.id)
    end_ms = max((item.end_ms for item in progress), default=int(datetime.now(timezone.utc).timestamp() * 1000))
    for item in progress:
        # 旧规划可能产生大量未开始的小缺口；resume 时重建这些 pending step，降低任务噪音。
        if item.status == "pending" and item.inserted_count <= 0 and item.raw_count <= 0:
            await session.delete(item)
            continue
        if item.status == "error":
            item.status = "pending"
            item.last_error = ""
            item.finished_at = None
    await session.flush()
    existing_keys = {item.step_key for item in await list_task_progress(session, task.id)}
    missing_ranges = await plan_candle_missing_ranges(session, symbol=task.symbol, intervals=intervals, end_ms=end_ms)
    for missing_range in missing_ranges:
        if missing_range.step_key not in existing_keys:
            session.add(create_missing_range_step(task.id, missing_range))
    await session.commit()


async def plan_candle_missing_ranges(
    session: AsyncSession,
    *,
    symbol: str,
    intervals: list[Interval],
    end_ms: int,
) -> list[CandleMissingRange]:
    missing_ranges: list[CandleMissingRange] = []
    for interval in sort_intervals_for_execution(intervals):
        interval_end_ms = latest_closed_open_ms(end_ms, interval)
        missing_ranges.extend(
            await plan_interval_missing_ranges(session, symbol=symbol, interval=interval, end_ms=interval_end_ms)
        )
    return missing_ranges


async def plan_interval_missing_ranges(
    session: AsyncSession,
    *,
    symbol: str,
    interval: Interval,
    end_ms: int,
) -> list[CandleMissingRange]:
    interval_ms = INTERVAL_MS[interval]
    target_start_ms = await history_coverage_start_ms(session, symbol=symbol, interval=interval, end_ms=end_ms)
    latest = await get_latest_candle(session, symbol=symbol, interval=interval)
    if latest is None:
        recent_start_ms = max(
            target_start_ms,
            end_ms - ((settings.candle_history_limit - 1) * interval_ms),
        )
        ranges = [(recent_start_ms, end_ms)]
        older_end_ms = recent_start_ms - interval_ms
        if target_start_ms <= older_end_ms:
            ranges.append((target_start_ms, older_end_ms))
        return [
            CandleMissingRange(interval=interval, start_ms=start_ms, end_ms=range_end_ms, index=index)
            for index, (start_ms, range_end_ms) in enumerate(ranges, start=1)
        ]

    interval_start_time = await get_earliest_candle_time(session, symbol=symbol, interval=interval)
    interval_start_ms = int(interval_start_time.timestamp() * 1000) if interval_start_time is not None else target_start_ms
    latest_ms = int(latest.open_time.timestamp() * 1000)
    planned: list[tuple[int, int]] = []
    if interval_start_ms - target_start_ms > interval_ms:
        planned.append((target_start_ms, interval_start_ms - interval_ms))

    missing_ranges = await list_candle_missing_ranges(
        session,
        symbol=symbol,
        interval=interval,
        start=ms_to_datetime(max(target_start_ms, interval_start_ms)),
        end=ms_to_datetime(min(end_ms, latest_ms)),
    )
    planned.extend(
        (int(missing_start.timestamp() * 1000), int(missing_end.timestamp() * 1000))
        for missing_start, missing_end in missing_ranges
    )

    latest_start_ms = latest_ms + interval_ms
    if latest_start_ms <= end_ms:
        planned.append((latest_start_ms, end_ms))
    planned = merge_missing_range_windows(planned, interval=interval)
    unavailable_ranges = await list_candle_unavailable_ranges(
        session,
        symbol=symbol,
        interval=interval,
        start_ms=target_start_ms,
        end_ms=end_ms,
    )
    planned = subtract_unavailable_ranges(planned, unavailable_ranges, interval=interval)
    # K 线下载统一只处理缺口窗口；相邻小缺口合并后用幂等 upsert 吸收重复返回。
    recent_first = sorted(planned, key=lambda item: (item[1], item[0]), reverse=True)
    return [
        CandleMissingRange(interval=interval, start_ms=start_ms, end_ms=range_end_ms, index=index)
        for index, (start_ms, range_end_ms) in enumerate(recent_first, start=1)
        if start_ms <= range_end_ms
    ]


def merge_missing_range_windows(ranges: list[tuple[int, int]], *, interval: Interval) -> list[tuple[int, int]]:
    if not ranges:
        return []
    max_bridge_ms = MISSING_RANGE_MERGE_PAGES * BINANCE_KLINE_LIMIT * INTERVAL_MS[interval]
    merged: list[tuple[int, int]] = []
    for start_ms, end_ms in sorted(ranges):
        if start_ms > end_ms:
            continue
        if not merged:
            merged.append((start_ms, end_ms))
            continue
        previous_start, previous_end = merged[-1]
        if start_ms - previous_end <= max_bridge_ms:
            merged[-1] = (previous_start, max(previous_end, end_ms))
        else:
            merged.append((start_ms, end_ms))
    return merged


def subtract_unavailable_ranges(
    ranges: list[tuple[int, int]],
    unavailable_ranges: list[tuple[int, int]],
    *,
    interval: Interval,
) -> list[tuple[int, int]]:
    if not ranges or not unavailable_ranges:
        return ranges
    interval_ms = INTERVAL_MS[interval]
    result: list[tuple[int, int]] = []
    unavailable = sorted(unavailable_ranges)
    for start_ms, end_ms in ranges:
        segments = [(start_ms, end_ms)]
        for unavailable_start, unavailable_end in unavailable:
            next_segments: list[tuple[int, int]] = []
            for segment_start, segment_end in segments:
                if unavailable_end < segment_start or unavailable_start > segment_end:
                    next_segments.append((segment_start, segment_end))
                    continue
                left_end = unavailable_start - interval_ms
                right_start = unavailable_end + interval_ms
                if segment_start <= left_end:
                    next_segments.append((segment_start, left_end))
                if right_start <= segment_end:
                    next_segments.append((right_start, segment_end))
            segments = next_segments
            if not segments:
                break
        result.extend(segments)
    return result


def create_missing_range_step(task_id: int, missing_range: CandleMissingRange) -> SystemTaskStep:
    return system_task_store.create_step(
        task_id=task_id,
        step_key=missing_range.step_key,
        interval=missing_range.interval,
        start_ms=missing_range.start_ms,
        cursor_ms=missing_range.start_ms,
        end_ms=missing_range.end_ms,
    )


def sort_intervals_for_execution(intervals: list[Interval]) -> list[Interval]:
    order = {interval: index for index, interval in enumerate(INTERVAL_EXECUTION_ORDER)}
    return sorted(intervals, key=lambda value: order.get(value, len(order)))


def sort_progress_for_execution(progress: list[SystemTaskStep]) -> list[SystemTaskStep]:
    order = {interval: index for index, interval in enumerate(INTERVAL_EXECUTION_ORDER)}
    return sorted(
        progress,
        key=lambda item: (
            order.get(cast(Interval, item.interval), len(order)),
            -item.end_ms,
            -item.start_ms,
            item.id,
        ),
    )


def ms_to_datetime(value: int) -> datetime:
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc)


async def history_coverage_start_ms(session: AsyncSession, *, symbol: str, interval: Interval, end_ms: int) -> int:
    earliest = await get_earliest_candle_time(session, symbol=symbol)
    if earliest is not None:
        return align_interval_open_ms(int(earliest.timestamp() * 1000), interval)
    # archive-first 后新库可以直接从 Binance spot 历史起点规划，不再只拉最近窗口。
    return align_interval_open_ms(BINANCE_SPOT_HISTORY_START_MS, interval)


async def latest_task(session: AsyncSession) -> SystemTask | None:
    return await system_task_store.latest_task(session, task_type="kline_backfill")


async def latest_resumable_task(session: AsyncSession, symbol: str) -> SystemTask | None:
    return await system_task_store.latest_resumable_task(
        session,
        task_type="kline_backfill",
        symbol=symbol,
    )


async def get_task(session: AsyncSession, task_id: int) -> SystemTask | None:
    return await system_task_store.get_task(session, task_id=task_id, task_type="kline_backfill")


async def get_progress(session: AsyncSession, progress_id: int) -> SystemTaskStep | None:
    return await system_task_store.get_step(session, progress_id)


async def list_task_progress(session: AsyncSession, task_id: int) -> list[SystemTaskStep]:
    return await system_task_store.list_steps(session, task_id)


async def sum_inserted_count(session: AsyncSession, task_id: int) -> int:
    return await system_task_store.sum_inserted_count(session, task_id)


def serialize_status(
    task: SystemTask,
    progress: list[SystemTaskStep],
    *,
    candle_ranges: dict[str, dict[str, Any]] | None = None,
) -> CandleBackfillStatus:
    ranges = candle_ranges or {}
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
            raw_count=item.raw_count,
            range=ranges.get(str(item.interval)),
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
        candle_ranges=ranges,
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
    return "system_tasks" in message and "UndefinedTableError" in message


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
    return sort_intervals_for_execution(intervals or list(SUPPORTED_INTERVALS))


candle_backfill_runner = CandleBackfillRunner()
candle_sync_service = CandleSyncService()
