from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

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
        return [await serialize_system_task(session, task) for task in tasks]


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


@router.post("/system-tasks/{task_type}/start", response_model=SystemTaskStatus)
async def start_system_task(task_type: SystemTaskType, symbol: str = settings.binance_symbol) -> SystemTaskStatus:
    if task_type == "kline_backfill":
        status = await candle_backfill_runner.start_all(symbol=symbol)
    elif task_type == "indicator_backfill":
        status = await indicator_backfill_runner.start_all(symbol=symbol)
    else:
        raise HTTPException(status_code=404, detail="unknown system task type")
    async with AsyncSessionLocal() as session:
        task = await latest_task(session, task_type=task_type, symbol=status.symbol.upper())
        if task is None:
            return SystemTaskStatus(task_type=task_type, symbol=status.symbol, status=status.state, message=status.message)
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
    steps = [
        SystemTaskStepStatus(
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
        for step in step_rows.all()
    ]
    ranges = await list_candle_ranges(session, task.symbol) if task.task_type == "kline_backfill" else {}
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
        steps=steps,
        candle_ranges=ranges,
    )
