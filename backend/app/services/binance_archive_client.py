from __future__ import annotations

import csv
import os
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO, TextIOWrapper
import logging
import zipfile

import httpx

from app.core.config import settings
from app.schemas.candle import Interval
from app.services.binance_client import BinanceClient, KlinePage
from app.services.candle_intervals import CANDLE_INTERVAL_MS, kline_open_ms, validate_aligned_open_time
from app.services.external_http import with_retry
from app.services.service_health import service_health_store

DAY_MS = 24 * 60 * 60 * 1000
MICROSECOND_TIMESTAMP_THRESHOLD = 10_000_000_000_000
REST_KLINE_LIMIT = 1000
ARCHIVE_PARSE_BATCH_SIZE = 3000
logger = logging.getLogger(__name__)


class BinanceArchiveFileNotFound(RuntimeError):
    pass


@dataclass(frozen=True)
class ArchivePeriod:
    kind: str
    path_suffix: str
    start_ms: int
    end_ms: int
    next_start_ms: int


@dataclass(frozen=True)
class ArchiveKlineBatch:
    candles: list
    raw_count: int
    skipped_invalid_count: int


class BinanceArchiveClient:
    """Binance public data archive client：用于历史区间批量 zip 下载，REST 只做兜底。"""

    def __init__(self, base_url: str | None = None, timeout: float = 20.0) -> None:
        self._base_url = (base_url or settings.binance_archive_base_url).rstrip("/")
        self._timeout = timeout

    async def fetch_klines_period(
        self,
        *,
        symbol: str,
        interval: Interval,
        period: ArchivePeriod,
    ) -> KlinePage:
        url = f"{self._base_url}{period.path_suffix}"
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await with_retry(lambda: client.get(url))
        if response.status_code == 404:
            raise BinanceArchiveFileNotFound(url)
        response.raise_for_status()
        rows = parse_archive_zip_rows(response.content)
        page, skipped_invalid_count = parse_archive_klines_page(symbol.upper(), interval, rows)
        candles = [
            candle
            for candle in page.candles
            if period.start_ms <= int(candle.open_time.timestamp() * 1000) <= period.end_ms
        ]
        service_health_store.set(
            "binance_archive",
            "running",
            metadata={
                "endpoint": self._base_url,
                "symbol": symbol.upper(),
                "interval": interval,
                "period": period.kind,
                "path": period.path_suffix,
                "raw_count": len(rows),
                "selected_count": len(candles),
                "skipped_invalid_count": skipped_invalid_count,
            },
        )
        if skipped_invalid_count:
            logger.warning(
                "Skipped invalid Binance archive klines",
                extra={
                    "symbol": symbol.upper(),
                    "interval": interval,
                    "period": period.kind,
                    "path": period.path_suffix,
                    "skipped_invalid_count": skipped_invalid_count,
                },
            )
        return KlinePage(candles=candles, next_start_ms=period.next_start_ms, raw_count=len(candles))

    async def download_klines_period_file(
        self,
        *,
        symbol: str,
        interval: Interval,
        period: ArchivePeriod,
    ) -> str:
        url = f"{self._base_url}{period.path_suffix}"
        fd, path = tempfile.mkstemp(prefix="binance-archive-", suffix=".zip")
        os.close(fd)
        try:
            async def download_once() -> None:
                async with client.stream("GET", url) as response:
                    if response.status_code == 404:
                        raise BinanceArchiveFileNotFound(url)
                    response.raise_for_status()
                    with open(path, "wb") as output:
                        async for chunk in response.aiter_bytes():
                            output.write(chunk)

            async with httpx.AsyncClient(timeout=self._timeout) as client:
                await with_retry(download_once)
            service_health_store.set(
                "binance_archive",
                "running",
                metadata={
                    "endpoint": self._base_url,
                    "symbol": symbol.upper(),
                    "interval": interval,
                    "period": period.kind,
                    "path": period.path_suffix,
                    "downloaded_bytes": os.path.getsize(path),
                },
            )
            return path
        except Exception:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
            raise

    def iter_klines_period_batches(
        self,
        file_path: str,
        *,
        symbol: str,
        interval: Interval,
        period: ArchivePeriod,
        batch_size: int = ARCHIVE_PARSE_BATCH_SIZE,
    ) -> Iterator[ArchiveKlineBatch]:
        batch: list = []
        raw_count = 0
        skipped_invalid_count = 0
        for row in iter_archive_zip_rows(file_path):
            raw_count += 1
            try:
                open_ms = kline_open_ms(row)
                if open_ms is None or open_ms < period.start_ms or open_ms > period.end_ms:
                    skipped_invalid_count += 1
                    continue
                validate_aligned_open_time(open_ms, interval)
                batch.append(BinanceClient._parse_kline(symbol.upper(), interval, row))
            except ValueError:
                skipped_invalid_count += 1
                continue
            if len(batch) >= batch_size:
                yield ArchiveKlineBatch(candles=batch, raw_count=raw_count, skipped_invalid_count=skipped_invalid_count)
                batch = []
                raw_count = 0
                skipped_invalid_count = 0
        if batch or raw_count or skipped_invalid_count:
            yield ArchiveKlineBatch(candles=batch, raw_count=raw_count, skipped_invalid_count=skipped_invalid_count)


