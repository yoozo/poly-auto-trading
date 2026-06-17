import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from app.api import routes_reports
from app.db.session import get_session
from app.main import create_app
from app.schemas.report import AnalyzeAccountRequest, MarketPerformance
from app.services.report_analysis import build_account_summary, build_market_performance, parse_metadata_date
from app.services import market_metadata
from app.services.market_metadata import market_metadata_row
from app.services.polymarket_client import NormalizedActivity, PolymarketClient, normalize_polymarket_input
from app.services import report_snapshot
from app.services import report_store
from conftest import login_test_client


def make_client() -> TestClient:
    app = create_app(enable_lifespan=False)

    async def fake_session():
        yield object()

    app.dependency_overrides[get_session] = fake_session
    client = TestClient(app)
    login_test_client(client)
    return client


def test_normalize_polymarket_input() -> None:
    assert normalize_polymarket_input("@alice") == "alice"
    assert normalize_polymarket_input("https://polymarket.com/profile/alice") == "alice"
    assert (
        normalize_polymarket_input("0x1111111111111111111111111111111111111111").lower()
        == "0x1111111111111111111111111111111111111111"
    )


def test_analyze_account_starts_task(monkeypatch) -> None:
    calls = {}

    async def fake_create_task(session, task_id, message):
        calls["task_id"] = task_id
        calls["message"] = message

    class FakeAwaitable:
        def close(self):
            calls["closed"] = True

    class FakeAsyncioTask:
        pass

    def fake_run_account_analysis(task_id, payload):
        calls["payload"] = payload
        return FakeAwaitable()

    def fake_create_background_task(coro):
        calls["background"] = coro
        coro.close()
        return FakeAsyncioTask()

    monkeypatch.setattr(routes_reports, "create_task", fake_create_task)
    monkeypatch.setattr(routes_reports, "run_account_analysis", fake_run_account_analysis)
    monkeypatch.setattr(routes_reports.asyncio, "create_task", fake_create_background_task)

    client = make_client()
    response = client.post("/api/reports/accounts/analyze", json={"input": "@alice", "activity_limit": 100})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "running"
    assert len(body["task_id"]) == 32
    assert calls["message"] == "已创建分析任务"
    assert calls["payload"].input == "@alice"
    assert calls["payload"].activity_limit == 100


def test_task_status_not_found(monkeypatch) -> None:
    async def fake_get_task(session, task_id):
        return None

    monkeypatch.setattr(routes_reports, "get_task", fake_get_task)

    client = make_client()
    response = client.get("/api/reports/tasks/missing")

    assert response.status_code == 404


def test_list_accounts(monkeypatch) -> None:
    async def fake_list_accounts(session):
        return []

    monkeypatch.setattr(routes_reports, "list_accounts", fake_list_accounts)

    client = make_client()
    response = client.get("/api/reports/accounts")

    assert response.status_code == 200
    assert response.json() == []


def test_patch_account_updates_note(monkeypatch) -> None:
    calls = {}

    async def fake_update_account(session, account_id, note=None, favorite=None):
        calls["account_id"] = account_id
        calls["note"] = note
        calls["favorite"] = favorite
        return SimpleNamespace(
            id=account_id,
            input="@alice",
            normalized_user="alice",
            proxy_wallet="0x1111111111111111111111111111111111111111",
            profile={},
            favorite=False,
            note=note,
            last_downloaded_at=None,
            created_at=None,
            updated_at=None,
        )

    async def fake_get_account_activity_count(session, account_id):
        return 3

    monkeypatch.setattr(routes_reports, "update_account", fake_update_account)
    monkeypatch.setattr(routes_reports, "get_account_activity_count", fake_get_account_activity_count)

    client = make_client()
    response = client.patch("/api/reports/accounts/0xabc", json={"note": "主号"})

    assert response.status_code == 200
    assert response.json()["note"] == "主号"
    assert response.json()["activity_count"] == 3
    assert calls["account_id"] == "0xabc"
    assert calls["note"] == "主号"


