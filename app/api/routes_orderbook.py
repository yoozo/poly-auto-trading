from fastapi import APIRouter

from app.schemas import OrderbookSnapshot
from app.services.state_store import state_store

router = APIRouter(tags=["orderbook"])


@router.get("/orderbook/latest")
async def latest_orderbook(token_id: str | None = None) -> dict:
    snapshot = state_store.get_orderbook(token_id=token_id)
    if snapshot is None:
        return OrderbookSnapshot(token_id=token_id or "").model_dump(mode="json")
    return snapshot.model_dump(mode="json")
