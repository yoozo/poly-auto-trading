from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.schemas.candle import Candle
from app.services import candle_backfill
from app.services.binance_archive_client import BinanceArchiveFileNotFound
from app.services.binance_client import KlinePage


def make_candle(index: int) -> Candle:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=index)
    return Candle(
        symbol="BTCUSDT",
        interval="1m",
        open_time=start,
        close_time=start + timedelta(minutes=1),
        open=100 + index,
        high=101 + index,
        low=99 + index,
        close=100 + index,
        volume=1,
        is_closed=True,
    )


def test_wave_page_starts_uses_ten_pages() -> None:
    start_ms = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    starts = candle_backfill.wave_page_starts(start_ms, end_ms=start_ms + 1_000_000_000, interval="1m")

    assert len(starts) == 10
    assert starts == [start_ms + index * 60_000_000 for index in range(10)]


def test_wave_page_starts_probes_real_binance_start_before_concurrency() -> None:
    assert candle_backfill.wave_page_starts(0, end_ms=1_000_000_000, interval="1m") == [0]


def test_align_interval_open_ms_uses_binance_boundaries() -> None:
    value = int(datetime(2017, 8, 17, 4, 1, tzinfo=timezone.utc).timestamp() * 1000)

    assert candle_backfill.align_interval_open_ms(value, "1d") == int(
        datetime(2017, 8, 17, tzinfo=timezone.utc).timestamp() * 1000
    )
    assert candle_backfill.align_interval_open_ms(value, "12h") == int(
        datetime(2017, 8, 17, tzinfo=timezone.utc).timestamp() * 1000
    )
    assert candle_backfill.align_interval_open_ms(value, "1w") == int(
        datetime(2017, 8, 14, tzinfo=timezone.utc).timestamp() * 1000
    )


def test_normalize_step_end_ms_keeps_aligned_and_clamps_partial_interval() -> None:
    aligned = int(datetime(2026, 6, 20, 17, 35, tzinfo=timezone.utc).timestamp() * 1000)
    partial = int(datetime(2026, 6, 20, 17, 40, 5, tzinfo=timezone.utc).timestamp() * 1000)

    assert candle_backfill.normalize_step_end_ms(aligned, "5m") == aligned
    assert candle_backfill.normalize_step_end_ms(partial, "5m") == aligned


def test_expected_candle_count_uses_inclusive_open_times() -> None:
    start_ms = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)

    assert candle_backfill.expected_candle_count(start_ms, start_ms, "1m") == 1
    assert candle_backfill.expected_candle_count(start_ms, start_ms + 2 * 60_000, "1m") == 3


@pytest.mark.asyncio
async def test_candle_sync_service_schedules_large_range(monkeypatch) -> None:
    calls = []

    async def fake_schedule(symbol):
        calls.append(symbol)

    monkeypatch.setattr(candle_backfill, "schedule_large_backfill", fake_schedule)
    service = candle_backfill.CandleSyncService()
    start_ms = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)

    await service.ensure_range(
        SimpleNamespace(),
        symbol="BTCUSDT",
        interval="1m",
        start_ms=start_ms,
        end_ms=start_ms + 2_500 * 60_000,
    )

    assert calls == ["BTCUSDT"]


@pytest.mark.asyncio
async def test_candle_sync_service_fetches_small_missing_ranges(monkeypatch) -> None:
    calls = {"commits": 0, "fetches": []}

    class FakeSession:
        async def commit(self):
            calls["commits"] += 1

    async def fake_missing_ranges(session, *, symbol, interval, start_ms, end_ms):
        return [(start_ms, start_ms + 2 * 60_000)]

    async def fake_unavailable_ranges(session, *, symbol, interval, start_ms, end_ms):
        return []

    async def fake_fetch_and_persist(session, *, symbol, interval, ranges):
        calls["fetches"].append({"symbol": symbol, "interval": interval, "ranges": ranges})

    async def fail_schedule(symbol):
        raise AssertionError("small range should not schedule full backfill")

    monkeypatch.setattr(candle_backfill, "missing_ranges_for_window", fake_missing_ranges)
    monkeypatch.setattr(candle_backfill, "list_candle_unavailable_ranges", fake_unavailable_ranges)
    monkeypatch.setattr(candle_backfill, "fetch_and_persist_ranges", fake_fetch_and_persist)
    monkeypatch.setattr(candle_backfill, "schedule_large_backfill", fail_schedule)
    service = candle_backfill.CandleSyncService()
    start_ms = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)

    await service.ensure_range(
        FakeSession(),
        symbol="btcusdt",
        interval="1m",
        start_ms=start_ms,
        end_ms=start_ms + 5 * 60_000,
    )

    assert calls["commits"] == 1
    assert calls["fetches"] == [
        {
            "symbol": "BTCUSDT",
            "interval": "1m",
            "ranges": [(start_ms, start_ms + 2 * 60_000)],
        }
    ]