def test_account_markets_returns_paginated_filtered_page(monkeypatch) -> None:
    async def fake_account_exists(session, account_id):
        return True

    async def fake_get_report_snapshot(session, account_id):
        return SimpleNamespace(
            markets=[
                make_market_performance("btc", "BTC Up or Down"),
                make_market_performance("eth", "ETH Up or Down"),
                make_market_performance("sol", "SOL Up or Down"),
            ]
        )

    monkeypatch.setattr(routes_reports, "account_exists", fake_account_exists)
    monkeypatch.setattr(routes_reports, "get_report_snapshot", fake_get_report_snapshot)

    client = make_client()
    response = client.get("/api/reports/accounts/0xabc/markets?search=up%20or%20down&offset=1&limit=1")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert body["offset"] == 1
    assert body["limit"] == 1
    assert [item["market_id"] for item in body["items"]] == ["eth"]


def test_account_markets_applies_query_filters_on_api(monkeypatch) -> None:
    async def fake_account_exists(session, account_id):
        return True

    btc = make_market_performance("btc", "BTC Up or Down")
    btc.market_date = datetime(2026, 6, 13, tzinfo=timezone.utc)
    btc.up_shares = 1
    btc.down_shares = 0
    old_xrp = make_market_performance("old-xrp", "XRP Up or Down")
    old_xrp.market_date = datetime(2026, 6, 12, tzinfo=timezone.utc)
    old_xrp.up_shares = 1
    old_xrp.down_shares = 1
    xrp = make_market_performance("xrp", "XRP Up or Down")
    xrp.market_date = datetime(2026, 6, 13, 12, tzinfo=timezone.utc)
    xrp.up_shares = 1
    xrp.down_shares = 1

    async def fake_get_report_snapshot(session, account_id):
        return SimpleNamespace(markets=[btc, old_xrp, xrp])

    monkeypatch.setattr(routes_reports, "account_exists", fake_account_exists)
    monkeypatch.setattr(routes_reports, "get_report_snapshot", fake_get_report_snapshot)

    client = make_client()
    response = client.get(
        "/api/reports/accounts/0xabc/markets"
        "?search=xrp&start_date=2026-06-13&end_date=2026-06-13&only_bilateral=true"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert [item["market_id"] for item in body["items"]] == ["xrp"]


def test_account_market_detail_returns_404_when_account_missing(monkeypatch) -> None:
    async def fake_account_exists(session, account_id):
        return False

    monkeypatch.setattr(routes_reports, "account_exists", fake_account_exists)

    client = make_client()
    response = client.get("/api/reports/accounts/0xabc/markets/btc")

    assert response.status_code == 404
    assert response.json()["detail"] == "account not found"


def test_account_market_detail_returns_404_when_market_missing(monkeypatch) -> None:
    async def fake_account_exists(session, account_id):
        return True

    async def fake_get_report_snapshot(session, account_id):
        return SimpleNamespace(markets=[make_market_performance("eth", "ETH Up or Down")])

    monkeypatch.setattr(routes_reports, "account_exists", fake_account_exists)
    monkeypatch.setattr(routes_reports, "get_report_snapshot", fake_get_report_snapshot)

    client = make_client()
    response = client.get("/api/reports/accounts/0xabc/markets/btc")

    assert response.status_code == 404
    assert response.json()["detail"] == "market not found"


def test_account_market_detail_returns_market_activities_and_metadata(monkeypatch) -> None:
    async def fake_account_exists(session, account_id):
        return True

    market = make_market_performance("btc", "BTC Up or Down")

    async def fake_get_report_snapshot(session, account_id):
        return SimpleNamespace(markets=[market])

    async def fake_list_account_market_activities(session, account_id, market_id):
        return [
            SimpleNamespace(
                id="act-1",
                timestamp=datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
                type="TRADE",
                condition_id="condition-1",
                slug="btc",
                event_slug="btc-event",
                title="BTC Up or Down",
                side="BUY",
                outcome="Up",
                asset="asset-1",
                price=Decimal("0.51"),
                size=Decimal("10"),
                usdc_size=Decimal("5.10"),
                transaction_hash="0xabc",
                raw={"transactionHash": "0xabc"},
            ),
            SimpleNamespace(
                id="act-2",
                timestamp=datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc),
                type="REDEEM",
                condition_id="condition-1",
                slug="btc",
                event_slug="btc-event",
                title="BTC Up or Down",
                side=None,
                outcome=None,
                asset=None,
                price=None,
                size=Decimal("10"),
                usdc_size=Decimal("10"),
                transaction_hash=None,
                raw={},
            ),
        ]

    async def fake_list_market_metadata(session, slugs):
        return {
            "btc": SimpleNamespace(
                slug="btc",
                closed=True,
                outcome="Up",
                raw_outcome="Up",
                event={"slug": "btc-event"},
                market={"question": "BTC Up or Down"},
                fetched_at=datetime(2026, 1, 1, 0, 2, tzinfo=timezone.utc),
                updated_at=datetime(2026, 1, 1, 0, 3, tzinfo=timezone.utc),
            )
        }

    monkeypatch.setattr(routes_reports, "account_exists", fake_account_exists)
    monkeypatch.setattr(routes_reports, "get_report_snapshot", fake_get_report_snapshot)
    monkeypatch.setattr(routes_reports, "list_account_market_activities", fake_list_account_market_activities)
    monkeypatch.setattr(routes_reports, "list_market_metadata", fake_list_market_metadata)

    client = make_client()
    response = client.get("/api/reports/accounts/0xabc/markets/btc")

    assert response.status_code == 200
    body = response.json()
    assert body["market"]["market_id"] == "btc"
    assert body["metadata"]["outcome"] == "Up"
    assert [item["id"] for item in body["activities"]] == ["act-1", "act-2"]
    assert body["activities"][0]["transaction_hash"] == "0xabc"
    assert body["activities"][0]["price"] == 0.51
    assert body["activities"][0]["size"] == 10.0
    assert body["activities"][0]["usdc_size"] == 5.1


