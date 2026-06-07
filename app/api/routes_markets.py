from fastapi import APIRouter, HTTPException

from app.services.state_store import state_store
from app.services.polymarket_market import polymarket_market_service

router = APIRouter(tags=["markets"])


@router.get("/markets")
async def markets() -> list[dict]:
    return [market.model_dump(mode="json") for market in state_store.get_markets()]


@router.get("/markets/result")
async def market_result(event_slug: str) -> dict:
    result = await polymarket_market_service.fetch_market_result(event_slug)
    if result is None:
        raise HTTPException(status_code=404, detail="Market result not found")
    return result.model_dump(mode="json")
