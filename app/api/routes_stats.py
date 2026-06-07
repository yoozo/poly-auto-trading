from fastapi import APIRouter

from app.services.mock_data import get_stats_summary

router = APIRouter(tags=["stats"])


@router.get("/stats/summary")
async def stats_summary() -> dict:
    return get_stats_summary()

