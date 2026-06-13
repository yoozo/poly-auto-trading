import logging

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.encoders import jsonable_encoder

from app.schemas.polymarket import PolymarketUpDownMarket
from app.services.polymarket_client import PolymarketClient, PolymarketInputError
from app.services.polymarket_market_store import polymarket_up_down_store
from app.services.polymarket_ws_hub import polymarket_ws_hub

router = APIRouter(tags=["polymarket"])
logger = logging.getLogger(__name__)


@router.get("/polymarket/btc-up-down", response_model=list[PolymarketUpDownMarket])
async def btc_up_down_markets(
    interval: str = Query("5m", pattern="^(5m|15m|1h|4h)$"),
    limit: int = Query(6, ge=1, le=20),
    include_recent_closed: bool = Query(True),
) -> list[PolymarketUpDownMarket]:
    try:
        cached = await polymarket_up_down_store.list_markets(interval, limit=limit)
        if cached:
            return cached
        # API 层只暴露项目需要的 BTC up/down 视图；实时盘口由后台 marketChannel 缓存覆盖。
        markets = await PolymarketClient().fetch_btc_up_down_markets(
            interval=interval,
            limit=limit,
            include_recent_closed=include_recent_closed,
        )
        await polymarket_up_down_store.replace_markets(interval, markets)
        return await polymarket_up_down_store.list_markets(interval, limit=limit)
    except PolymarketInputError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.warning("Failed to fetch Polymarket BTC up/down markets", exc_info=exc)
        raise HTTPException(status_code=502, detail=f"Polymarket 数据获取失败: {exc}") from exc


@router.get("/polymarket/btc-up-down/{market_id}", response_model=PolymarketUpDownMarket)
async def btc_up_down_market(market_id: str) -> PolymarketUpDownMarket:
    market = await polymarket_up_down_store.get_market(market_id)
    if market is None:
        raise HTTPException(status_code=404, detail="Polymarket market not found")
    return market


@router.websocket("/ws/polymarket/btc-up-down")
async def btc_up_down_websocket(
    websocket: WebSocket,
    interval: str = Query("5m", pattern="^(5m|15m|1h|4h)$"),
) -> None:
    await polymarket_ws_hub.connect(websocket, interval)
    try:
        markets = await polymarket_up_down_store.list_markets(interval, limit=12)
        await websocket.send_json(
            {
                "type": "polymarket.btc_up_down.snapshot",
                "interval": interval,
                "markets": jsonable_encoder(markets),
            }
        )
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await polymarket_ws_hub.disconnect(websocket, interval)
