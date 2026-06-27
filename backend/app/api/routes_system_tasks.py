from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any, Literal, Sequence

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.config import settings
from app.db.models import SystemTask, SystemTaskStep
from app.db.session import AsyncSessionLocal
from app.services.candle_backfill import candle_backfill_runner
from app.services.candle_store import list_candle_ranges
from app.services.indicator_backfill import indicator_backfill_runner

router = APIRouter(tags=["system-tasks"])

SystemTaskType = Literal["kline_backfill", "indicator_backfill"]


class SystemTaskStepStatus(BaseModel):
    id: int
    step_key: str
    interval: str
    status: str
    start_ms: int
    cursor_ms: int
    end_ms: int | None = None
    inserted_count: int
    raw_count: int
    last_error: str = ""
    started_at: datetime | None = None
    finished_at: datetime | None = None


class SystemTaskStatus(BaseModel):
    id: int | None = None
    task_type: SystemTaskType
    symbol: str = Field(default_factory=lambda: settings.binance_symbol)
    status: str = "idle"
    message: str = ""
    error: str | None = None
    total_inserted: int = 0
    started_at: datetime | None = None
    finished_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    current_step: SystemTaskStepStatus | None = None
    raw_count: int = 0
    step_count: int = 0
    steps: list[SystemTaskStepStatus] = Field(default_factory=list)
    candle_ranges: dict[str, dict[str, Any]] = Field(default_factory=dict)


@router.get("/system-tasks", response_model=list[SystemTaskStatus])
async def system_tasks(symbol: str = settings.binance_symbol) -> list[SystemTaskStatus]:
    async with AsyncSessionLocal() as session:
        rows = await session.scalars(
            select(SystemTask)
            .where(SystemTask.symbol == symbol.upper())
            .order_by(SystemTask.id.desc())
            .limit(20)
        )
        tasks = list(rows.all())
        steps_by_task_id = await load_steps_by_task_id(session, [task.id for task in tasks])
        return [
            build_system_task_summary(task, steps_by_task_id.get(task.id, [])) for task in tasks
        ]


@router.get("/system-tasks/latest", response_model=SystemTaskStatus)
async def latest_system_task(
    task_type: SystemTaskType = Query(...),
    symbol: str = settings.binance_symbol,
) -> SystemTaskStatus:
    async with AsyncSessionLocal() as session:
        task = await latest_task(session, task_type=task_type, symbol=symbol.upper())
        if task is None:
            return SystemTaskStatus(task_type=task_type, symbol=symbol.upper())
        return await serialize_system_task(session, task)


@router.get("/system-tasks/{task_id}", response_model=SystemTaskStatus)
async def system_task_detail(task_id: int) -> SystemTaskStatus:
    async with AsyncSessionLocal() as session:
        task = await session.get(SystemTask, task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="system task not found")
        return await serialize_system_task(session, task)


@router.post("/system-tasks/{task_type}/start", response_model=SystemTaskStatus)
async def start_system_task(
    task_type: SystemTaskType, symbol: str = settings.binance_symbol
) -> SystemTaskStatus:
    if task_type == "kline_backfill":
        status = await candle_backfill_runner.start_all(symbol=symbol)
    elif task_type == "indicator_backfill":
        status = await indicator_backfill_runner.start_all(symbol=symbol)
    else:
        raise HTTPException(status_code=404, detail="unknown system task type")
    async with AsyncSessionLocal() as session:
        task = await latest_task(session, task_type=task_type, symbol=status.symbol.upper())
        if task is None:
            return SystemTaskStatus(
                task_type=task_type,
                symbol=status.symbol,
                status=status.state,
                message=status.message,
            )
        return await serialize_system_task(session, task)


async def latest_task(session, *, task_type: SystemTaskType, symbol: str) -> SystemTask | None:
    return await session.scalar(
        select(SystemTask)
        .where(SystemTask.task_type == task_type, SystemTask.symbol == symbol)
        .order_by(SystemTask.id.desc())
        .limit(1)
    )


async def serialize_system_task(session, task: SystemTask) -> SystemTaskStatus:
    step_rows = await session.scalars(
        select(SystemTaskStep)
        .where(SystemTaskStep.task_id == task.id)
        .order_by(SystemTaskStep.id.asc())
    )
    steps = list(step_rows.all())
    ranges = (
        await list_candle_ranges(session, task.symbol) if task.task_type == "kline_backfill" else {}
    )
    return build_system_task_status(task, steps, candle_ranges=ranges)


async def load_steps_by_task_id(
    session, task_ids: Sequence[int]
) -> dict[int, list[SystemTaskStep]]:
    if not task_ids:
        return {}
    step_rows = await session.scalars(
        select(SystemTaskStep)
        .where(SystemTaskStep.task_id.in_(task_ids))
        .order_by(SystemTaskStep.task_id.asc(), SystemTaskStep.id.asc())
    )
    grouped: dict[int, list[SystemTaskStep]] = defaultdict(list)
    for step in step_rows.all():
        grouped[step.task_id].append(step)
    return grouped


def build_system_task_status(
    task: SystemTask,
    step_rows: Sequence[SystemTaskStep],
    *,
    candle_ranges: dict[str, dict[str, Any]] | None = None,
) -> SystemTaskStatus:
    steps = [build_system_task_step_status(step) for step in step_rows]
    return SystemTaskStatus(
        id=task.id,
        task_type=task.task_type,  # type: ignore[arg-type]
        symbol=task.symbol,
        status=task.status,
        message=task.message,
        error=task.error or None,
        total_inserted=task.total_inserted,
        started_at=task.started_at,
        finished_at=task.finished_at,
        metadata=task.task_metadata or {},
        current_step=find_current_step(steps),
        raw_count=sum(step.raw_count for step in steps),
        step_count=len(steps),
        steps=steps,
        candle_ranges=candle_ranges or {},
    )


def build_system_task_summary(
    task: SystemTask,
    step_rows: Sequence[SystemTaskStep],
) -> SystemTaskStatus:
    steps = [build_system_task_step_status(step) for step in step_rows]
    # 轮询列表只承载摘要，完整 steps 和 candle_ranges 留给详情接口，避免每次刷新传输/聚合大数据。
    return SystemTaskStatus(
        id=task.id,
        task_type=task.task_type,  # type: ignore[arg-type]
        symbol=task.symbol,
        status=task.status,
        message=task.message,
        error=task.error or None,
        total_inserted=task.total_inserted,
        started_at=task.started_at,
        finished_at=task.finished_at,
        metadata=task.task_metadata or {},
        current_step=find_current_step(steps),
        raw_count=sum(step.raw_count for step in steps),
        step_count=len(steps),
        steps=[],
        candle_ranges={},
    )


def build_system_task_step_status(step: SystemTaskStep) -> SystemTaskStepStatus:
    return SystemTaskStepStatus(
        id=step.id,
        step_key=step.step_key,
        interval=step.interval,
        status=step.status,
        start_ms=step.start_ms,
        cursor_ms=step.cursor_ms,
        end_ms=step.end_ms,
        inserted_count=step.inserted_count,
        raw_count=step.raw_count,
        last_error=step.last_error,
        started_at=step.started_at,
        finished_at=step.finished_at,
    )


def find_current_step(steps: Sequence[SystemTaskStepStatus]) -> SystemTaskStepStatus | None:
    return next(
        (item for item in steps if item.status == "running"),
        next((item for item in steps if item.status in {"pending", "error"}), None),
    )
