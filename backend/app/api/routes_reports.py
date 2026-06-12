from __future__ import annotations

import asyncio
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal, get_session
from app.schemas.report import (
    AccountSummary,
    AnalyzeAccountRequest,
    AnalyzeAccountResponse,
    MarketPerformance,
    ReportAccount,
    ReportTask,
    UpdateReportAccountRequest,
)
from app.services.polymarket_client import PolymarketClient, PolymarketInputError
from app.services.report_analysis import build_account_summary, build_market_performance
from app.services.report_store import (
    account_exists,
    create_task,
    get_account_activity_count,
    get_task,
    list_account_activities,
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
    activities = await list_account_activities(session, account_id)
    return build_account_summary(account_id, activities)


@router.get("/accounts/{account_id}/markets", response_model=list[MarketPerformance])
async def account_markets(account_id: str, session: AsyncSession = Depends(get_session)) -> list[MarketPerformance]:
    if not await account_exists(session, account_id):
        raise HTTPException(status_code=404, detail="account not found")
    activities = await list_account_activities(session, account_id)
    return build_market_performance(activities)


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

            activities = await client.fetch_activity(
                wallet=resolved.proxy_wallet,
                activity_limit=payload.activity_limit,
            )
            await update_task(
                session,
                task_id,
                account_id=account.id,
                message="写入本地数据库",
                percent=75,
            )
            saved_count = await upsert_activities(session, account.id, activities)
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
                    "downloaded_count": len(activities),
                    "saved_count": saved_count,
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