@pytest.mark.asyncio
async def test_run_account_analysis_rebuilds_account_activities(monkeypatch) -> None:
    calls: dict[str, object] = {}

    async def fake_resolve_account(raw_input: str):
        return SimpleNamespace(
            input=raw_input,
            normalized_user="alice",
            proxy_wallet="0x1111111111111111111111111111111111111111",
            profile={},
        )

    async def fake_upsert_account(session, resolved):
        return SimpleNamespace(
            id="acc-1",
            proxy_wallet=resolved.proxy_wallet,
            normalized_user=resolved.normalized_user,
        )

    async def fake_upsert_activities(session, account_id: str, activities):
        return len(activities)

    async def fake_delete_account_activities(session, account_id):
        calls["deleted_account_id"] = account_id
        calls["deleted_count"] = 3
        return 3

    async def fake_list_account_activity_slugs(session, account_id: str):
        return {"btc-up-down"}

    async def fake_ensure_market_metadata_for_slugs(session, slugs, progress_callback=None):
        calls["metadata_slugs"] = slugs
        return {}

    async def fake_get_account_activity_count(session, account_id: str):
        return 5000

    class FakeClient:
        async def resolve_account(self, raw_input: str):
            return await fake_resolve_account(raw_input)

        async def iter_activity_batches(self, wallet: str, activity_limit: int, end: int | None = None):
            calls["iter_params"] = (wallet, activity_limit, end)
            yield [
                make_normalized_activity(1),
            ]

    async def fake_update_task(*args, **kwargs):
        calls.setdefault("tasks", []).append(kwargs)

    def fake_clear_report_snapshot_cache(account_id=None):
        calls["cleared_snapshot_account_id"] = account_id

    monkeypatch.setattr(routes_reports, "PolymarketClient", lambda: FakeClient())
    monkeypatch.setattr(routes_reports, "upsert_account", fake_upsert_account)
    monkeypatch.setattr(routes_reports, "delete_account_activities", fake_delete_account_activities)
    monkeypatch.setattr(routes_reports, "upsert_activities", fake_upsert_activities)
    monkeypatch.setattr(routes_reports, "list_account_activity_slugs", fake_list_account_activity_slugs)
    monkeypatch.setattr(routes_reports, "ensure_market_metadata_for_slugs", fake_ensure_market_metadata_for_slugs)
    monkeypatch.setattr(routes_reports, "get_account_activity_count", fake_get_account_activity_count)
    monkeypatch.setattr(routes_reports, "update_task", fake_update_task)
    monkeypatch.setattr(routes_reports, "clear_report_snapshot_cache", fake_clear_report_snapshot_cache)
    monkeypatch.setattr(routes_reports, "logger", SimpleNamespace(exception=lambda *_, **__: None))

    await routes_reports.run_account_analysis(
        "task-1",
        AnalyzeAccountRequest(input="@alice", activity_limit=5000),
    )

    assert calls["iter_params"] == ("0x1111111111111111111111111111111111111111", 5000, None)
    assert calls["deleted_count"] == 3
    assert calls["deleted_account_id"] == "acc-1"
    assert calls["cleared_snapshot_account_id"] == "acc-1"


