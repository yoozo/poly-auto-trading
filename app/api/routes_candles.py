from fastapi import APIRouter, Query

from app.services.state_store import state_store

router = APIRouter(tags=["candles"])


@router.get("/candles")
async def candles(
    symbol: str = "BTCUSDT",
    interval: str = Query("1m", pattern="^(1m|5m|15m|30m|1h|4h)$"),
    limit: int = Query(200, ge=1, le=1000),
) -> list[dict]:
    candles = state_store.get_candles(symbol=symbol, interval=interval, limit=limit)
    return [candle.model_dump(mode="json") for candle in candles]


@router.get("/indicators/latest")
async def indicators(symbol: str = "BTCUSDT") -> dict:
    snapshot = state_store.get_indicator_snapshot(symbol)
    if snapshot is None:
        return {"symbol": symbol.upper(), "updated_at": None, "intervals": {}}
    return snapshot.model_dump(mode="json")
