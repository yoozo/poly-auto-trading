from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes_candles import router as candles_router
from app.api.routes_health import router as health_router
from app.api.routes_markets import router as markets_router
from app.api.routes_orderbook import router as orderbook_router
from app.api.routes_orders import router as orders_router
from app.api.routes_signals import router as signals_router
from app.api.routes_stats import router as stats_router
from app.api.routes_status import router as status_router
from app.core.lifecycle import lifespan
from app.core.config import settings


def create_app() -> FastAPI:
    app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health_router)
    app.include_router(status_router)
    app.include_router(markets_router)
    app.include_router(signals_router)
    app.include_router(candles_router)
    app.include_router(orderbook_router)
    app.include_router(orders_router)
    app.include_router(stats_router)
    return app


app = create_app()
