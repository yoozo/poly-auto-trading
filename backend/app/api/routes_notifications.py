from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.schemas.notification import (
    NotificationDelivery,
    TelegramStatus,
    TelegramTestResponse,
    UpdateTelegramStatusRequest,
)
from app.services.notifications import (
    get_telegram_status,
    list_notification_deliveries,
    send_test_message,
    set_telegram_enabled,
)

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("/telegram/status", response_model=TelegramStatus)
async def telegram_status(session: AsyncSession = Depends(get_session)) -> TelegramStatus:
    return await get_telegram_status(session)


@router.patch("/telegram/status", response_model=TelegramStatus)
async def patch_telegram_status(
    payload: UpdateTelegramStatusRequest,
    session: AsyncSession = Depends(get_session),
) -> TelegramStatus:
    await set_telegram_enabled(session, payload.enabled)
    return await get_telegram_status(session)


@router.post("/telegram/test", response_model=TelegramTestResponse)
async def telegram_test(session: AsyncSession = Depends(get_session)) -> TelegramTestResponse:
    try:
        await send_test_message(session)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return TelegramTestResponse(ok=True, message="Telegram test message sent")


@router.get("/deliveries", response_model=list[NotificationDelivery])
async def notification_deliveries(
    target_type: str | None = Query(None),
    target_key: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
) -> list[NotificationDelivery]:
    return await list_notification_deliveries(
        session,
        target_type=target_type,
        target_key=target_key,
        limit=limit,
    )
