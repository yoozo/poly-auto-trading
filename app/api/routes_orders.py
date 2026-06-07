from fastapi import APIRouter

from app.services.mock_data import get_notifications, get_orders

router = APIRouter(tags=["orders"])


@router.get("/orders")
async def orders() -> list[dict]:
    return get_orders()


@router.get("/notifications")
async def notifications() -> list[dict]:
    return get_notifications()