def test_sort_intervals_for_execution_runs_light_intervals_first() -> None:
    intervals = ["1m", "5m", "1d", "4h", "1w"]

    assert candle_backfill.sort_intervals_for_execution(intervals) == ["1w", "1d", "4h", "5m", "1m"]


def test_sort_progress_for_execution_runs_1m_last() -> None:
    progress = [
        SimpleNamespace(interval="1m", start_ms=0, end_ms=0, id=1),
        SimpleNamespace(interval="1d", start_ms=0, end_ms=0, id=2),
        SimpleNamespace(interval="5m", start_ms=0, end_ms=0, id=3),
    ]

    assert [item.interval for item in candle_backfill.sort_progress_for_execution(progress)] == ["1d", "5m", "1m"]


def test_sort_progress_for_execution_runs_recent_gap_first_within_interval() -> None:
    progress = [
        SimpleNamespace(interval="5m", start_ms=1_000, end_ms=2_000, id=1),
        SimpleNamespace(interval="5m", start_ms=10_000, end_ms=11_000, id=2),
        SimpleNamespace(interval="5m", start_ms=5_000, end_ms=6_000, id=3),
    ]

    assert [item.id for item in candle_backfill.sort_progress_for_execution(progress)] == [2, 3, 1]


@pytest.mark.asyncio
async def test_start_all_reuses_active_task_status(monkeypatch) -> None:
    runner = candle_backfill.CandleBackfillRunner()
    runner._active_task_id = 123
    expected = candle_backfill.CandleBackfillStatus(state="running", task_id=123, symbol="BTCUSDT")

    async def fake_status():
        return expected

    async def fail_create_task(*args, **kwargs):
        raise AssertionError("active task should not create or resume a task")

    monkeypatch.setattr(runner, "status", fake_status)
    monkeypatch.setattr(candle_backfill, "create_task", fail_create_task)
    monkeypatch.setattr(candle_backfill, "resume_task", fail_create_task)

    assert await runner.start_all(symbol="BTCUSDT") is expected


@pytest.mark.asyncio
async def test_fetch_wave_requests_every_page_start() -> None:
    calls = []

    class FakeClient:
        async def fetch_klines_page(self, **kwargs):
            calls.append(kwargs)
            return KlinePage(candles=[make_candle(0)], next_start_ms=60_000, raw_count=1)

    results = await candle_backfill.fetch_wave(
        FakeClient(),
        symbol="BTCUSDT",
        interval="1m",
        end_ms=180_000_000,
        page_starts=[0, 60_000_000, 120_000_000],
    )

    assert sorted(results) == [0, 60_000_000, 120_000_000]
    assert [call["start_ms"] for call in calls] == [0, 60_000_000, 120_000_000]
    assert all(call["limit"] == 1000 for call in calls)


