from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import re
from statistics import median
from typing import Any, Mapping, Protocol
from zoneinfo import ZoneInfo

from app.schemas.report import AccountSummary, DailyPerformance, MarketPerformance, RecentPerformance

DUST = Decimal("0.000001")
ZERO = Decimal("0")
RECENT_WINDOWS = (1, 3, 7, 14, 30)
EASTERN_TZ = ZoneInfo("America/New_York")
TITLE_CLOSE_TIME_RE = re.compile(
    r"(?P<month>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+"
    r"(?P<day>\d{1,2}),?\s+"
    r"(?:(?P<start>\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\s*-\s*)?"
    r"(?P<end>\d{1,2}(?::\d{2})?\s*(?:am|pm))\s*et",
    re.IGNORECASE,
)
MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


class ActivityLike(Protocol):
    id: str
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


class MarketMetadataLike(Protocol):
    slug: str
    closed: bool
    outcome: str | None
    raw_outcome: str | None
    event: dict[str, Any]
    market: dict[str, Any]


@dataclass
class OutcomePosition:
    cost: Decimal = ZERO
    buy_shares: Decimal = ZERO
    current_shares: Decimal = ZERO


@dataclass
class MarketAccumulator:
    market_id: str
    title: str
    slug: str | None = None
    condition_id: str | None = None
    event_slug: str | None = None
    activity_count: int = 0
    redeem_count: int = 0
    merge_count: int = 0
    cost: Decimal = ZERO
    buy_cost: Decimal = ZERO
    split_cost: Decimal = ZERO
    split_shares: Decimal = ZERO
    sell_return: Decimal = ZERO
    redeemed: Decimal = ZERO
    redeemed_shares: Decimal = ZERO
    merged: Decimal = ZERO
    merged_shares: Decimal = ZERO
    recovery: Decimal = ZERO
    merge_return: Decimal = ZERO
    maker_rebate: Decimal = ZERO
    redeem_time: datetime | None = None
    merge_time: datetime | None = None
    market_date: datetime | None = None
    first_activity_at: datetime | None = None
    last_activity_at: datetime | None = None
    metadata_closed: bool = False
    metadata_outcome: str | None = None
    metadata_raw_outcome: str | None = None
    outcomes: dict[str, OutcomePosition] = field(default_factory=dict)
    incomplete: bool = False

    @property
    def pnl(self) -> Decimal:
        return self.recovery - self.cost

    @property
    def pnl_with_rebate(self) -> Decimal:
        return self.pnl + self.maker_rebate


def build_account_summary(
    account_id: str,
    activities: list[ActivityLike],
    market_metadata: Mapping[str, MarketMetadataLike] | None = None,
) -> AccountSummary:
    markets = aggregate_markets(activities, market_metadata=market_metadata)
    market_items = list(markets.values())
    data_start = min((activity.timestamp for activity in activities), default=None)
    data_end = max((activity.timestamp for activity in activities), default=None)
    costs = [market.cost for market in market_items if market.cost > ZERO]
    profits = [market.pnl for market in market_items if market.pnl > ZERO]
    losses = [market.pnl for market in market_items if market.pnl < ZERO]
    settled = [market for market in market_items if is_reliable_settled(market)]
    total_cost = sum_decimal(market.cost for market in market_items)
    total_recovery = sum_decimal(market.recovery for market in market_items)
    maker_rebate_amount = sum_decimal(market.maker_rebate for market in market_items)
    total_pnl = total_recovery - total_cost
    winning = [market for market in market_items if market.pnl > DUST]
    losing = [market for market in market_items if market.pnl < -DUST]
    breakeven = [market for market in market_items if abs(market.pnl) <= DUST]
    unsettled = [market for market in market_items if is_open_market(market)]

    return AccountSummary(
        account_id=account_id,
        activity_count=len(activities),
        market_count=len(market_items),
        data_start=data_start,
        data_end=data_end,
        generated_at=datetime.now(timezone.utc),
        total_cost=as_float(total_cost),
        total_recovery=as_float(total_recovery),
        total_pnl=as_float(total_pnl),
        total_pnl_with_rebate=as_float(total_pnl + maker_rebate_amount),
        total_roi=ratio(total_pnl, total_cost),
        maker_rebate_count=len([activity for activity in activities if activity.type == "MAKER_REBATE"]),
        maker_rebate_amount=as_float(maker_rebate_amount),
        settled_market_count=len(settled),
        unsettled_market_count=len(unsettled),
        unsettled_exposure=as_float(sum_decimal(open_exposure(market) for market in unsettled)),
        win_market_count=len(winning),
        loss_market_count=len(losing),
        breakeven_market_count=len(breakeven),
        win_rate=ratio(Decimal(len(winning)), Decimal(len(winning) + len(losing))) if winning or losing else None,
        average_cost=as_float(sum_decimal(costs) / Decimal(len(costs))) if costs else None,
        median_cost=as_float(Decimal(str(median(costs)))) if costs else None,
        max_cost=as_float(max(costs)) if costs else None,
        average_profit=as_float(sum_decimal(profits) / Decimal(len(profits))) if profits else None,
        average_loss=as_float(sum_decimal(losses) / Decimal(len(losses))) if losses else None,
        incomplete_market_count=len([market for market in market_items if market.incomplete]),
        recent=build_recent_performance(activities, market_items),
        daily_last_7d=build_daily_performance(activities, market_items),
    )


