from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.schemas.signal import SignalRecord
from app.services.signal_analysis import list_signals

router = APIRouter(prefix="/signals", tags=["signals"])


@router.get("", response_model=list[SignalRecord])
async def signals(
    target_type: str | None = Query(None),
    target_key: str | None = Query(None),
    signal_key: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
) -> list[SignalRecord]:
    return await list_signals(
        session,
        target_type=target_type,
        target_key=target_key,
        signal_key=signal_key,
        limit=limit,
    )
