from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi import Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal, get_session
from app.schemas.report import (
    AccountSummary,
    AnalyzeAccountRequest,
    AnalyzeAccountResponse,
    MarketPerformance,
    MarketPerformancePage,
    ReportAccount,
    ReportTask,
    UpdateReportAccountRequest,
)
from app.services.polymarket_client import PolymarketClient, PolymarketInputError
from app.services.market_metadata import ensure_market_metadata_for_slugs
from app.services.report_snapshot import get_report_snapshot
from app.services.report_store import (
    account_exists,
    create_task,
    get_account_activity_bounds,
    get_account_activity_count,
    get_task,
    list_account_activity_slugs,
    list_accounts,
    serialize_account,
    update_account,
    update_task,
    upsert_account,
    upsert_activities,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/reports", tags=["reports"])


@router.post("/accounts/analyze", response_model=AnalyzeAccountResponse)
async def analyze_account(
    payload: AnalyzeAccountRequest,
    session: AsyncSession = Depends(get_session),
) -> AnalyzeAccountResponse:
    task_id = uuid.uuid4().hex
    await create_task(session, task_id, message="已创建分析任务")
    # Polymarket activity 下载可能很慢，HTTP 请求只创建任务，实际分析放后台执行。
    asyncio.create_task(run_account_analysis(task_id, payload))
    return AnalyzeAccountResponse(task_id=task_id, status="running")


@router.get("/tasks/{task_id}", response_model=ReportTask)
async def task_status(task_id: str, session: AsyncSession = Depends(get_session)) -> ReportTask:
    task = await get_task(session, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    return task


@router.get("/accounts", response_model=list[ReportAccount])
async def accounts(session: AsyncSession = Depends(get_session)) -> list[ReportAccount]:
    return await list_accounts(session)


@router.patch("/accounts/{account_id}", response_model=ReportAccount)
async def patch_account(
    account_id: str,
    payload: UpdateReportAccountRequest,
    session: AsyncSession = Depends(get_session),
) -> ReportAccount:
    account = await update_account(session, account_id, note=payload.note, favorite=payload.favorite)
    if account is None:
        raise HTTPException(status_code=404, detail="account not found")
    activity_count = await get_account_activity_count(session, account_id)
    return serialize_account(account, activity_count=activity_count)


@router.get("/accounts/{account_id}/summary", response_model=AccountSummary)
async def account_summary(account_id: str, session: AsyncSession = Depends(get_session)) -> AccountSummary:
    if not await account_exists(session, account_id):
        raise HTTPException(status_code=404, detail="account not found")
    snapshot = await get_report_snapshot(session, account_id)
    return snapshot.summary


@router.get("/accounts/{account_id}/markets", response_model=MarketPerformancePage)
async def account_markets(
    account_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    search: str = "",
    start_date: str = "",
    end_date: str = "",
    only_bilateral: bool = False,
    session: AsyncSession = Depends(get_session),
) -> MarketPerformancePage:
    if not await account_exists(session, account_id):
        raise HTTPException(status_code=404, detail="account not found")
    markets = filter_market_performance(
        (await get_report_snapshot(session, account_id)).markets,
        search=search,
        start_date=start_date,
        end_date=end_date,
        only_bilateral=only_bilateral,
    )
    return MarketPerformancePage(
        items=markets[offset : offset + limit],
        total=len(markets),
        offset=offset,
        limit=limit,
    )


async def run_account_analysis(task_id: str, payload: AnalyzeAccountRequest) -> None:
    async with AsyncSessionLocal() as session:
        try:
            await update_task(session, task_id, message="解析 Polymarket 用户", percent=10)
            client = PolymarketClient()
            resolved = await client.resolve_account(payload.input)

            account = await upsert_account(session, resolved)
            await update_task(
                session,
                task_id,
                account_id=account.id,
                message="下载 Polymarket activity",
                percent=35,
            )

            existing_count, oldest_activity_at, _ = await get_account_activity_bounds(session, account.id)
            target_count = max(payload.activity_limit, 0)
            existing_count_for_target = min(existing_count, target_count)
            remaining_activity_count = max(target_count - existing_count_for_target, 0)
            # 已有本地 activity 时从最老记录之前继续拉，避免重复下载同一时间段。
            resume_end = activity_resume_end(oldest_activity_at) if oldest_activity_at and existing_count_for_target > 0 else None
            downloaded_count = existing_count_for_target
            saved_count = 0
            market_slugs: set[str] = set()
            if remaining_activity_count <= 0:
                await update_task(
                    session,
                    task_id,
                    account_id=account.id,
                    message=f"本地已有 {existing_count_for_target} 条 activity，跳过下载",
                    percent=74,
                )
            else:
                async for activity_batch in client.iter_activity_batches(
                    wallet=resolved.proxy_wallet,
                    activity_limit=remaining_activity_count,
                    end=resume_end,
                ):
                    downloaded_count += len(activity_batch)
                    saved_count += await upsert_activities(session, account.id, activity_batch)
                    market_slugs.update(activity.slug for activity in activity_batch if activity.slug)
                    # 进度只代表下载和写入阶段，后面还要补市场元数据和重算快照。
                    progress = min(74, 35 + int((downloaded_count / max(target_count, 1)) * 39))
                    await update_task(
                        session,
                        task_id,
                        account_id=account.id,
                        message=f"下载并写入 Polymarket activity: {downloaded_count}/{target_count}",
                        percent=progress,
                    )
            await update_task(
                session,
                task_id,
                account_id=account.id,
                message="补全市场元数据",
                percent=88,
            )
            market_slugs.update(await list_account_activity_slugs(session, account.id))
            # 市场结果可能不在 activity 里，需要额外用 slug 补全后才能计算胜负和收益。
            market_metadata = await ensure_market_metadata_for_slugs(session, market_slugs)
            total_count = await get_account_activity_count(session, account.id)
            await update_task(
                session,
                task_id,
                status="done",
                account_id=account.id,
                message="分析任务完成",
                percent=100,
                result={
                    "account_id": account.id,
                    "proxy_wallet": account.proxy_wallet,
                    "normalized_user": account.normalized_user,
                    "downloaded_count": downloaded_count,
                    "saved_count": saved_count,
                    "market_metadata_count": len(market_metadata),
                    "total_activity_count": total_count,
                },
                error="",
            )
        except PolymarketInputError as exc:
            await update_task(
                session,
                task_id,
                status="error",
                message="输入解析失败",
                percent=100,
                error=str(exc),
            )
        except Exception as exc:
            logger.exception("Report analysis task failed", extra={"task_id": task_id})
            await update_task(
                session,
                task_id,
                status="error",
                message="分析任务失败",
                percent=100,
                error=str(exc),
            )


def activity_resume_end(oldest_activity_at: datetime) -> int:
    if oldest_activity_at.tzinfo is None:
        oldest_activity_at = oldest_activity_at.replace(tzinfo=timezone.utc)
    # Polymarket end 参数按秒截断，减 1 秒避免把当前最老记录再次包含进来。
    return int(oldest_activity_at.timestamp()) - 1


def filter_market_performance(
    markets: list[MarketPerformance],
    *,
    search: str,
    start_date: str,
    end_date: str,
    only_bilateral: bool,
) -> list[MarketPerformance]:
    keyword = search.strip().lower()
    start = parse_filter_date(start_date, end_of_day=False)
    end = parse_filter_date(end_date, end_of_day=True)
    result: list[MarketPerformance] = []
    for market in markets:
        if keyword:
            haystack = " ".join(
                value
                for value in [market.title, market.slug, market.condition_id, market.event_slug]
                if value
            ).lower()
            if keyword not in haystack:
                continue
        if start and (market.market_date is None or market.market_date < start):
            continue
        if end and (market.market_date is None or market.market_date > end):
            continue
        if only_bilateral and not (market.up_shares > 0 and market.down_shares > 0):
            continue
        result.append(market)
    return result


def parse_filter_date(value: str, *, end_of_day: bool) -> datetime | None:
    if not value:
        return None
    try:
        date = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    if end_of_day:
        return date.replace(hour=23, minute=59, second=59, microsecond=999999)
    return date