@pytest.mark.asyncio
async def test_persist_wave_stops_cursor_at_failed_page(monkeypatch) -> None:
    upserted = []

    async def fake_upsert(session, candles):
        upserted.append(candles)

    monkeypatch.setattr(candle_backfill, "upsert_candles", fake_upsert)
    first_candles = [make_candle(index) for index in range(1000)]
    second_candles = [make_candle(index) for index in range(1000, 2000)]
    progress = SimpleNamespace(
        interval="1m",
        next_start_ms=0,
        end_ms=int(second_candles[-1].open_time.timestamp() * 1000),
        inserted_count=0,
        status="running",
        finished_at=None,
    )
    task = SimpleNamespace(task_metadata={})
    first_start_ms = int(first_candles[0].open_time.timestamp() * 1000)
    second_start_ms = int(second_candles[0].open_time.timestamp() * 1000)
    first_page = KlinePage(
        candles=first_candles,
        next_start_ms=int(first_candles[-1].open_time.timestamp() * 1000) + 60_000,
        raw_count=1000,
    )
    second_page = KlinePage(
        candles=second_candles,
        next_start_ms=int(second_candles[-1].open_time.timestamp() * 1000) + 60_000,
        raw_count=1000,
    )

    failed = await candle_backfill.persist_wave(
        object(),
        task,
        progress,
        {
            first_start_ms: first_page,
            second_start_ms: "RuntimeError: rate limited",
            second_start_ms + 60_000_000: second_page,
        },
    )

    assert failed == second_start_ms
    assert progress.next_start_ms == second_start_ms
    assert progress.inserted_count == 1000
    assert progress.raw_count == 1000
    assert upserted == [first_candles]


@pytest.mark.asyncio
async def test_persist_wave_advances_by_raw_page_cursor_when_rows_are_skipped(monkeypatch) -> None:
    upserted = []

    async def fake_upsert(session, candles):
        upserted.append(candles)

    monkeypatch.setattr(candle_backfill, "upsert_candles", fake_upsert)
    candles = [make_candle(index) for index in range(999)]
    progress = SimpleNamespace(
        interval="1m",
        next_start_ms=0,
        end_ms=int(candles[-1].open_time.timestamp() * 1000),
        inserted_count=0,
        status="running",
        finished_at=None,
    )
    task = SimpleNamespace(task_metadata={})
    page = KlinePage(
        candles=candles,
        next_start_ms=int(candles[-1].open_time.timestamp() * 1000) + 120_000,
        raw_count=1000,
    )

    failed = await candle_backfill.persist_wave(object(), task, progress, {0: page})

    assert failed is None
    assert progress.next_start_ms == page.next_start_ms
    assert progress.status == "running"
    assert progress.inserted_count == 999
    assert progress.raw_count == 1000
    assert upserted == [candles]


@pytest.mark.asyncio
async def test_persist_wave_ignores_open_candles_beyond_step_end(monkeypatch) -> None:
    upserted = []

    async def fake_upsert(session, candles):
        upserted.append(candles)

    monkeypatch.setattr(candle_backfill, "upsert_candles", fake_upsert)
    closed = make_candle(0)
    open_candle = make_candle(1).model_copy(update={"is_closed": False})
    progress = SimpleNamespace(
        interval="1m",
        next_start_ms=0,
        end_ms=int(closed.open_time.timestamp() * 1000),
        inserted_count=0,
        raw_count=0,
        status="running",
        finished_at=None,
    )
    page = KlinePage(
        candles=[closed, open_candle],
        next_start_ms=int(open_candle.open_time.timestamp() * 1000) + 60_000,
        raw_count=2,
    )

    failed = await candle_backfill.persist_wave(object(), SimpleNamespace(), progress, {0: page})

    assert failed is None
    assert progress.inserted_count == 1
    assert progress.raw_count == 2
    assert upserted == [[closed]]


@pytest.mark.asyncio
async def test_persist_wave_records_empty_provider_range(monkeypatch) -> None:
    recorded = []

    async def fake_record(session, **kwargs):
        recorded.append(kwargs)

    monkeypatch.setattr(candle_backfill, "upsert_candle_unavailable_range", fake_record)
    progress = SimpleNamespace(
        interval="1m",
        next_start_ms=0,
        end_ms=60_000,
        inserted_count=0,
        raw_count=0,
        status="running",
        finished_at=None,
    )
    task = SimpleNamespace(symbol="BTCUSDT")
    page = KlinePage(candles=[], next_start_ms=None, raw_count=0)

    failed = await candle_backfill.persist_wave(object(), task, progress, {0: page})

    assert failed is None
    assert progress.status == "completed"
    assert recorded == [
        {
            "symbol": "BTCUSDT",
            "interval": "1m",
            "start_ms": 0,
            "end_ms": 60_000,
            "reason": "Binance returned no klines for this closed range",
        }
    ]