@pytest.mark.asyncio
async def test_report_snapshot_coalesces_concurrent_requests(monkeypatch) -> None:
    calls = 0
    newest = datetime(2026, 1, 1, tzinfo=timezone.utc)

    async def fake_get_account_activity_bounds(session, account_id):
        return 10, None, newest

    async def fake_list_account_activity_slugs(session, account_id):
        return {"btc-up-down"}

    async def fake_get_market_metadata_updated_at(session, slugs):
        return datetime(2026, 1, 2, tzinfo=timezone.utc)

    async def fake_build_report_snapshot(key):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return report_snapshot.ReportSnapshot(
            key=key,
            summary=None,
            markets=[],
            cached_at=datetime.now(timezone.utc),
        )

    report_snapshot.clear_report_snapshot_cache()
    monkeypatch.setattr(report_snapshot, "get_account_activity_bounds", fake_get_account_activity_bounds)
    monkeypatch.setattr(report_snapshot, "list_account_activity_slugs", fake_list_account_activity_slugs)
    monkeypatch.setattr(report_snapshot, "get_market_metadata_updated_at", fake_get_market_metadata_updated_at)
    monkeypatch.setattr(report_snapshot, "build_report_snapshot", fake_build_report_snapshot)

    left, right = await asyncio.gather(
        report_snapshot.get_report_snapshot(object(), "0xabc"),
        report_snapshot.get_report_snapshot(object(), "0xabc"),
    )

    assert left is right
    assert calls == 1


def test_report_summary_aggregates_activity_rules() -> None:
    activities = [
        make_activity("a1", "TRADE", "BUY", "Up", "100", "0", "10"),
        make_activity("a2", "TRADE", "SELL", "Up", "0", "35", "3"),
        make_activity("a3", "REDEEM", None, "Up", "0", "70", "7"),
        make_activity("a4", "MAKER_REBATE", None, None, "2", "0", "0"),
    ]

    summary = build_account_summary("0xabc", activities)
    markets = build_market_performance(activities)

    assert summary.activity_count == 4
    assert summary.market_count == 1
    assert summary.total_cost == 100
    assert summary.total_recovery == 105
    assert summary.total_pnl == 5
    assert summary.total_pnl_with_rebate == 7
    assert summary.maker_rebate_amount == 2
    assert summary.win_market_count == 1
    assert markets[0].cost == 100
    assert markets[0].recovery == 105
    assert markets[0].pnl_with_rebate == 7
    assert markets[0].result == "未结算"
    assert markets[0].incomplete is True


def test_market_performance_does_not_double_count_redeemed_shares() -> None:
    activities = [
        make_activity("buy", "TRADE", "BUY", "Up", "2.5", "0", "5.35714"),
        make_activity("redeem", "REDEEM", None, "Up", "0", "5.35714", "5.35714"),
    ]

    market = build_market_performance(activities)[0]

    assert market.result == "上涨"
    assert market.position_status == "无持仓"
    assert market.up_shares == 5.35714
    assert market.down_shares == 0


