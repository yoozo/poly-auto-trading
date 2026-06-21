from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.db.session import AsyncSessionLocal
from app.services.binance_monitor import binance_monitor
from app.services.maintenance import cleanup_diagnostic_data
from app.services.polymarket_account_monitor import polymarket_account_monitor
from app.services.polymarket_monitor import polymarket_market_monitor
from app.services.service_events import record_service_event

logger = logging.getLogger(__name__)


async def cleanup_diagnostic_data_job() -> None:
    async with AsyncSessionLocal() as session:
        result = await cleanup_diagnostic_data(session)
        await record_service_event(
            session,
            service="maintenance",
            level="info",
            message="Diagnostic data cleanup completed",
            payload={
                "service_events_deleted": result.service_events_deleted,
                "analysis_tasks_deleted": result.analysis_tasks_deleted,
            },
        )


async def sync_binance_live_window_job() -> None:
    await binance_monitor.backfill_once()


async def refresh_polymarket_markets_job() -> None:
    await polymarket_market_monitor.scheduled_refresh_once()


async def refresh_polymarket_account_snapshot_job() -> None:
    await polymarket_account_monitor.refresh_account_snapshot()
    await polymarket_account_monitor.broadcast_all_snapshots()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
