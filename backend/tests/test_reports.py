import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from app.api import routes_reports
from app.db.session import get_session
from app.main import create_app
from app.services.report_analysis import build_account_summary, build_market_performance
from app.services.polymarket_client import NormalizedActivity, PolymarketClient, normalize_polymarket_input
from app.services import report_store


def make_client() -> TestClient:
    app = create_app(enable_lifespan=False)

    async def fake_session():
        yield object()

    app.dependency_overrides[get_session] = fake_session
    return TestClient(app)


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
    activities = [make_normalized_activity(index) for index in range(501)]
    saved_count = await report_store.upsert_activities(session, "0xabc", activities)

    assert saved_count == 501
    assert batch_sizes == [501]
    assert session.commit_count == 1


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
):
    amount = Decimal(recovery) if activity_type in {"SELL", "REDEEM", "MERGE"} else Decimal(usdc_size)
    if activity_type == "TRADE" and side == "SELL":
        amount = Decimal(recovery)
    return SimpleNamespace(
        id=activity_id,
        timestamp=timestamp or datetime(2026, 1, 1, tzinfo=timezone.utc),
        type=activity_type,
        condition_id="0x" + "1" * 64,
        slug="btc-up-down",
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