def select_archive_period(
    *,
    symbol: str,
    interval: Interval,
    start_ms: int,
    end_ms: int,
    now: datetime | None = None,
) -> ArchivePeriod | None:
    if start_ms > end_ms:
        return None
    current = now or datetime.now(timezone.utc)
    current_month_start = datetime(current.year, current.month, 1, tzinfo=timezone.utc)
    current_day_start_ms = day_start_ms(int(current.timestamp() * 1000))
    interval_ms = CANDLE_INTERVAL_MS[interval]

    month_start = month_start_datetime(start_ms)
    next_month_start = next_month(month_start)
    next_month_start_ms = to_ms(next_month_start)
    month_end_ms = next_month_start_ms - interval_ms
    if (
        end_ms >= month_end_ms
        and next_month_start <= current_month_start
        and archive_candidate_is_more_efficient(
            start_ms=start_ms,
            end_ms=month_end_ms,
            interval_ms=interval_ms,
        )
    ):
        path = (
            f"/data/spot/monthly/klines/{symbol.upper()}/{interval}/"
            f"{symbol.upper()}-{interval}-{month_start:%Y-%m}.zip"
        )
        return ArchivePeriod(
            kind="monthly",
            path_suffix=path,
            start_ms=start_ms,
            end_ms=month_end_ms,
            next_start_ms=next_month_start_ms,
        )

    day_start = day_start_datetime(start_ms)
    day_start = datetime(day_start.year, day_start.month, day_start.day, tzinfo=timezone.utc)
    day_start_value = to_ms(day_start)
    if interval_ms <= DAY_MS and day_start_value < current_day_start_ms:
        day_next_ms = day_start_value + DAY_MS
        daily_end_ms = min(end_ms, day_next_ms - interval_ms)
        if not archive_candidate_is_more_efficient(
            start_ms=start_ms,
            end_ms=daily_end_ms,
            interval_ms=interval_ms,
        ):
            return None
        path = (
            f"/data/spot/daily/klines/{symbol.upper()}/{interval}/"
            f"{symbol.upper()}-{interval}-{day_start:%Y-%m-%d}.zip"
        )
        return ArchivePeriod(
            kind="daily",
            path_suffix=path,
            start_ms=start_ms,
            end_ms=daily_end_ms,
            next_start_ms=min(day_next_ms, end_ms + interval_ms),
        )
    return None


def archive_candidate_is_more_efficient(*, start_ms: int, end_ms: int, interval_ms: int) -> bool:
    if start_ms > end_ms:
        return False
    # archive 是按整日/整月文件推进；长周期一个文件只有几十到几百根时，REST 单页 1000 根反而覆盖更远。
    candle_count = ((end_ms - start_ms) // interval_ms) + 1
    return candle_count > REST_KLINE_LIMIT


def parse_archive_zip_rows(content: bytes) -> list[list[str]]:
    with zipfile.ZipFile(BytesIO(content)) as archive:
        csv_name = next((name for name in archive.namelist() if name.endswith(".csv")), archive.namelist()[0])
        with archive.open(csv_name) as raw_file:
            reader = csv.reader(TextIOWrapper(raw_file, encoding="utf-8"))
            rows: list[list[str]] = []
            for row in reader:
                if not row or row[0].lower().startswith("open"):
                    continue
                normalized = list(row)
                normalized[0] = str(normalize_archive_timestamp(int(normalized[0])))
                rows.append(normalized)
            return rows


def iter_archive_zip_rows(file_path: str) -> Iterator[list[str]]:
    with zipfile.ZipFile(file_path) as archive:
        csv_name = next((name for name in archive.namelist() if name.endswith(".csv")), archive.namelist()[0])
        with archive.open(csv_name) as raw_file:
            reader = csv.reader(TextIOWrapper(raw_file, encoding="utf-8"))
            for row in reader:
                if not row or row[0].lower().startswith("open"):
                    continue
                normalized = list(row)
                normalized[0] = str(normalize_archive_timestamp(int(normalized[0])))
                yield normalized


def parse_archive_klines_page(symbol: str, interval: Interval, rows: list[list[str]]) -> tuple[KlinePage, int]:
    candles = []
    next_start_ms: int | None = None
    skipped_invalid_count = 0
    for row in rows:
        try:
            open_ms = kline_open_ms(row)
            if open_ms is None:
                skipped_invalid_count += 1
                continue
            validate_aligned_open_time(open_ms, interval)
            candle = BinanceClient._parse_kline(symbol, interval, row)
        except ValueError:
            skipped_invalid_count += 1
            continue
        candles.append(candle)
        next_start_ms = int(candle.open_time.timestamp() * 1000) + CANDLE_INTERVAL_MS[interval]
    return KlinePage(candles=candles, next_start_ms=next_start_ms, raw_count=len(rows)), skipped_invalid_count


def normalize_archive_timestamp(value: int) -> int:
    if value >= MICROSECOND_TIMESTAMP_THRESHOLD:
        return value // 1000
    return value


def day_start_ms(value: int) -> int:
    return (value // DAY_MS) * DAY_MS


def day_start_datetime(value: int) -> datetime:
    return datetime.fromtimestamp(day_start_ms(value) / 1000, tz=timezone.utc)


def month_start_datetime(value: int) -> datetime:
    current = datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    return datetime(current.year, current.month, 1, tzinfo=timezone.utc)


def next_month(value: datetime) -> datetime:
    if value.month == 12:
        return datetime(value.year + 1, 1, 1, tzinfo=timezone.utc)
    return datetime(value.year, value.month + 1, 1, tzinfo=timezone.utc)


def to_ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)
