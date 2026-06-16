from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Select, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from app.db.models import Account, Activity, AnalysisTask, MarketMetadata
from app.schemas.report import ReportAccount, ReportTask, TaskStatus
from app.services.polymarket_client import NormalizedActivity, ResolvedPolymarketAccount, account_id_for_wallet

ACTIVITY_UPSERT_BATCH_SIZE = 1000


async def create_task(session: AsyncSession, task_id: str, message: str = "排队中") -> AnalysisTask:
    task = AnalysisTask(
        id=task_id,
        account_id=None,
        status="running",
        message=message,
        percent=0,
        result={},
        error="",
    )
    session.add(task)
    await session.commit()
    return task


async def update_task(
    session: AsyncSession,
    task_id: str,
    *,
    status: TaskStatus | None = None,
    account_id: str | None = None,
    message: str | None = None,
    percent: int | None = None,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    task = await session.get(AnalysisTask, task_id)
    if task is None:
        return
    if status is not None:
        task.status = status
    if account_id is not None:
        task.account_id = account_id
    if message is not None:
        task.message = message
    if percent is not None:
        task.percent = percent
    if result is not None:
        task.result = result
    if error is not None:
        task.error = error
    await session.commit()


async def fail_interrupted_running_tasks(session: AsyncSession) -> int:
    # 本地后台任务只存在于当前进程；重启后 running 不可恢复，必须明确失败避免前端误等。
    result = await session.execute(
        update(AnalysisTask)
        .where(AnalysisTask.status == "running")
        .values(
            status="error",
            message="服务重启，任务已中断，请重新分析",
            percent=100,
            error="service restarted while task was running",
            updated_at=func.now(),
        )
    )
    await session.commit()
    return int(result.rowcount or 0)


async def delete_finished_tasks_before(session: AsyncSession, before: datetime) -> int:
    result = await session.execute(
        delete(AnalysisTask).where(
            AnalysisTask.status.in_(["done", "error"]),
            AnalysisTask.updated_at < before,
        )
    )
    await session.commit()
    return int(result.rowcount or 0)


async def get_task(session: AsyncSession, task_id: str) -> ReportTask | None:
    task = await session.get(AnalysisTask, task_id)
    return serialize_task(task) if task else None


async def account_exists(session: AsyncSession, account_id: str) -> bool:
    return await session.get(Account, account_id) is not None


async def update_account(
    session: AsyncSession,
    account_id: str,
    *,
    note: str | None = None,
    favorite: bool | None = None,
) -> Account | None:
    account = await session.get(Account, account_id)
    if account is None:
        return None
    if note is not None:
        account.note = note
    if favorite is not None:
        account.favorite = favorite
    await session.commit()
    await session.refresh(account)
    return account


async def upsert_account(session: AsyncSession, resolved: ResolvedPolymarketAccount) -> Account:
    account_id = account_id_for_wallet(resolved.proxy_wallet)
    statement = insert(Account).values(
        id=account_id,
        input=resolved.input,
        normalized_user=resolved.normalized_user,
        proxy_wallet=resolved.proxy_wallet.lower(),
        profile=resolved.profile,
        last_downloaded_at=datetime.now(timezone.utc),
    )
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[Account.id],
            set_={
                "input": statement.excluded.input,
                "normalized_user": statement.excluded.normalized_user,
                "proxy_wallet": statement.excluded.proxy_wallet,
                "profile": statement.excluded.profile,
                "last_downloaded_at": statement.excluded.last_downloaded_at,
            },
        )
    )
    await session.commit()
    account = await session.get(Account, account_id)
    if account is None:
        raise RuntimeError("Account upsert failed")
    return account


async def upsert_activities(session: AsyncSession, account_id: str, activities: list[NormalizedActivity]) -> int:
    if not activities:
        return 0
    rows = [activity_row(account_id, activity) for activity in activities]
    for batch in iter_batches(rows, ACTIVITY_UPSERT_BATCH_SIZE):
        await upsert_activity_rows(session, batch)
        await session.commit()
    return len(activities)


async def delete_account_activities(session: AsyncSession, account_id: str) -> int:
    result = await session.execute(delete(Activity).where(Activity.account_id == account_id))
    await session.commit()
    return int(result.rowcount or 0)


async def upsert_activity_rows(session: AsyncSession, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    statement = insert(Activity).values(rows)
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[Activity.id],
            set_={
                "timestamp": statement.excluded.timestamp,
                "type": statement.excluded.type,
                "condition_id": statement.excluded.condition_id,
                "slug": statement.excluded.slug,
                "event_slug": statement.excluded.event_slug,
                "title": statement.excluded.title,
                "side": statement.excluded.side,
                "outcome": statement.excluded.outcome,
                "asset": statement.excluded.asset,
                "price": statement.excluded.price,
                "size": statement.excluded.size,
                "usdc_size": statement.excluded.usdc_size,
                "transaction_hash": statement.excluded.transaction_hash,
                "raw": statement.excluded.raw,
            },
        )
    )