def test_market_performance_uses_redeem_amount_when_size_is_missing() -> None:
    activities = [
        make_activity("buy", "TRADE", "BUY", "Down", "4.13", "0", "7.27273"),
        make_activity("redeem", "REDEEM", None, "Down", "0", "7.27", "0"),
    ]

    market = build_market_performance(activities)[0]

    assert market.result == "下跌"
    assert market.position_status == "无持仓"
    assert market.up_shares == 0
    assert market.down_shares == 7.27273


def test_market_hypothetical_uses_outcome_buy_shares_not_actual_pnl() -> None:
    activities = [
        make_activity("buy", "TRADE", "BUY", "Down", "2", "0", "4", slug="btc-down-win"),
        make_activity("sell", "TRADE", "SELL", "Down", "0", "3", "4", slug="btc-down-win"),
    ]
    metadata = SimpleNamespace(
        slug="btc-down-win",
        closed=True,
        outcome="down",
        raw_outcome="Down",
        event={"endDate": "2026-01-01T01:00:00Z"},
        market={"question": "BTC Up or Down", "endDateIso": "2026-01-01T00:30:00Z"},
    )

    market = build_market_performance(activities, market_metadata={"btc-down-win": metadata})[0]

    assert market.result == "下跌"
    assert market.pnl == 1
    assert market.if_down_pnl == 2
    assert market.if_down_roi == 1


def test_market_performance_skips_activity_without_market_identity() -> None:
    activity = make_activity("missing-market", "MAKER_REBATE", None, None, "2", "0", "0")
    activity.title = None
    activity.slug = None
    activity.condition_id = None

    summary = build_account_summary("0xabc", [activity])
    markets = build_market_performance([activity])

    assert summary.activity_count == 1
    assert summary.market_count == 0
    assert markets == []


def test_market_performance_sorts_by_market_close_time_desc() -> None:
    activities = [
        make_activity(
            "old",
            "TRADE",
            "BUY",
            "Up",
            "1",
            "0",
            "1",
            title="Bitcoin Up or Down - June 12, 8:30AM-8:35AM ET",
        ),
        make_activity(
            "new",
            "TRADE",
            "BUY",
            "Up",
            "1",
            "0",
            "1",
            title="Bitcoin Up or Down - June 12, 10:30AM-10:35AM ET",
        ),
    ]

    markets = build_market_performance(activities)

    assert markets[0].title == "Bitcoin Up or Down - June 12, 10:30AM-10:35AM ET"
    assert markets[1].title == "Bitcoin Up or Down - June 12, 8:30AM-8:35AM ET"


def test_recent_performance_uses_full_market_cost_when_buy_is_outside_window() -> None:
    activities = [
        make_activity(
            "buy",
            "TRADE",
            "BUY",
            "Up",
            "100",
            "0",
            "100",
            timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ),
        make_activity(
            "redeem",
            "REDEEM",
            None,
            "Up",
            "0",
            "60",
            "100",
            timestamp=datetime(2026, 1, 3, tzinfo=timezone.utc),
        ),
    ]

    summary = build_account_summary("0xabc", activities)
    recent_1d = next(item for item in summary.recent if item.days == 1)

    assert recent_1d.cost == 100
    assert recent_1d.recovery == 60
    assert recent_1d.pnl == -40
    assert recent_1d.roi == -0.4


def test_market_metadata_overrides_inferred_market_result() -> None:
    activities = [
        make_activity(
            "buy",
            "TRADE",
            "BUY",
            "Up",
            "100",
            "0",
            "100",
            timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ),
    ]
    metadata = SimpleNamespace(
        slug="btc-up-down",
        closed=True,
        outcome="down",
        raw_outcome="Down",
        event={"endDate": "2026-01-01T01:00:00Z"},
        market={"question": "Bitcoin Up or Down", "endDateIso": "2026-01-01T00:30:00Z"},
    )

    markets = build_market_performance(activities, market_metadata={"btc-up-down": metadata})

    assert markets[0].result == "下跌"
    assert markets[0].title == "Bitcoin Up or Down"
    assert markets[0].market_date == datetime(2026, 1, 1, 0, 30, tzinfo=timezone.utc)


