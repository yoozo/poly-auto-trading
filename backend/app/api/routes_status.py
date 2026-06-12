from fastapi import APIRouter

from app.services.service_health import ServiceHealth, service_health_store

router = APIRouter(tags=["status"])


@router.get("/status/services", response_model=list[ServiceHealth])
async def services() -> list[ServiceHealth]:
    return service_health_store.list()

