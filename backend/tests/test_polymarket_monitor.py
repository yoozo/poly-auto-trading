from datetime import datetime, timedelta, timezone

from app.services.polymarket_monitor import calculate_next_refresh_delay


def test_next_refresh_delay_uses_empty_retry_without_markets() -> None:
    now = datetime(2026, 6, 13, 5, 0, tzinfo=timezone.utc)

    delay = calculate_next_refresh_delay(
        now=now,
        next_boundary=None,
        market_count=0,
        fallback_seconds=60,
        boundary_window_seconds=3,
        empty_retry_seconds=5,
    )

    assert delay == 5


def test_next_refresh_delay_schedules_before_future_boundary() -> None:
    now = datetime(2026, 6, 13, 5, 0, tzinfo=timezone.utc)

    delay = calculate_next_refresh_delay(
        now=now,
        next_boundary=now + timedelta(seconds=20),
        market_count=4,
        fallback_seconds=60,
        boundary_window_seconds=3,
        empty_retry_seconds=5,
    )

    assert delay == 17


def test_next_refresh_delay_schedules_after_boundary_when_inside_window() -> None:
    now = datetime(2026, 6, 13, 5, 0, tzinfo=timezone.utc)

    delay = calculate_next_refresh_delay(
        now=now,
        next_boundary=now + timedelta(seconds=2),
        market_count=4,
        fallback_seconds=60,
        boundary_window_seconds=3,
        empty_retry_seconds=5,
    )

    assert delay == 5


def test_next_refresh_delay_uses_fallback_without_future_boundary() -> None:
    now = datetime(2026, 6, 13, 5, 0, tzinfo=timezone.utc)

    delay = calculate_next_refresh_delay(
        now=now,
        next_boundary=None,
        market_count=4,
        fallback_seconds=60,
        boundary_window_seconds=3,
        empty_retry_seconds=5,
    )

    assert delay == 60