@pytest.mark.asyncio
async def test_fetch_archive_period_falls_back_to_rest_when_archive_missing() -> None:
    candle = make_candle(0)

    class FakeArchiveClient:
        async def fetch_klines_period(self, **kwargs):
            raise BinanceArchiveFileNotFound("missing")

    class FakeRestClient:
        async def fetch_klines_page(self, **kwargs):
            return KlinePage(candles=[candle], next_start_ms=60_000, raw_count=1)

    period = SimpleNamespace(start_ms=0, end_ms=60_000, path_suffix="/missing.zip")

    result = await candle_backfill.fetch_archive_period(
        FakeArchiveClient(),
        rest_client=FakeRestClient(),
        symbol="BTCUSDT",
        interval="1m",
        period=period,
    )

    assert result == {0: KlinePage(candles=[candle], next_start_ms=60_000, raw_count=1)}


def test_serialize_status_uses_step_columns_raw_counts_and_ranges() -> None:
    task = SimpleNamespace(
        id=7,
        symbol="BTCUSDT",
        status="running",
        error="",
        message="K line backfill resumed",
        started_at=None,
        finished_at=None,
        task_metadata={},
    )
    progress = [
        SimpleNamespace(
            interval="1d",
            status="running",
            next_start_ms=1000,
            end_ms=2000,
            inserted_count=10,
            raw_count=11,
            last_error="",
            started_at=None,
            finished_at=None,
        )
    ]

    status = candle_backfill.serialize_status(
        task,
        progress,
        candle_ranges={"1d": {"count": 10, "min_open_time": None, "max_open_time": None}},
    )

    assert status.progress[0].raw_count == 11
    assert status.candle_ranges["1d"]["count"] == 10


def test_merge_missing_range_windows_coalesces_nearby_ranges() -> None:
    minute = 60_000
    ranges = [
        (0, 10 * minute),
        (100 * minute, 120 * minute),
        (20_000 * minute, 20_010 * minute),
    ]

    merged = candle_backfill.merge_missing_range_windows(ranges, interval="1m")

    assert merged == [
        (0, 120 * minute),
        (20_000 * minute, 20_010 * minute),
    ]


def test_subtract_unavailable_ranges_removes_provider_empty_segments() -> None:
    minute = 60_000

    result = candle_backfill.subtract_unavailable_ranges(
        [(0, 10 * minute)],
        [(3 * minute, 5 * minute)],
        interval="1m",
    )

    assert result == [(0, 2 * minute), (6 * minute, 10 * minute)]


@pytest.mark.asyncio
async def test_history_coverage_start_uses_binance_history_start_when_empty(monkeypatch) -> None:
    async def fake_earliest(*args, **kwargs):  # noqa: ANN002, ANN003
        return None

    monkeypatch.setattr(candle_backfill, "get_earliest_candle_time", fake_earliest)

    start_ms = await candle_backfill.history_coverage_start_ms(
        object(),
        symbol="BTCUSDT",
        interval="1m",
        end_ms=int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000),
    )

    assert start_ms == candle_backfill.BINANCE_SPOT_HISTORY_START_MS


@pytest.mark.asyncio
async def test_plan_candle_missing_ranges_uses_latest_closed_interval_end(monkeypatch) -> None:
    now_ms = int(datetime(2026, 6, 20, 17, 40, 5, tzinfo=timezone.utc).timestamp() * 1000)
    expected_end_ms = int(datetime(2026, 6, 20, 17, 35, tzinfo=timezone.utc).timestamp() * 1000)

    async def fake_history_start(*args, **kwargs):  # noqa: ANN002, ANN003
        return int(datetime(2026, 6, 20, 17, tzinfo=timezone.utc).timestamp() * 1000)

    async def fake_latest(*args, **kwargs):  # noqa: ANN002, ANN003
        return None

    monkeypatch.setattr(candle_backfill, "history_coverage_start_ms", fake_history_start)
    monkeypatch.setattr(candle_backfill, "get_latest_candle", fake_latest)

    gaps = await candle_backfill.plan_candle_missing_ranges(
        object(),
        symbol="BTCUSDT",
        intervals=["5m"],
        end_ms=now_ms,
    )

    assert gaps[0].end_ms == expected_end_ms


