from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes_candles import router as candles_router
from app.api.routes_health import router as health_router
from app.api.routes_reports import router as reports_router
from app.api.routes_status import router as status_router
from app.core.config import settings
from app.core.lifecycle import lifespan
from app.core.logging import configure_logging


def create_app(enable_lifespan: bool = True) -> FastAPI:
    configure_logging()
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        lifespan=lifespan if enable_lifespan else None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health_router, prefix="/api")
    app.include_router(status_router, prefix="/api")
    app.include_router(candles_router, prefix="/api")
    app.include_router(reports_router, prefix="/api")
    return app


app = create_app()
