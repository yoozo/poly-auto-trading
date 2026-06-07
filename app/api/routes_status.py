from fastapi import APIRouter

from app.core.config import settings
from app.services.state_store import state_store

router = APIRouter(tags=["status"])


@router.get("/status")
async def status() -> dict:
    runtime = state_store.get_runtime_status()
    payload = {
        "ws": {
            "binance_rest": runtime.services["binance_rest"].state,
            "binance_ws": runtime.services["binance_ws"].state,
            "polymarket_market_refresh": runtime.services["polymarket_market_refresh"].state,
            "polymarket_market_ws": runtime.services["polymarket_market_ws"].state,
        },
        "scheduler": runtime.scheduler,
        "tracked_markets": runtime.tracked_markets,
        "last_error": runtime.last_error,
        "updated_at": runtime.updated_at.isoformat(),
    }
    payload["config"] = {
        "symbol": settings.binance_symbol,
        "dry_run": settings.dry_run,
        "trading_enabled": settings.trading_enabled,
        "max_order_usdc": settings.max_order_usdc,
        "max_daily_loss_usdc": settings.max_daily_loss_usdc,
    }
    return payload
