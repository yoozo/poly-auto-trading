from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import logging
import re
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from app.core.config import settings
from app.services.service_health import service_health_store

logger = logging.getLogger(__name__)

WALLET_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
ACTIVITY_PAGE_SIZE = 500
ACTIVITY_PARALLEL_REQUESTS = 15
# The public docs still show a wider offset range, but the live data API rejects
# historical activity windows at the 3000 boundary.
ACTIVITY_MAX_OFFSET = 3000


@dataclass(frozen=True)
class ResolvedPolymarketAccount:
    input: str
    normalized_user: str
    proxy_wallet: str
    profile: dict[str, Any]


@dataclass(frozen=True)
class NormalizedActivity:
    id: str
    proxy_wallet: str
    timestamp: datetime
    type: str
    condition_id: str | None
    slug: str | None
    event_slug: str | None
    title: str | None
    side: str | None
    outcome: str | None
    asset: str | None
    price: Decimal | None
    size: Decimal | None
    usdc_size: Decimal | None
    transaction_hash: str | None
    raw: dict[str, Any]


class PolymarketInputError(ValueError):
    pass


class PolymarketClient:
    def __init__(
        self,
        gamma_base_url: str | None = None,
        data_base_url: str | None = None,
        timeout: float = 15.0,
    ) -> None:
        self._gamma_base_url = (gamma_base_url or settings.polymarket_gamma_base_url).rstrip("/")
        self._data_base_url = (data_base_url or settings.polymarket_data_base_url).rstrip("/")
        self._timeout = timeout

    async def resolve_account(self, raw_input: str) -> ResolvedPolymarketAccount:
        normalized = normalize_polymarket_input(raw_input)
        if is_wallet(normalized):
            profile = await self.fetch_profile_by_wallet(normalized)
            return ResolvedPolymarketAccount(
                input=raw_input.strip(),
                normalized_user=profile_name(profile) or normalized.lower(),
                proxy_wallet=normalized.lower(),
                profile=profile,
            )

        search_result = await self.search_profiles(normalized)
        profiles = search_result.get("profiles") or []
        profile = select_profile(profiles, normalized)
        proxy_wallet = string_or_none(profile.get("proxyWallet"))
        if not proxy_wallet or not is_wallet(proxy_wallet):
            raise PolymarketInputError(f"未找到 Polymarket 用户对应的钱包: {raw_input}")
        return ResolvedPolymarketAccount(
            input=raw_input.strip(),
            normalized_user=profile_name(profile) or normalized,
            proxy_wallet=proxy_wallet.lower(),
            profile=profile,
        )

    async def search_profiles(self, query: str) -> dict[str, Any]:
        params = {
            "q": query,
            "search_profiles": "true",
            "limit_per_type": 10,
        }
        try:
            async with httpx.AsyncClient(base_url=self._gamma_base_url, timeout=self._timeout) as client:
                response = await client.get("/public-search", params=params)
                response.raise_for_status()
                payload = response.json()
            service_health_store.set("polymarket", "running", metadata={"endpoint": "public-search"})
            return payload
        except Exception as exc:
            service_health_store.set("polymarket", "error", last_error=str(exc), metadata={"endpoint": "public-search"})
            logger.warning("Polymarket profile search failed", extra={"query": query}, exc_info=exc)
            raise

    async def fetch_profile_by_wallet(self, wallet: str) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(base_url=self._gamma_base_url, timeout=self._timeout) as client:
                response = await client.get("/public-profile", params={"address": wallet})
                if response.status_code == 404:
                    return {"proxyWallet": wallet}
                response.raise_for_status()
                payload = response.json()
            service_health_store.set("polymarket", "running", metadata={"endpoint": "profiles"})
            return payload if isinstance(payload, dict) else {"proxyWallet": wallet, "raw": payload}
        except Exception as exc:
            service_health_store.set("polymarket", "error", last_error=str(exc), metadata={"endpoint": "profiles"})
            logger.warning("Polymarket profile lookup failed", extra={"wallet": wallet}, exc_info=exc)
            return {"proxyWallet": wallet}

    async def fetch_activity(self, wallet: str, activity_limit: int) -> list[NormalizedActivity]:
        if activity_limit <= 0:
            return []
        page_size = activity_page_size(activity_limit)
        async with httpx.AsyncClient(base_url=self._data_base_url, timeout=self._timeout) as client:
            rows, exhausted = await self.fetch_parallel_offset_pages(
                client=client,
                wallet=wallet,
                activity_limit=activity_limit,
                page_size=page_size,
            )
            rows = dedupe_activity_rows(rows)
            if not exhausted and len(rows) < activity_limit:
                rows.extend(
                    await self.fetch_cursor_pages(
                        client=client,
                        wallet=wallet,
                        activity_limit=activity_limit - len(rows),
                        page_size=page_size,
                        end=oldest_timestamp(rows),
                    )
                )
        return [normalize_activity(row, wallet) for row in dedupe_activity_rows(rows)[:activity_limit]]

    async def fetch_parallel_offset_pages(
        self,
        *,
        client: httpx.AsyncClient,
        wallet: str,
        activity_limit: int,
        page_size: int,
    ) -> tuple[list[dict[str, Any]], bool]:
        page_count = offset_batch_page_count(activity_limit, page_size)
        rows, reached_end = await self.fetch_offset_page_batch(
            client=client,
            wallet=wallet,
            page_size=page_size,
            page_count=page_count,
            end=None,
        )
        if reached_end:
            return rows, True
        return rows, len(rows) >= activity_limit

    async def fetch_cursor_pages(
        self,
        *,
        client: httpx.AsyncClient,
        wallet: str,
        activity_limit: int,
        page_size: int,
        end: int | None,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        cursor_end = end
        while len(rows) < activity_limit and cursor_end is not None:
            page_count = offset_batch_page_count(activity_limit - len(rows), page_size)
            batch_rows, reached_end = await self.fetch_offset_page_batch(
                client=client,
                wallet=wallet,
                page_size=page_size,
                page_count=page_count,
                end=cursor_end,
            )
            if not batch_rows:
                break
            rows.extend(batch_rows)
            if reached_end or len(rows) >= activity_limit:
                break
            next_end = oldest_timestamp(batch_rows)
            if next_end is None or next_end >= cursor_end:
                break
            cursor_end = next_end
        return rows

    async def fetch_offset_page_batch(
        self,
        *,
        client: httpx.AsyncClient,
        wallet: str,
        page_size: int,
        page_count: int,
        end: int | None,
    ) -> tuple[list[dict[str, Any]], bool]:
        tasks = [
            self.fetch_activity_page(
                wallet=wallet,
                limit=page_size,
                offset=page_index * page_size,
                end=end,
                client=client,
            )
            for page_index in range(page_count)
        ]
        pages = await asyncio.gather(*tasks, return_exceptions=True)
        rows: list[dict[str, Any]] = []
        for page_index, page in enumerate(pages):
            offset = page_index * page_size
            if isinstance(page, Exception):
                if offset == 0:
                    raise page
                logger.warning(
                    "Polymarket offset page failed, falling back to cursor pagination",
                    extra={"wallet": wallet, "offset": offset, "end": end},
                    exc_info=page,
                )
                return rows, False
            rows.extend(page)
            if len(page) < page_size:
                return rows, True
        return rows, False

    async def fetch_activity_page(
        self,
        wallet: str,
        limit: int,
        offset: int,
        end: int | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> list[dict[str, Any]]:
        params = {
            "user": wallet,
            "limit": limit,
            "offset": offset,
            "sortBy": "TIMESTAMP",
            "sortDirection": "DESC",
        }
        if end is not None:
            params["end"] = end
        try:
            if client is None:
                async with httpx.AsyncClient(base_url=self._data_base_url, timeout=self._timeout) as scoped_client:
                    response = await scoped_client.get("/activity", params=params)
                    raise_for_activity_status(response)
                    payload = response.json()
            else:
                response = await client.get("/activity", params=params)
                raise_for_activity_status(response)
                payload = response.json()
            service_health_store.set("polymarket", "running", metadata={"endpoint": "activity"})
            if not isinstance(payload, list):
                raise RuntimeError("Unexpected Polymarket activity response")
            return [row for row in payload if isinstance(row, dict)]
        except Exception as exc:
            service_health_store.set("polymarket", "error", last_error=str(exc), metadata={"endpoint": "activity"})
            logger.warning(
                "Polymarket activity fetch failed",
                extra={"wallet": wallet, "offset": offset, "end": end},
                exc_info=exc,
            )
            raise


def normalize_polymarket_input(raw_input: str) -> str:
    value = raw_input.strip()
    if not value:
        raise PolymarketInputError("请输入 Polymarket profile 或钱包地址")

    wallet_match = re.search(r"0x[a-fA-F0-9]{40}", value)
    if wallet_match:
        return wallet_match.group(0).lower()

    if value.startswith("@"):
        return value[1:].strip()

    if "://" in value or value.startswith("polymarket.com/"):
        parsed = urlparse(value if "://" in value else f"https://{value}")
        query = parse_qs(parsed.query)
        for key in ("user", "profile", "username"):
            if query.get(key):
                return query[key][0].strip().lstrip("@")
        parts = [part for part in parsed.path.split("/") if part]
        if parts:
            return parts[-1].strip().lstrip("@")

    return value


def page_count_for_limit(activity_limit: int, page_size: int) -> int:
    return max(1, (activity_limit + page_size - 1) // page_size)


def activity_page_size(activity_limit: int) -> int:
    if activity_limit <= ACTIVITY_PAGE_SIZE:
        return max(1, activity_limit)
    return min(ACTIVITY_PAGE_SIZE, max(1, ACTIVITY_MAX_OFFSET // ACTIVITY_PARALLEL_REQUESTS))


def offset_batch_page_count(activity_limit: int, page_size: int) -> int:
    max_offset_pages = ((ACTIVITY_MAX_OFFSET - 1) // page_size) + 1
    return min(page_count_for_limit(activity_limit, page_size), ACTIVITY_PARALLEL_REQUESTS, max_offset_pages)


def dedupe_activity_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        key = row_identity(row)
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


def row_identity(row: dict[str, Any]) -> str:
    transaction_hash = string_or_none(row.get("transactionHash") or row.get("transaction_hash"))
    if transaction_hash:
        return "|".join(
            [
                transaction_hash.lower(),
                str(row.get("timestamp") or ""),
                str(row.get("conditionId") or row.get("condition_id") or ""),
                str(row.get("asset") or ""),
                str(row.get("outcome") or ""),
                str(row.get("side") or ""),
                str(row.get("size") or ""),
                str(row.get("usdcSize") or row.get("usdc_size") or ""),
            ]
        )
    return json.dumps(row, sort_keys=True, default=str)


def oldest_timestamp(rows: list[dict[str, Any]]) -> int | None:
    timestamps: list[int] = []
    for row in rows:
        try:
            timestamps.append(int(parse_timestamp(row.get("timestamp")).timestamp()))
        except PolymarketInputError:
            continue
    if not timestamps:
        return None
    return min(timestamps) - 1


def raise_for_activity_status(response: httpx.Response) -> None:
    if response.status_code < 400:
        return
    detail = response.text[:600]
    raise httpx.HTTPStatusError(
        f"Polymarket activity returned {response.status_code}: {detail}",
        request=response.request,
        response=response,
    )


def is_wallet(value: str) -> bool:
    return bool(WALLET_RE.fullmatch(value.strip()))


def account_id_for_wallet(wallet: str) -> str:
    return wallet.lower()


def select_profile(profiles: list[Any], query: str) -> dict[str, Any]:
    if not profiles:
        raise PolymarketInputError(f"未找到 Polymarket 用户: {query}")
    lowered = query.lower().lstrip("@")
    for profile in profiles:
        if not isinstance(profile, dict):
            continue
        names = [
            string_or_none(profile.get("name")),
            string_or_none(profile.get("pseudonym")),
        ]
        if any(name and name.lower() == lowered for name in names):
            return profile
    first = profiles[0]
    if not isinstance(first, dict):
        raise PolymarketInputError(f"未找到 Polymarket 用户: {query}")
    return first


def profile_name(profile: dict[str, Any]) -> str | None:
    return string_or_none(profile.get("name")) or string_or_none(profile.get("pseudonym"))


def normalize_activity(row: dict[str, Any], wallet: str) -> NormalizedActivity:
    timestamp = parse_timestamp(row.get("timestamp"))
    transaction_hash = string_or_none(row.get("transactionHash") or row.get("transaction_hash"))
    activity_type = string_or_none(row.get("type")) or "UNKNOWN"
    condition_id = string_or_none(row.get("conditionId") or row.get("condition_id"))
    asset = string_or_none(row.get("asset"))
    outcome = string_or_none(row.get("outcome"))
    activity_id = make_activity_id(
        row=row,
        wallet=wallet,
        transaction_hash=transaction_hash,
        timestamp=timestamp,
        activity_type=activity_type,
        condition_id=condition_id,
        asset=asset,
        outcome=outcome,
    )
    return NormalizedActivity(
        id=activity_id,
        proxy_wallet=string_or_none(row.get("proxyWallet")) or wallet.lower(),
        timestamp=timestamp,
        type=activity_type,
        condition_id=condition_id,
        slug=string_or_none(row.get("slug")),
        event_slug=string_or_none(row.get("eventSlug") or row.get("event_slug")),
        title=string_or_none(row.get("title")),
        side=string_or_none(row.get("side")),
        outcome=outcome,
        asset=asset,
        price=decimal_or_none(row.get("price")),
        size=decimal_or_none(row.get("size")),
        usdc_size=decimal_or_none(row.get("usdcSize") or row.get("usdc_size")),
        transaction_hash=transaction_hash,
        raw=row,
    )


def make_activity_id(
    *,
    row: dict[str, Any],
    wallet: str,
    transaction_hash: str | None,
    timestamp: datetime,
    activity_type: str,
    condition_id: str | None,
    asset: str | None,
    outcome: str | None,
) -> str:
    if transaction_hash:
        seed = "|".join(
            [
                transaction_hash.lower(),
                activity_type,
                str(int(timestamp.timestamp())),
                condition_id or "",
                asset or "",
                outcome or "",
                string_or_none(row.get("side")) or "",
                str(row.get("size") or ""),
                str(row.get("usdcSize") or row.get("usdc_size") or ""),
            ]
        )
    else:
        seed = json.dumps(row, sort_keys=True, default=str)
    digest = hashlib.sha1(f"{wallet.lower()}|{seed}".encode("utf-8")).hexdigest()
    return digest


def parse_timestamp(value: Any) -> datetime:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    if isinstance(value, str):
        if value.isdigit():
            return datetime.fromtimestamp(int(value), tz=timezone.utc)
        normalized = value.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            pass
        else:
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    raise PolymarketInputError("Activity 缺少有效 timestamp")


def decimal_or_none(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