@pytest.mark.asyncio
async def test_plan_interval_missing_ranges_returns_unified_gaps(monkeypatch) -> None:
    target_start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    local_start = target_start + timedelta(minutes=10)
    latest_time = target_start + timedelta(minutes=30)
    target_end_ms = int((target_start + timedelta(minutes=40)).timestamp() * 1000)

    async def fake_history_start(*args, **kwargs):  # noqa: ANN002, ANN003
        return int(target_start.timestamp() * 1000)

    async def fake_latest(*args, **kwargs):  # noqa: ANN002, ANN003
        return SimpleNamespace(open_time=latest_time)

    async def fake_earliest(*args, **kwargs):  # noqa: ANN002, ANN003
        return local_start

    async def fake_missing_ranges(*args, **kwargs):  # noqa: ANN002, ANN003
        return [(target_start + timedelta(minutes=20), target_start + timedelta(minutes=22))]

    async def fake_unavailable_ranges(*args, **kwargs):  # noqa: ANN002, ANN003
        return []

    monkeypatch.setattr(candle_backfill, "history_coverage_start_ms", fake_history_start)
    monkeypatch.setattr(candle_backfill, "get_latest_candle", fake_latest)
    monkeypatch.setattr(candle_backfill, "get_earliest_candle_time", fake_earliest)
    monkeypatch.setattr(candle_backfill, "list_candle_missing_ranges", fake_missing_ranges)
    monkeypatch.setattr(candle_backfill, "list_candle_unavailable_ranges", fake_unavailable_ranges)

    gaps = await candle_backfill.plan_interval_missing_ranges(object(), symbol="BTCUSDT", interval="1m", end_ms=target_end_ms)

    assert [gap.step_key for gap in gaps] == [f"1m:{int(target_start.timestamp() * 1000)}"]
    assert gaps[0].start_ms == int(target_start.timestamp() * 1000)
    assert gaps[0].end_ms == target_end_ms


@pytest.mark.asyncio
async def test_plan_interval_missing_ranges_splits_recent_window_first_for_empty_database(monkeypatch) -> None:
    target_start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    target_end = target_start + timedelta(minutes=9)
    target_end_ms = int(target_end.timestamp() * 1000)

    async def fake_history_start(*args, **kwargs):  # noqa: ANN002, ANN003
        return int(target_start.timestamp() * 1000)

    async def fake_latest(*args, **kwargs):  # noqa: ANN002, ANN003
        return None

    monkeypatch.setattr(candle_backfill.settings, "candle_history_limit", 3)
    monkeypatch.setattr(candle_backfill, "history_coverage_start_ms", fake_history_start)
    monkeypatch.setattr(candle_backfill, "get_latest_candle", fake_latest)

    gaps = await candle_backfill.plan_interval_missing_ranges(
        object(),
        symbol="BTCUSDT",
        interval="1m",
        end_ms=target_end_ms,
    )

    assert [(gap.start_ms, gap.end_ms) for gap in gaps] == [
        (int((target_start + timedelta(minutes=7)).timestamp() * 1000), target_end_ms),
        (
            int(target_start.timestamp() * 1000),
            int((target_start + timedelta(minutes=6)).timestamp() * 1000),
        ),
    ]


@pytest.mark.asyncio
async def test_plan_interval_missing_ranges_returns_recent_gap_first(monkeypatch) -> None:
    target_start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    latest_time = target_start + timedelta(minutes=30)
    target_end_ms = int((target_start + timedelta(minutes=40)).timestamp() * 1000)

    async def fake_history_start(*args, **kwargs):  # noqa: ANN002, ANN003
        return int(target_start.timestamp() * 1000)

    async def fake_latest(*args, **kwargs):  # noqa: ANN002, ANN003
        return SimpleNamespace(open_time=latest_time)

    async def fake_earliest(*args, **kwargs):  # noqa: ANN002, ANN003
        return target_start

    async def fake_missing_ranges(*args, **kwargs):  # noqa: ANN002, ANN003
        return [(target_start + timedelta(minutes=3), target_start + timedelta(minutes=5))]

    async def fake_unavailable_ranges(*args, **kwargs):  # noqa: ANN002, ANN003
        return []

    monkeypatch.setattr(candle_backfill, "MISSING_RANGE_MERGE_PAGES", 0)
    monkeypatch.setattr(candle_backfill, "history_coverage_start_ms", fake_history_start)
    monkeypatch.setattr(candle_backfill, "get_latest_candle", fake_latest)
    monkeypatch.setattr(candle_backfill, "get_earliest_candle_time", fake_earliest)
    monkeypatch.setattr(candle_backfill, "list_candle_missing_ranges", fake_missing_ranges)
    monkeypatch.setattr(candle_backfill, "list_candle_unavailable_ranges", fake_unavailable_ranges)

    gaps = await candle_backfill.plan_interval_missing_ranges(
        object(),
        symbol="BTCUSDT",
        interval="1m",
        end_ms=target_end_ms,
    )

    assert [(gap.start_ms, gap.end_ms) for gap in gaps] == [
        (int((target_start + timedelta(minutes=31)).timestamp() * 1000), target_end_ms),
        (
            int((target_start + timedelta(minutes=3)).timestamp() * 1000),
            int((target_start + timedelta(minutes=5)).timestamp() * 1000),
        ),
    ]


