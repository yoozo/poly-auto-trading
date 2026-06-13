from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.db.session import AsyncSessionLocal
from app.services.binance_monitor import binance_monitor
from app.services.polymarket_monitor import polymarket_market_monitor
from app.services.report_store import fail_interrupted_running_tasks
from app.services.service_events import record_service_event


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    async with AsyncSessionLocal() as session:
        interrupted = await fail_interrupted_running_tasks(session)
        if interrupted:
            await record_service_event(
                session,
                service="analysis_task",
                level="warning",
                message="服务重启后已中断未完成分析任务",
                payload={"interrupted_count": interrupted},
            )
    await binance_monitor.start()
    await polymarket_market_monitor.start()
    try:
        yield
    finally:
        await polymarket_market_monitor.stop()
        await binance_monitor.stop()
