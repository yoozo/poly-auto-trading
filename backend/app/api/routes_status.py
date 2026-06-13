import logging
from datetime import datetime

from fastapi import APIRouter
from fastapi import Depends
from fastapi import Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.schemas.status import ServiceEventRecord
from app.services.notifications import refresh_telegram_service_health
from app.services.service_events import list_service_events
from app.services.service_health import ServiceHealth, service_health_store

router = APIRouter(tags=["status"])
logger = logging.getLogger(__name__)


@router.get("/status/services", response_model=list[ServiceHealth])
async def services(session: AsyncSession = Depends(get_session)) -> list[ServiceHealth]:
    try:
        await refresh_telegram_service_health(session)
    except Exception as exc:
        logger.warning("Failed to refresh Telegram service health", exc_info=exc)
        service_health_store.set("telegram", "error", last_error=str(exc))
    return service_health_store.list()


@router.get("/status/events", response_model=list[ServiceEventRecord])
async def service_events(
    service: str | None = None,
    level: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    start: datetime | None = None,
    end: datetime | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[ServiceEventRecord]:
    return await list_service_events(
        session,
        service=service,
        level=level,
        limit=limit,
        start=start,
        end=end,
    )