def activity_row(account_id: str, activity: NormalizedActivity) -> dict[str, Any]:
    return {
        "id": activity.id,
        "account_id": account_id,
        "proxy_wallet": activity.proxy_wallet.lower(),
        "timestamp": activity.timestamp,
        "type": activity.type,
        "condition_id": activity.condition_id,
        "slug": activity.slug,
        "event_slug": activity.event_slug,
        "title": activity.title,
        "side": activity.side,
        "outcome": activity.outcome,
        "asset": activity.asset,
        "price": activity.price,
        "size": activity.size,
        "usdc_size": activity.usdc_size,
        "transaction_hash": activity.transaction_hash,
        "raw": activity.raw,
    }


def iter_batches(rows: list[dict[str, Any]], batch_size: int):
    for index in range(0, len(rows), batch_size):
        yield rows[index : index + batch_size]


async def list_accounts(session: AsyncSession) -> list[ReportAccount]:
    count_subquery = (
        select(
            Activity.account_id.label("account_id"),
            func.count(Activity.id).label("activity_count"),
            func.max(Activity.timestamp).label("latest_activity_at"),
        )
        .group_by(Activity.account_id)
        .subquery()
    )
    statement: Select[tuple[Account, int | None, datetime | None]] = (
        select(
            Account,
            count_subquery.c.activity_count,
            count_subquery.c.latest_activity_at,
        )
        .outerjoin(count_subquery, count_subquery.c.account_id == Account.id)
        .order_by(Account.last_downloaded_at.desc().nullslast(), Account.created_at.desc())
    )
    rows = (await session.execute(statement)).all()
    return [
        serialize_account(account, activity_count=activity_count or 0, latest_activity_at=latest_activity_at)
        for account, activity_count, latest_activity_at in rows
    ]


async def get_account_activity_count(session: AsyncSession, account_id: str) -> int:
    result = await session.scalar(select(func.count(Activity.id)).where(Activity.account_id == account_id))
    return int(result or 0)


async def get_account_activity_bounds(session: AsyncSession, account_id: str) -> tuple[int, datetime | None, datetime | None]:
    statement = select(
        func.count(Activity.id),
        func.min(Activity.timestamp),
        func.max(Activity.timestamp),
    ).where(Activity.account_id == account_id)
    count, oldest, newest = (await session.execute(statement)).one()
    return int(count or 0), oldest, newest


async def list_account_activities(session: AsyncSession, account_id: str) -> list[Activity]:
    result = await session.scalars(
        select(Activity)
        .options(
            load_only(
                Activity.id,
                Activity.timestamp,
                Activity.type,
                Activity.condition_id,
                Activity.slug,
                Activity.event_slug,
                Activity.title,
                Activity.side,
                Activity.outcome,
                Activity.asset,
                Activity.price,
                Activity.size,
                Activity.usdc_size,
            )
        )
        .where(Activity.account_id == account_id)
        .order_by(Activity.timestamp.asc())
    )
    return list(result.all())


async def list_account_market_activities(session: AsyncSession, account_id: str, market_id: str) -> list[Activity]:
    # 详情页的 market_id 沿用报表聚合身份：title 优先，其次 slug / condition_id。
    result = await session.scalars(
        select(Activity)
        .where(
            Activity.account_id == account_id,
            (
                (Activity.title == market_id)
                | (Activity.slug == market_id)
                | (Activity.condition_id == market_id)
            ),
        )
        .order_by(Activity.timestamp.asc())
    )
    return list(result.all())


async def list_account_activity_slugs(session: AsyncSession, account_id: str) -> set[str]:
    result = await session.scalars(
        select(Activity.slug)
        .where(Activity.account_id == account_id, Activity.slug.is_not(None))
        .distinct()
    )
    return {slug for slug in result.all() if slug}


async def list_market_metadata(session: AsyncSession, slugs: set[str]) -> dict[str, MarketMetadata]:
    if not slugs:
        return {}
    result = await session.scalars(select(MarketMetadata).where(MarketMetadata.slug.in_(slugs)))
    return {metadata.slug: metadata for metadata in result.all()}


async def upsert_market_metadata_rows(session: AsyncSession, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    statement = insert(MarketMetadata).values(rows)
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[MarketMetadata.slug],
            set_={
                "closed": statement.excluded.closed,
                "outcome": statement.excluded.outcome,
                "raw_outcome": statement.excluded.raw_outcome,
                "event": statement.excluded.event,
                "market": statement.excluded.market,
                "fetched_at": statement.excluded.fetched_at,
                "updated_at": func.now(),
            },
        )
    )
    await session.commit()
    return len(rows)


def serialize_task(task: AnalysisTask) -> ReportTask:
    return ReportTask(
        id=task.id,
        account_id=task.account_id,
        status=task.status,  # type: ignore[arg-type]
        message=task.message,
        percent=task.percent,
        result=task.result or {},
        error=task.error,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


def serialize_account(
    account: Account,
    *,
    activity_count: int = 0,
    latest_activity_at: datetime | None = None,
) -> ReportAccount:
    return ReportAccount(
        id=account.id,
        input=account.input,
        normalized_user=account.normalized_user,
        proxy_wallet=account.proxy_wallet,
        profile=account.profile or {},
        favorite=account.favorite,
        note=account.note,
        last_downloaded_at=account.last_downloaded_at,
        activity_count=activity_count,
        latest_activity_at=latest_activity_at,
        created_at=account.created_at,
        updated_at=account.updated_at,
    )