def test_settled_metadata_keeps_actual_recovery_and_hypothetical_buy_shares() -> None:
    activities = [
        make_activity("buy", "TRADE", "BUY", "Down", "2", "0", "10", slug="btc-up-win"),
        make_activity("sell", "TRADE", "SELL", "Down", "0", "3", "10", slug="btc-up-win"),
    ]
    metadata = SimpleNamespace(
        slug="btc-up-win",
        closed=True,
        outcome="up",
        raw_outcome="Up",
        event={"endDate": "2026-01-01T01:00:00Z"},
        market={"question": "Bitcoin Up or Down", "endDateIso": "2026-01-01T00:30:00Z"},
    )

    market = build_market_performance(activities, market_metadata={"btc-up-win": metadata})[0]

    assert market.result == "上涨"
    assert market.position_status == "无持仓"
    assert market.cost == 2
    assert market.recovery == 3
    assert market.pnl == 1
    assert market.down_cost == 2
    assert market.down_shares == 10
    assert market.if_up_pnl == -2
    assert market.if_up_roi == -1
    assert market.if_down_pnl == 8
    assert market.if_down_roi == 4


def test_up_down_title_market_date_is_not_overridden_by_event_metadata() -> None:
    activities = [
        make_activity(
            "xrp-0940",
            "TRADE",
            "BUY",
            "Down",
            "1",
            "0",
            "1",
            title="XRP Up or Down - June 13, 9:40AM-9:45AM ET",
            slug="xrp-0940",
            timestamp=datetime(2026, 6, 13, 13, 44, tzinfo=timezone.utc),
        ),
        make_activity(
            "xrp-0945",
            "TRADE",
            "BUY",
            "Up",
            "1",
            "0",
            "1",
            title="XRP Up or Down - June 13, 9:45AM-9:50AM ET",
            slug="xrp-0945",
            timestamp=datetime(2026, 6, 13, 13, 49, tzinfo=timezone.utc),
        ),
    ]
    shared_event_date = "2026-06-13T12:00:00Z"
    metadata = {
        activity.slug: SimpleNamespace(
            slug=activity.slug,
            closed=False,
            outcome=None,
            raw_outcome=None,
            event={"endDate": shared_event_date},
            market={"question": activity.title},
        )
        for activity in activities
    }

    markets = {market.slug: market for market in build_market_performance(activities, market_metadata=metadata)}

    assert markets["xrp-0940"].market_date == datetime(2026, 6, 13, 13, 45, tzinfo=timezone.utc)
    assert markets["xrp-0945"].market_date == datetime(2026, 6, 13, 13, 50, tzinfo=timezone.utc)


def test_parse_metadata_date_handles_nested_state_snake_case() -> None:
    assert parse_metadata_date({"state": {"end_date": "2026-01-01T10:00:00Z"}}) == datetime(
        2026,
        1,
        1,
        10,
        tzinfo=timezone.utc,
    )
    assert parse_metadata_date({"state": {"event_start_time": "2026-01-02T08:00:00Z"}}) == datetime(
        2026,
        1,
        2,
        8,
        tzinfo=timezone.utc,
    )


def test_market_metadata_row_infers_winning_outcome_from_prices() -> None:
    row = market_metadata_row(
        "btc-up-down",
        {
            "slug": "btc-up-down",
            "closed": True,
            "outcomes": '["Up", "Down"]',
            "outcomePrices": '["0", "1"]',
            "events": [{"slug": "btc", "closed": True}],
        },
    )

    assert row["closed"] is True
    assert row["outcome"] == "down"
    assert row["raw_outcome"] == "Down"


def test_activity_resume_end_uses_second_before_oldest_timestamp() -> None:
    oldest = datetime(2026, 1, 1, 0, 0, 10, tzinfo=timezone.utc)

    assert routes_reports.activity_resume_end(oldest) == 1767225609