def build_market_performance(
    activities: list[ActivityLike],
    market_metadata: Mapping[str, MarketMetadataLike] | None = None,
) -> list[MarketPerformance]:
    markets = aggregate_markets(activities, market_metadata=market_metadata).values()
    return sorted(
        [serialize_market(market) for market in markets],
        key=lambda item: item.market_date or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )


def aggregate_markets(
    activities: list[ActivityLike],
    market_metadata: Mapping[str, MarketMetadataLike] | None = None,
) -> dict[str, MarketAccumulator]:
    markets: dict[str, MarketAccumulator] = {}
    for activity in sorted(activities, key=lambda item: item.timestamp):
        market_id = market_key(activity)
        title = activity.title or activity.slug or activity.condition_id or "(unknown)"
        market = markets.setdefault(
            market_id,
            MarketAccumulator(
                market_id=market_id,
                title=title,
                slug=activity.slug,
                condition_id=activity.condition_id,
                event_slug=activity.event_slug,
            ),
        )
        market.activity_count += 1
        market.slug = market.slug or activity.slug
        market.condition_id = market.condition_id or activity.condition_id
        market.event_slug = market.event_slug or activity.event_slug
        market.market_date = parse_market_close_time(title, activity.timestamp) or max_date(market.market_date, activity.timestamp)
        market.first_activity_at = min_date(market.first_activity_at, activity.timestamp)
        market.last_activity_at = max_date(market.last_activity_at, activity.timestamp)
        apply_activity(market, activity)
    if market_metadata:
        for market in markets.values():
            apply_market_metadata(market, market_metadata)
    return markets


def apply_market_metadata(
    market: MarketAccumulator,
    market_metadata: Mapping[str, MarketMetadataLike],
) -> None:
    if not market.slug:
        return
    metadata = market_metadata.get(market.slug)
    if metadata is None:
        return
    market.metadata_closed = metadata.closed
    market.metadata_outcome = metadata.outcome
    market.metadata_raw_outcome = metadata.raw_outcome
    title = string_or_none(metadata.market.get("question")) or string_or_none(metadata.market.get("title"))
    if title:
        market.title = title
    market_date = parse_metadata_date(metadata.market) or parse_metadata_date(metadata.event)
    if market_date:
        market.market_date = market_date


