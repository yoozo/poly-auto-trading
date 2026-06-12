from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal
from app.schemas.report import AccountSummary, MarketPerformance
from app.services.market_metadata import collect_market_slugs
from app.services.report_analysis import build_account_summary, build_market_performance
from app.services.report_store import get_account_activity_bounds, list_account_activities, list_market_metadata

REPORT_SNAPSHOT_TTL = timedelta(seconds=60)


@dataclass(frozen=True)
class ReportSnapshotKey:
    account_id: str
    activity_count: int
    newest_activity_at: datetime | None


@dataclass
class ReportSnapshot:
    key: ReportSnapshotKey
    summary: AccountSummary
    markets: list[MarketPerformance]
    cached_at: datetime


_SNAPSHOT_CACHE: dict[str, ReportSnapshot] = {}
_IN_FLIGHT_SNAPSHOTS: dict[str, tuple[ReportSnapshotKey, asyncio.Task[ReportSnapshot]]] = {}
_SNAPSHOT_LOCK = asyncio.Lock()


async def get_report_snapshot(session: AsyncSession, account_id: str) -> ReportSnapshot:
    count, _, newest = await get_account_activity_bounds(session, account_id)
    key = ReportSnapshotKey(account_id=account_id, activity_count=count, newest_activity_at=newest)
    cached = _SNAPSHOT_CACHE.get(account_id)
    if cached and cached.key == key and not snapshot_expired(cached):
        return cached

    async with _SNAPSHOT_LOCK:
        cached = _SNAPSHOT_CACHE.get(account_id)
        if cached and cached.key == key and not snapshot_expired(cached):
            return cached
        inflight = _IN_FLIGHT_SNAPSHOTS.get(account_id)
        if inflight and inflight[0] == key and not inflight[1].done():
            task = inflight[1]
        else:
            task = asyncio.create_task(build_report_snapshot(key))
            _IN_FLIGHT_SNAPSHOTS[account_id] = (key, task)
            task.add_done_callback(lambda completed, account_id=account_id: clear_snapshot_task(account_id, completed))

    return await task


async def build_report_snapshot(key: ReportSnapshotKey) -> ReportSnapshot:
    async with AsyncSessionLocal() as session:
        activities = await list_account_activities(session, key.account_id)
        slugs = collect_market_slugs(activities)
        market_metadata = await list_market_metadata(session, slugs)
    snapshot = ReportSnapshot(
        key=key,
        summary=build_account_summary(key.account_id, activities, market_metadata=market_metadata),
        markets=build_market_performance(activities, market_metadata=market_metadata),
        cached_at=datetime.now(timezone.utc),
    )
    _SNAPSHOT_CACHE[key.account_id] = snapshot
    return snapshot


def snapshot_expired(snapshot: ReportSnapshot) -> bool:
    return datetime.now(timezone.utc) - snapshot.cached_at >= REPORT_SNAPSHOT_TTL


def clear_snapshot_task(account_id: str, completed: asyncio.Task[ReportSnapshot]) -> None:
    inflight = _IN_FLIGHT_SNAPSHOTS.get(account_id)
    if inflight and inflight[1] is completed:
        _IN_FLIGHT_SNAPSHOTS.pop(account_id, None)


def clear_report_snapshot_cache() -> None:
    _SNAPSHOT_CACHE.clear()
    _IN_FLIGHT_SNAPSHOTS.clear()
