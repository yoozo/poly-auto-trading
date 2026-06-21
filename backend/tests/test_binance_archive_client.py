from datetime import datetime, timezone
from io import BytesIO
import zipfile

from app.services.binance_archive_client import (
    archive_candidate_is_more_efficient,
    normalize_archive_timestamp,
    ArchivePeriod,
    BinanceArchiveClient,
    parse_archive_klines_page,
    parse_archive_zip_rows,
    select_archive_period,
)


def make_zip(content: str) -> bytes:
    output = BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("BTCUSDT-1m-2026-01-01.csv", content)
    return output.getvalue()


def test_parse_archive_zip_rows_skips_header_and_normalizes_microseconds() -> None:
    content = "\n".join(
        [
            "open_time,open,high,low,close,volume,close_time,quote_volume,trades,taker_base,taker_quote,ignore",
            "1735689600000000,1,2,0.5,1.5,10,1735689659999999,0,1,0,0,0",
        ]
    )

    rows = parse_archive_zip_rows(make_zip(content))

    assert rows[0][0] == "1735689600000"


def test_iter_klines_period_batches_streams_zip_rows(tmp_path) -> None:
    content = "\n".join(
        [
            "open_time,open,high,low,close,volume,close_time,quote_volume,trades,taker_base,taker_quote,ignore",
            "1735689600000,1,2,0.5,1.5,10,1735689659999,0,1,0,0,0",
            "1735689660000,1.5,2.5,1.0,2.0,11,1735689719999,0,1,0,0,0",
            "1735689720000,2.0,3.0,1.5,2.5,12,1735689779999,0,1,0,0,0",
        ]
    )
    file_path = tmp_path / "BTCUSDT-1m-2025-01.zip"
    file_path.write_bytes(make_zip(content))
    period = ArchivePeriod(
        kind="monthly",
        path_suffix="/data/spot/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-2025-01.zip",
        start_ms=1735689600000,
        end_ms=1735689720000,
        next_start_ms=1735689780000,
    )

    batches = list(
        BinanceArchiveClient().iter_klines_period_batches(
            str(file_path),
            symbol="BTCUSDT",
            interval="1m",
            period=period,
            batch_size=2,
        )
    )

    assert [len(batch.candles) for batch in batches] == [2, 1]
    assert sum(batch.raw_count for batch in batches) == 3
    assert sum(batch.skipped_invalid_count for batch in batches) == 0


def test_normalize_archive_timestamp_keeps_milliseconds() -> None:
    assert normalize_archive_timestamp(1735689600000) == 1735689600000


def test_parse_archive_klines_page_skips_invalid_rows() -> None:
    rows = [
        ["1518170340000", "7789.9", "8230.46", "7789.9", "8230.46", "148.4", "1518170399999"],
        ["1518170354789", "7789.9", "8230.46", "7789.9", "8230.46", "148.4", "1518170414788"],
    ]

    page, skipped = parse_archive_klines_page("BTCUSDT", "1m", rows)

    assert skipped == 1
    assert page.raw_count == 2
    assert len(page.candles) == 1
    assert int(page.candles[0].open_time.timestamp() * 1000) == 1518170340000


def test_select_archive_period_prefers_monthly_for_full_old_month() -> None:
    start_ms = int(datetime(2024, 5, 1, tzinfo=timezone.utc).timestamp() * 1000)
    end_ms = int(datetime(2024, 5, 31, 23, 59, tzinfo=timezone.utc).timestamp() * 1000)

    period = select_archive_period(
        symbol="BTCUSDT",
        interval="1m",
        start_ms=start_ms,
        end_ms=end_ms,
        now=datetime(2026, 6, 21, tzinfo=timezone.utc),
    )

    assert period is not None
    assert period.kind == "monthly"
    assert period.path_suffix.endswith("/BTCUSDT-1m-2024-05.zip")


def test_select_archive_period_uses_monthly_for_mid_month_long_range() -> None:
    start_ms = int(datetime(2024, 5, 2, 3, 4, tzinfo=timezone.utc).timestamp() * 1000)
    end_ms = int(datetime(2024, 5, 31, 23, 59, tzinfo=timezone.utc).timestamp() * 1000)

    period = select_archive_period(
        symbol="BTCUSDT",
        interval="1m",
        start_ms=start_ms,
        end_ms=end_ms,
        now=datetime(2026, 6, 21, tzinfo=timezone.utc),
    )

    assert period is not None
    assert period.kind == "monthly"
    assert period.start_ms == start_ms
    assert period.path_suffix.endswith("/BTCUSDT-1m-2024-05.zip")


def test_select_archive_period_uses_daily_for_large_partial_day() -> None:
    start_ms = int(datetime(2024, 5, 2, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
    end_ms = int(datetime(2024, 5, 2, 20, 0, tzinfo=timezone.utc).timestamp() * 1000)

    period = select_archive_period(
        symbol="BTCUSDT",
        interval="1m",
        start_ms=start_ms,
        end_ms=end_ms,
        now=datetime(2026, 6, 21, tzinfo=timezone.utc),
    )

    assert period is not None
    assert period.kind == "daily"
    assert period.start_ms == start_ms
    assert period.end_ms == end_ms
    assert period.path_suffix.endswith("/BTCUSDT-1m-2024-05-02.zip")


def test_select_archive_period_skips_archive_when_rest_page_covers_more() -> None:
    start_ms = int(datetime(2024, 5, 1, tzinfo=timezone.utc).timestamp() * 1000)
    end_ms = int(datetime(2024, 5, 31, tzinfo=timezone.utc).timestamp() * 1000)

    period = select_archive_period(
        symbol="BTCUSDT",
        interval="1d",
        start_ms=start_ms,
        end_ms=end_ms,
        now=datetime(2026, 6, 21, tzinfo=timezone.utc),
    )

    assert period is None


def test_select_archive_period_skips_short_daily_archive_window() -> None:
    start_ms = int(datetime(2024, 5, 2, 3, 4, tzinfo=timezone.utc).timestamp() * 1000)
    end_ms = int(datetime(2024, 5, 2, 8, 9, tzinfo=timezone.utc).timestamp() * 1000)

    period = select_archive_period(
        symbol="BTCUSDT",
        interval="1m",
        start_ms=start_ms,
        end_ms=end_ms,
        now=datetime(2026, 6, 21, tzinfo=timezone.utc),
    )

    assert period is None


def test_archive_candidate_is_more_efficient_requires_more_than_rest_limit() -> None:
    assert not archive_candidate_is_more_efficient(start_ms=0, end_ms=999 * 60_000, interval_ms=60_000)
    assert archive_candidate_is_more_efficient(start_ms=0, end_ms=1000 * 60_000, interval_ms=60_000)