def apply_activity(market: MarketAccumulator, activity: ActivityLike) -> None:
    amount = activity.usdc_size or ZERO
    size = activity.size or ZERO
    activity_type = activity.type.upper()
    side = (activity.side or "").upper()
    outcome = normalize_outcome(activity.outcome)

    if activity_type == "TRADE" and side == "BUY":
        market.buy_cost += amount
        market.cost += amount
        position = market.outcomes.setdefault(outcome, OutcomePosition())
        position.cost += amount
        position.buy_shares += size
        position.current_shares += size
    elif activity_type == "TRADE" and side == "SELL":
        market.sell_return += amount
        market.recovery += amount
        position = market.outcomes.setdefault(outcome, OutcomePosition())
        position.current_shares -= size
    elif activity_type == "SPLIT":
        market.split_cost += amount
        market.split_shares += size
        market.cost += amount
        if size > ZERO and is_up_down_market(market):
            for split_outcome in ("up", "down"):
                position = market.outcomes.setdefault(split_outcome, OutcomePosition())
                position.cost += amount / Decimal(2)
                position.buy_shares += size
                position.current_shares += size
    elif activity_type == "MERGE":
        market.merged += amount
        market.merged_shares += size
        market.recovery += amount
        market.merge_return += amount
        market.merge_count += 1
        market.merge_time = max_date(market.merge_time, activity.timestamp)
        reduce_all_outcomes(market, size)
    elif activity_type == "REDEEM":
        market.redeemed += amount
        market.redeemed_shares += size
        market.recovery += amount
        market.redeem_count += 1
        market.redeem_time = max_date(market.redeem_time, activity.timestamp)
        redeemed_outcome = infer_redeem_outcome(market)
        market.incomplete = has_incomplete_redeem(market)
        if redeemed_outcome and redeemed_outcome in market.outcomes:
            market.outcomes[redeemed_outcome].current_shares -= size
    elif activity_type == "MAKER_REBATE":
        market.maker_rebate += amount


def serialize_market(market: MarketAccumulator) -> MarketPerformance:
    up = market.outcomes.get("up") or OutcomePosition()
    down = market.outcomes.get("down") or OutcomePosition()
    remaining_up = current_shares_for(market, "up")
    remaining_down = current_shares_for(market, "down")
    if_up_pnl = market.merged + remaining_up - market.cost
    if_down_pnl = market.merged + remaining_down - market.cost
    position_status = format_position_status(market)

    return MarketPerformance(
        market_id=market.market_id,
        title=market.title,
        slug=market.slug,
        condition_id=market.condition_id,
        event_slug=market.event_slug,
        result=inferred_result(market),
        position_status=position_status,
        activity_count=market.activity_count,
        redeem_count=market.redeem_count,
        merge_count=market.merge_count,
        market_date=market.market_date,
        redeem_time=market.redeem_time,
        up_cost=as_float(up.cost),
        up_shares=as_float(remaining_up),
        up_average_cost=ratio(up.cost, up.buy_shares) if up.buy_shares > DUST else None,
        down_cost=as_float(down.cost),
        down_shares=as_float(remaining_down),
        down_average_cost=ratio(down.cost, down.buy_shares) if down.buy_shares > DUST else None,
        cost=as_float(market.cost),
        recovery=as_float(market.recovery),
        merge_return=as_float(market.merge_return),
        maker_rebate=as_float(market.maker_rebate),
        pnl=as_float(market.pnl),
        pnl_with_rebate=as_float(market.pnl_with_rebate),
        roi=ratio(market.pnl, market.cost),
        if_up_pnl=as_float(if_up_pnl) if is_up_down_market(market) else None,
        if_up_roi=ratio(if_up_pnl, market.cost) if is_up_down_market(market) else None,
        if_down_pnl=as_float(if_down_pnl) if is_up_down_market(market) else None,
        if_down_roi=ratio(if_down_pnl, market.cost) if is_up_down_market(market) else None,
        incomplete=market.incomplete,
    )


def build_recent_performance(
    activities: list[ActivityLike],
    market_items: list[MarketAccumulator] | None = None,
) -> list[RecentPerformance]:
    now = max((activity.timestamp for activity in activities), default=datetime.now(timezone.utc))
    all_markets = market_items if market_items is not None else list(aggregate_markets(activities).values())
    result: list[RecentPerformance] = []
    for days in RECENT_WINDOWS:
        start = now - timedelta(days=days)
        markets = [market for market in all_markets if (market_detail_time(market) or datetime.min.replace(tzinfo=timezone.utc)) >= start]
        settled = [market for market in markets if is_reliable_settled(market)]
        unsettled = [market for market in markets if is_open_market(market)]
        cost = sum_decimal(market.cost for market in markets)
        recovery = sum_decimal(market.recovery for market in markets)
        pnl = recovery - cost
        winning = [market for market in markets if market.pnl > DUST]
        losing = [market for market in markets if market.pnl < -DUST]
        result.append(
            RecentPerformance(
                days=days,
                market_count=len(markets),
                settled_market_count=len(settled),
                unsettled_market_count=len(unsettled),
                cost=as_float(cost),
                recovery=as_float(recovery),
                pnl=as_float(pnl),
                roi=ratio(pnl, cost),
                win_rate=ratio(Decimal(len(winning)), Decimal(len(winning) + len(losing))) if winning or losing else None,
                unsettled_exposure=as_float(sum_decimal(open_exposure(market) for market in unsettled)),
            )
        )
    return result


