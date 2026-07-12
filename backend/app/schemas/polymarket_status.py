from __future__ import annotations

from pydantic import BaseModel, Field


class PolymarketIncident(BaseModel):
    id: str
    name: str
    status: str
    impact: str
    started_at: str | None = None
    updated_at: str | None = None
    url: str | None = None


class PolymarketStatusResponse(BaseModel):
    healthy: bool
    component_name: str | None = None
    component_status: str | None = None
    summary_page_status: str | None = None
    active_incidents: list[PolymarketIncident] = Field(default_factory=list)
    error: str | None = None