@pytest.mark.asyncio
async def test_plan_interval_missing_ranges_skips_known_unavailable_ranges(monkeypatch) -> None:
    target_start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    latest_time = target_start + timedelta(minutes=10)
    target_end_ms = int(latest_time.timestamp() * 1000)

    async def fake_history_start(*args, **kwargs):  # noqa: ANN002, ANN003
        return int(target_start.timestamp() * 1000)

    async def fake_latest(*args, **kwargs):  # noqa: ANN002, ANN003
        return SimpleNamespace(open_time=latest_time)

    async def fake_earliest(*args, **kwargs):  # noqa: ANN002, ANN003
        return target_start

    async def fake_missing_ranges(*args, **kwargs):  # noqa: ANN002, ANN003
        return [(target_start + timedelta(minutes=3), target_start + timedelta(minutes=5))]

    async def fake_unavailable_ranges(*args, **kwargs):  # noqa: ANN002, ANN003
        return [
            (
                int((target_start + timedelta(minutes=3)).timestamp() * 1000),
                int((target_start + timedelta(minutes=5)).timestamp() * 1000),
            )
        ]

    monkeypatch.setattr(candle_backfill, "history_coverage_start_ms", fake_history_start)
    monkeypatch.setattr(candle_backfill, "get_latest_candle", fake_latest)
    monkeypatch.setattr(candle_backfill, "get_earliest_candle_time", fake_earliest)
    monkeypatch.setattr(candle_backfill, "list_candle_missing_ranges", fake_missing_ranges)
    monkeypatch.setattr(candle_backfill, "list_candle_unavailable_ranges", fake_unavailable_ranges)

    gaps = await candle_backfill.plan_interval_missing_ranges(
        object(),
        symbol="BTCUSDT",
        interval="1m",
        end_ms=target_end_ms,
    )

    assert gaps == []


@pytest.mark.asyncio
async def test_resume_task_rebuilds_unstarted_pending_steps(monkeypatch) -> None:
    stale = SimpleNamespace(
        step_key="1m:old",
        interval="1m",
        status="pending",
        inserted_count=0,
        raw_count=0,
        end_ms=1_000,
    )
    completed = SimpleNamespace(
        step_key="1m:done",
        interval="1m",
        status="completed",
        inserted_count=10,
        raw_count=10,
        end_ms=1_000,
    )
    added = []
    deleted = []

    class FakeSession:
        async def delete(self, item):
            deleted.append(item)

        async def flush(self):
            pass

        def add(self, item):
            added.append(item)

        async def commit(self):
            pass

    async def fake_list_task_progress(session, task_id):
        if deleted:
            return [completed]
        return [stale, completed]

    async def fake_plan(*args, **kwargs):  # noqa: ANN002, ANN003
        return [candle_backfill.CandleMissingRange(interval="1m", start_ms=2_000, end_ms=3_000)]

    monkeypatch.setattr(candle_backfill, "list_task_progress", fake_list_task_progress)
    monkeypatch.setattr(candle_backfill, "plan_candle_missing_ranges", fake_plan)

    task = SimpleNamespace(id=7, symbol="BTCUSDT", status="error", error="x", message="", finished_at=None)

    await candle_backfill.resume_task(FakeSession(), task, intervals=["1m"])

    assert deleted == [stale]
    assert len(added) == 1
    assert added[0].step_key == "1m:2000"
