from datetime import datetime, timezone

import pytest

from app.services import maintenance


@pytest.mark.asyncio
async def test_cleanup_diagnostic_data_only_deletes_diagnostics(monkeypatch) -> None:
    calls = {}

    async def fake_delete_service_events_before(session, before):
        calls["events_before"] = before
        return 4

    async def fake_delete_finished_tasks_before(session, before):
        calls["tasks_before"] = before
        return 2

    monkeypatch.setattr(maintenance, "delete_service_events_before", fake_delete_service_events_before)
    monkeypatch.setattr(maintenance, "delete_finished_tasks_before", fake_delete_finished_tasks_before)

    now = datetime(2026, 6, 13, tzinfo=timezone.utc)
    result = await maintenance.cleanup_diagnostic_data(object(), now=now)

    assert result.service_events_deleted == 4
    assert result.analysis_tasks_deleted == 2
    assert calls["events_before"].date().isoformat() == "2026-05-14"
    assert calls["tasks_before"].date().isoformat() == "2026-05-30"
