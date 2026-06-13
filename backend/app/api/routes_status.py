import logging

from fastapi import APIRouter
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.services.notifications import refresh_telegram_service_health
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
