from fastapi import APIRouter

from app.services.signals import signal_service

router = APIRouter(tags=["signals"])


@router.get("/signals/latest")
async def latest_signal() -> dict:
    return signal_service.latest_signal()


@router.get("/signals/preview")
async def preview_signal() -> dict:
    return signal_service.preview_signal().model_dump(mode="json")


@router.get("/signals")
async def signals(limit: int = 20) -> list[dict]:
    return signal_service.signals(limit=limit)