def build_daily_performance(
    activities: list[ActivityLike],
    market_items: list[MarketAccumulator] | None = None,
) -> list[DailyPerformance]:
    if not activities:
        return []
    latest_date = max(activity.timestamp for activity in activities).date()
    by_date: dict[str, list[MarketAccumulator]] = defaultdict(list)
    all_markets = market_items if market_items is not None else list(aggregate_markets(activities).values())
    for market in all_markets:
        detail_time = market_detail_time(market)
        if detail_time and detail_time.date() >= latest_date - timedelta(days=6):
            by_date[detail_time.date().isoformat()].append(market)
    result: list[DailyPerformance] = []
    for offset in range(6, -1, -1):
        date_key = (latest_date - timedelta(days=offset)).isoformat()
        markets = by_date.get(date_key, [])
        cost = sum_decimal(market.cost for market in markets)
        recovery = sum_decimal(market.recovery for market in markets)
        pnl = recovery - cost
        result.append(DailyPerformance(date=date_key, cost=as_float(cost), recovery=as_float(recovery), pnl=as_float(pnl), roi=ratio(pnl, cost)))
    return result


def market_key(activity: ActivityLike) -> str:
    return activity.title or activity.slug or activity.condition_id or "(unknown)"


def market_detail_time(market: MarketAccumulator) -> datetime | None:
    candidates = [market.redeem_time, market.merge_time, market.last_activity_at]
    return max((candidate for candidate in candidates if candidate is not None), default=None)


def is_settled(market: MarketAccumulator) -> bool:
    return market.metadata_closed or open_exposure(market) <= DUST or market.redeem_count > 0 or market.sell_return > ZERO or market.merge_count > 0


def is_reliable_settled(market: MarketAccumulator) -> bool:
    return is_settled(market) and not market.incomplete


def is_open_market(market: MarketAccumulator) -> bool:
    return not market.metadata_closed and open_exposure(market) > DUST and max(ZERO, market.cost - market.recovery) > ZERO


def inferred_result(market: MarketAccumulator) -> str:
    if market.metadata_closed and market.metadata_outcome:
        return translate_outcome(market.metadata_outcome)
    if market.metadata_closed:
        return "已结算"
    if market.redeem_count <= 0:
        return "未结算"
    if market.incomplete:
        return "未结算"
    redeemed_outcome = infer_redeem_outcome(market)
    if not redeemed_outcome:
        return "已结算"
    return translate_outcome(redeemed_outcome)


def infer_redeem_outcome(market: MarketAccumulator) -> str:
    if market.redeemed_shares <= DUST and market.redeemed <= DUST:
        return ""
    if has_incomplete_redeem(market):
        return ""
    candidates = [
        (
            outcome,
            abs(max(ZERO, position.buy_shares - market.merged_shares) - market.redeemed_shares),
        )
        for outcome, position in market.outcomes.items()
    ]
    if not candidates:
        return ""
    return min(candidates, key=lambda item: item[1])[0]


def has_incomplete_redeem(market: MarketAccumulator) -> bool:
    if market.redeemed_shares <= DUST:
        return False
    shares = [
        max(ZERO, position.buy_shares - market.merged_shares)
        for position in market.outcomes.values()
    ]
    if not shares:
        return True
    return all(abs(value - market.redeemed_shares) > DUST for value in shares)


def normalize_outcome(value: str | None) -> str:
    if not value:
        return "unknown"
    lowered = value.strip().lower()
    if lowered in {"up", "yes"}:
        return "up"
    if lowered in {"down", "no"}:
        return "down"
    return lowered


