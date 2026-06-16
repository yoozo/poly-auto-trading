from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse

from app.api.routes_auth import router as auth_router
from app.api.routes_candles import router as candles_router
from app.api.routes_health import router as health_router
from app.api.routes_notifications import router as notifications_router
from app.api.routes_polymarket import router as polymarket_router
from app.api.routes_reports import router as reports_router
from app.api.routes_signals import router as signals_router
from app.api.routes_status import router as status_router
from app.core.auth import AUTH_EXEMPT_PATHS, auth_is_configured, request_is_authenticated
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
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def require_api_auth(request, call_next):
        path = request.url.path
        if request.method == "OPTIONS" or not path.startswith("/api") or path in AUTH_EXEMPT_PATHS:
            return await call_next(request)
        # 认证边界必须在后端统一兜住，前端隐藏页面只负责体验，不能作为安全措施。
        if not auth_is_configured():
            return JSONResponse(
                status_code=503,
                content={"detail": "authentication is not configured"},
            )
        if not request_is_authenticated(request):
            return JSONResponse(status_code=401, content={"detail": "not authenticated"})
        return await call_next(request)

    app.include_router(auth_router, prefix="/api")
    app.include_router(health_router, prefix="/api")
    app.include_router(status_router, prefix="/api")
    app.include_router(candles_router, prefix="/api")
    app.include_router(signals_router, prefix="/api")
    app.include_router(polymarket_router, prefix="/api")
    app.include_router(reports_router, prefix="/api")
    app.include_router(notifications_router, prefix="/api")
    return app


app = create_app()