@pytest.mark.asyncio
async def test_fetch_activity_uses_parallel_io_windows_after_offset_limit() -> None:
    calls = []
    active_requests = 0
    max_active_requests = 0

    class FakePolymarketClient(PolymarketClient):
        async def fetch_activity_page(self, wallet, limit, offset, end=None, client=None):
            nonlocal active_requests, max_active_requests
            calls.append({"limit": limit, "offset": offset, "end": end})
            if offset >= 3000:
                raise AssertionError("historical offset limit exceeded")
            active_requests += 1
            max_active_requests = max(max_active_requests, active_requests)
            try:
                await asyncio.sleep(0.01)
                base = end if end is not None else 10_000 - offset
                return [
                    {
                        "proxyWallet": wallet,
                        "timestamp": base - index,
                        "conditionId": "0x" + "1" * 64,
                        "type": "TRADE",
                        "size": "1",
                        "usdcSize": "1",
                        "transactionHash": f"{offset}-{end}-{index}",
                        "price": "1",
                        "asset": "asset",
                        "side": "BUY",
                        "title": "BTC Up or Down",
                        "slug": "btc-up-down",
                        "eventSlug": "btc",
                        "outcome": "Up",
                    }
                    for index in range(limit)
                ]
            finally:
                active_requests -= 1

    client = FakePolymarketClient(data_base_url="https://example.com")
    activities = await client.fetch_activity("0x1111111111111111111111111111111111111111", 7600)

    assert len(activities) == 7600
    assert {call["limit"] for call in calls} == {200}
    assert max(call["offset"] for call in calls) == 2800
    assert [call["offset"] for call in calls[:15]] == [index * 200 for index in range(15)]
    assert max_active_requests > 1
    assert any(call["end"] is not None for call in calls)


@pytest.mark.asyncio
async def test_iter_activity_batches_starts_from_resume_end() -> None:
    calls = []

    class FakePolymarketClient(PolymarketClient):
        async def fetch_activity_page(self, wallet, limit, offset, end=None, client=None):
            calls.append({"limit": limit, "offset": offset, "end": end})
            return [
                {
                    "proxyWallet": wallet,
                    "timestamp": 1_000 - index,
                    "conditionId": "0x" + "1" * 64,
                    "type": "TRADE",
                    "size": "1",
                    "usdcSize": "1",
                    "transactionHash": f"{offset}-{end}-{index}",
                    "price": "1",
                    "asset": "asset",
                    "side": "BUY",
                    "title": "BTC Up or Down",
                    "slug": "btc-up-down",
                    "eventSlug": "btc",
                    "outcome": "Up",
                }
                for index in range(limit)
            ]

    client = FakePolymarketClient(data_base_url="https://example.com")
    batches = [
        batch
        async for batch in client.iter_activity_batches(
            "0x1111111111111111111111111111111111111111",
            100,
            end=12345,
        )
    ]

    assert len(batches) == 1
    assert len(batches[0]) == 100
    assert calls == [{"limit": 100, "offset": 0, "end": 12345}]


@pytest.mark.asyncio
async def test_upsert_activities_batches_writes(monkeypatch) -> None:
    batch_sizes = []

    class FakeSession:
        def __init__(self):
            self.commit_count = 0

        async def commit(self):
            self.commit_count += 1

    async def fake_upsert_activity_rows(session, rows):
        batch_sizes.append(len(rows))

    monkeypatch.setattr(report_store, "upsert_activity_rows", fake_upsert_activity_rows)

    session = FakeSession()
    activities = [make_normalized_activity(index) for index in range(2501)]
    saved_count = await report_store.upsert_activities(session, "0xabc", activities)

    assert saved_count == 2501
    assert batch_sizes == [1000, 1000, 501]
    assert session.commit_count == 3