def translate_outcome(value: str) -> str:
    if value == "up":
        return "上涨"
    if value == "down":
        return "下跌"
    return value


def reduce_all_outcomes(market: MarketAccumulator, size: Decimal) -> None:
    if size <= ZERO:
        return
    for position in market.outcomes.values():
        position.current_shares -= size


def open_exposure(market: MarketAccumulator) -> Decimal:
    return sum_decimal(positive(current_shares_for(market, outcome)) for outcome in market.outcomes)


def current_shares_for(market: MarketAccumulator, outcome: str) -> Decimal:
    position = market.outcomes.get(outcome)
    if position is None:
        return ZERO
    if market.redeem_count > 0:
        redeemed_outcome = infer_redeem_outcome(market)
        if not redeemed_outcome:
            return ZERO
        if outcome == redeemed_outcome:
            return positive(position.current_shares - market.redeemed_shares)
        return positive(position.current_shares)
    return positive(position.current_shares)


def format_position_status(market: MarketAccumulator) -> str:
    parts: list[str] = []
    labels = {"up": "Up", "down": "Down", "yes": "Yes", "no": "No"}
    for outcome, position in market.outcomes.items():
        shares = current_shares_for(market, outcome)
        if shares >= Decimal("0.01"):
            parts.append(f"{labels.get(outcome, outcome)} {as_float(shares):g}")
    return " / ".join(parts) if parts else "无持仓"


def is_up_down_market(market: MarketAccumulator) -> bool:
    text = f"{market.title} {market.slug or ''}".lower()
    return "up or down" in text or "updown" in text or "up" in market.outcomes or "down" in market.outcomes


def parse_market_close_time(title: str, reference: datetime) -> datetime | None:
    match = TITLE_CLOSE_TIME_RE.search(title)
    if not match:
        return None
    month_text = match.group("month").lower()
    month = MONTHS.get(month_text)
    if month is None:
        return None
    end_text = match.group("end")
    parsed_time = parse_title_time(end_text)
    if parsed_time is None:
        return None
    year = reference.astimezone(EASTERN_TZ).year
    local_dt = datetime(year, month, int(match.group("day")), parsed_time[0], parsed_time[1], tzinfo=EASTERN_TZ)
    reference_local = reference.astimezone(EASTERN_TZ)
    if local_dt - reference_local > timedelta(days=180):
        local_dt = local_dt.replace(year=year - 1)
    elif reference_local - local_dt > timedelta(days=180):
        local_dt = local_dt.replace(year=year + 1)
    return local_dt.astimezone(timezone.utc)


def parse_metadata_date(payload: dict[str, Any]) -> datetime | None:
    for key in ("endDateIso", "endDate", "closedTime", "umaEndDateIso", "umaEndDate"):
        value = string_or_none(payload.get(key))
        if not value:
            continue
        parsed = parse_iso_datetime(value)
        if parsed:
            return parsed
    return None


def parse_iso_datetime(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_title_time(value: str) -> tuple[int, int] | None:
    match = re.fullmatch(r"\s*(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<period>am|pm)\s*", value, re.IGNORECASE)
    if not match:
        return None
    hour = int(match.group("hour"))
    minute = int(match.group("minute") or "0")
    period = match.group("period").lower()
    if hour < 1 or hour > 12 or minute > 59:
        return None
    if period == "am":
        hour = 0 if hour == 12 else hour
    else:
        hour = 12 if hour == 12 else hour + 12
    return hour, minute


def positive(value: Decimal) -> Decimal:
    return value if value > DUST else ZERO


def sum_decimal(values) -> Decimal:
    total = ZERO
    for value in values:
        total += value or ZERO
    return total


def ratio(numerator: Decimal, denominator: Decimal) -> float | None:
    if denominator == ZERO:
        return None
    return as_float(numerator / denominator)


def as_float(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.000001")))


def min_date(left: datetime | None, right: datetime) -> datetime:
    return right if left is None or right < left else left


def max_date(left: datetime | None, right: datetime) -> datetime:
    return right if left is None or right > left else left


def string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
