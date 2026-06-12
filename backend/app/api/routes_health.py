from datetime import datetime, timezone

from fastapi import APIRouter
from sqlalchemy import text

from app.db.session import AsyncSessionLocal
from app.services.service_health import service_health_store

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict:
    db = await check_database()
    status = "ok" if db["ok"] else "degraded"
    return {
        "status": status,
        "time": datetime.now(timezone.utc).isoformat(),
        "checks": {
            "api": {"ok": True},
            "database": db,
        },
    }


async def check_database() -> dict:
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("select 1"))
        service_health_store.set("database", "running")
        return {"ok": True}
    except Exception as exc:
        error = exc.__class__.__name__
        service_health_store.set("database", "error", last_error=error)
        return {"ok": False, "error": error}
