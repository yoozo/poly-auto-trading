from __future__ import annotations

import logging
from datetime import timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.core.config import settings
from app.cron import jobs
from app.services.polymarket_monitor import polymarket_market_monitor

logger = logging.getLogger(__name__)

MARKET_REFRESH_JOB_ID = "polymarket_market_refresh"

_scheduler: AsyncIOScheduler | None = None


def start_cron_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        return

    scheduler = AsyncIOScheduler(
        timezone="UTC",
        job_defaults={
            "coalesce": True,
            "max_instances": 1,
            "misfire_grace_time": 30,
        },
    )
    _scheduler = scheduler
    register_jobs(scheduler)
    scheduler.start()
    logger.info("Cron scheduler started")


def stop_cron_scheduler() -> None:
    global _scheduler
    if _scheduler is None:
        return
    if _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Cron scheduler stopped")
    _scheduler = None


def register_jobs(scheduler: AsyncIOScheduler) -> None:
    # 这里只维护调度策略；业务实现仍留在 service，避免 cron 层持有领域状态。
    scheduler.add_job(
        jobs.cleanup_diagnostic_data_job,
        trigger="cron",
        hour=3,
        minute=0,
        id="maintenance_cleanup_diagnostic_data",
        name="Cleanup diagnostic data",
        replace_existing=True,
    )
    if settings.binance_ws_enabled:
        scheduler.add_job(
            jobs.sync_binance_live_window_job,
            trigger=IntervalTrigger(seconds=60),
            id="binance_live_window_sync",
            name="Sync Binance live candle window",
            replace_existing=True,
        )
    if settings.polymarket_ws_enabled:
        schedule_polymarket_market_refresh(0)
    scheduler.add_job(
        jobs.refresh_polymarket_account_snapshot_job,
        trigger=IntervalTrigger(seconds=max(5, settings.polymarket_account_refresh_seconds)),
        id="polymarket_account_snapshot_refresh",
        name="Refresh Polymarket account snapshot",
        replace_existing=True,
    )


def schedule_polymarket_market_refresh(delay_seconds: float) -> None:
    scheduler = _scheduler
    if scheduler is None:
        return
    scheduler.add_job(
        _run_polymarket_market_refresh_and_reschedule,
        trigger=DateTrigger(run_date=jobs.utc_now() + timedelta(seconds=max(0.0, delay_seconds))),
        id=MARKET_REFRESH_JOB_ID,
        name="Refresh Polymarket BTC Up/Down markets",
        replace_existing=True,
    )


def schedule_polymarket_market_signal_refresh(delay_seconds: float | None = None) -> None:
    scheduler = _scheduler
    if scheduler is None:
        return
    if delay_seconds is None:
        delay_seconds = polymarket_market_monitor.signal_refresh_delay()
    current_job = scheduler.get_job(MARKET_REFRESH_JOB_ID)
    if current_job and current_job.next_run_time:
        current_delay = (current_job.next_run_time - jobs.utc_now()).total_seconds()
        delay_seconds = min(max(0.0, delay_seconds), max(0.0, current_delay))
    # WS 事件和边界刷新复用同一个 job id，避免两条刷新链路并发写市场缓存。
    schedule_polymarket_market_refresh(delay_seconds)


async def _run_polymarket_market_refresh_and_reschedule() -> None:
    try:
        await jobs.refresh_polymarket_markets_job()
    finally:
        schedule_polymarket_market_refresh(await polymarket_market_monitor.next_refresh_delay())