@pytest.mark.asyncio
async def test_ensure_market_metadata_batches_upserts(monkeypatch) -> None:
    batch_sizes = []
    fetch_sizes = []

    async def fake_list_market_metadata(session, slugs):
        return {}

    async def fake_fetch_market_metadata_rows(slugs):
        fetch_sizes.append(len(slugs))
        return [{"slug": slug} for slug in slugs]

    async def fake_upsert_market_metadata_rows(session, rows):
        batch_sizes.append(len(rows))
        return len(rows)

    monkeypatch.setattr(market_metadata, "list_market_metadata", fake_list_market_metadata)
    monkeypatch.setattr(market_metadata, "fetch_market_metadata_rows", fake_fetch_market_metadata_rows)
    monkeypatch.setattr(market_metadata, "upsert_market_metadata_rows", fake_upsert_market_metadata_rows)

    await market_metadata.ensure_market_metadata_for_slugs(
        object(),
        {f"market-{index}" for index in range(250)},
    )

    assert fetch_sizes == [100, 100, 50]
    assert batch_sizes == [100, 100, 50]


@pytest.mark.asyncio
async def test_market_metadata_fetch_coalesces_in_flight_slug(monkeypatch) -> None:
    calls = 0

    async def fake_fetch_market_metadata_row(client, semaphore, slug):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return {"slug": slug}

    monkeypatch.setattr(market_metadata, "fetch_market_metadata_row", fake_fetch_market_metadata_row)

    left, right = await asyncio.gather(
        market_metadata.fetch_market_metadata_rows(["same-slug"]),
        market_metadata.fetch_market_metadata_rows(["same-slug"]),
    )

    assert calls == 1
    assert left == [{"slug": "same-slug"}]
    assert right == [{"slug": "same-slug"}]


def make_activity(
    activity_id: str,
    activity_type: str,
    side: str | None,
    outcome: str | None,
    usdc_size: str,
    recovery: str,
    size: str,
    title: str = "BTC Up or Down",
    timestamp: datetime | None = None,
    slug: str = "btc-up-down",
):
    amount = Decimal(recovery) if activity_type in {"SELL", "REDEEM", "MERGE"} else Decimal(usdc_size)
    if activity_type == "TRADE" and side == "SELL":
        amount = Decimal(recovery)
    return SimpleNamespace(
        id=activity_id,
        timestamp=timestamp or datetime(2026, 1, 1, tzinfo=timezone.utc),
        type=activity_type,
        condition_id="0x" + "1" * 64,
        slug=slug,
        event_slug="btc",
        title=title,
        side=side,
        outcome=outcome,
        asset="asset",
        price=Decimal("0.5"),
        size=Decimal(size),
        usdc_size=amount,
    )


def make_normalized_activity(index: int) -> NormalizedActivity:
    return NormalizedActivity(
        id=f"activity-{index}",
        proxy_wallet="0x1111111111111111111111111111111111111111",
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        type="TRADE",
        condition_id="0x" + "1" * 64,
        slug="btc-up-down",
        event_slug="btc",
        title="BTC Up or Down",
        side="BUY",
        outcome="Up",
        asset="asset",
        price=Decimal("0.5"),
        size=Decimal("1"),
        usdc_size=Decimal("1"),
        transaction_hash=f"0x{index}",
        raw={"index": index},
    )


def make_market_performance(market_id: str, title: str) -> MarketPerformance:
    return MarketPerformance(
        market_id=market_id,
        title=title,
        slug=market_id,
        condition_id="0x" + "1" * 64,
        event_slug=market_id,
        result="未结算",
        position_status="无持仓",
        activity_count=1,
        redeem_count=0,
        merge_count=0,
        market_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
        redeem_time=None,
        up_cost=0,
        up_shares=0,
        up_average_cost=None,
        down_cost=0,
        down_shares=0,
        down_average_cost=None,
        cost=0,
        recovery=0,
        merge_return=0,
        maker_rebate=0,
        pnl=0,
        pnl_with_rebate=0,
        roi=None,
        if_up_pnl=None,
        if_up_roi=None,
        if_down_pnl=None,
        if_down_roi=None,
        incomplete=False,
    )
