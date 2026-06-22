from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import create_app
from app.schemas.polymarket import (
    PolymarketAccountBalance,
    PolymarketAccountOrder,
    PolymarketAccountPosition,
    PolymarketAccountTrade,
    PolymarketTradingRestriction,
)
from app.services import polymarket_account_monitor
from app.services.polymarket_account_monitor import (
    PolymarketAccountMonitor,
    user_subscription_payload,
)
from app.services.polymarket_account_store import PolymarketAccountStore
from app.services.polymarket_client import (
    PolymarketClient,
    normalize_account_balance,
    normalize_account_order,
    normalize_account_position,
)
from app.services.polymarket_credentials import RuntimePolymarketCredentials
from conftest import login_test_client


def test_normalizes_position_and_order_rows() -> None:
    position = normalize_account_position(
        {
            "conditionId": "0xabc",
            "asset": "up-token",
            "title": "BTC Up or Down",
            "outcome": "Up",
            "size": "10.5",
            "avgPrice": "0.48",
            "curPrice": "0.56",
            "currentValue": "5.88",
            "cashPnl": "0.84",
            "percentPnl": "0.1667",
            "redeemable": True,
            "mergeable": False,
            "endDate": "2026-06-19T12:00:00Z",
        }
    )
    order = normalize_account_order(
        {
            "id": "order-1",
            "market": "0xabc",
            "asset_id": "up-token",
            "side": "BUY",
            "price": "0.52",
            "original_size": "20",
            "size_matched": "5",
            "order_type": "GTC",
            "status": "LIVE",
        }
    )

    assert position.condition_id == "0xabc"
    assert position.size == 10.5
    assert position.avg_price == 0.48
    assert position.redeemable is True
    assert order.remaining_size == 15
    assert order.status == "LIVE"


def test_normalizes_position_avg_price_fallbacks() -> None:
    average_price_position = normalize_account_position({"size": "4", "averagePrice": "0.31"})
    derived_position = normalize_account_position({"size": "3.45", "currentValue": "1.12", "cashPnl": "1.12"})
    cost_position = normalize_account_position({"size": "10", "initialValue": "4.2"})

    assert average_price_position.avg_price == 0.31
    assert derived_position.avg_price is None
    assert cost_position.avg_price == pytest.approx(0.42)


def test_normalizes_balance_allowance_base_units() -> None:
    balance = normalize_account_balance({"balance": "116050000", "allowance": "999000000"})
    small_balance = normalize_account_balance({"balance": "500000", "allowance": "500000"})

    assert balance.cash == 116.05
    assert balance.allowance == 999
    assert small_balance.cash == 0.5
    assert small_balance.allowance == 0.5


@pytest.mark.asyncio
async def test_store_filters_snapshot_by_condition_id() -> None:
    store = PolymarketAccountStore()
    await store.replace_positions(
        [
            make_position("0xabc", "up-token"),
            make_position("0xdef", "down-token"),
        ]
    )
    await store.replace_orders(
        [
            make_order("order-1", "0xabc", "up-token"),
            make_order("order-2", "0xdef", "down-token"),
        ]
    )
    await store.replace_balance(PolymarketAccountBalance(cash=12.34, allowance=100.0))

    snapshot = await store.snapshot("0xabc")

    assert [position.asset for position in snapshot.positions] == ["up-token"]
    assert [order.id for order in snapshot.orders] == ["order-1"]
    assert snapshot.balance
    assert snapshot.balance.cash == 12.34


@pytest.mark.asyncio
async def test_store_clears_account_snapshots_when_identity_changes() -> None:
    store = PolymarketAccountStore()
    await store.set_account_identity(
        wallet="0x0000000000000000000000000000000000000001",
        clob_address="0x0000000000000000000000000000000000000002",
    )
    await store.replace_positions([make_position("0xabc", "up-token")])
    await store.replace_orders([make_order("order-1", "0xabc", "up-token")])
    await store.replace_balance(PolymarketAccountBalance(cash=12.34, allowance=100.0))
    await store.apply_trade(make_trade("trade-1", "0xabc", "up-token"))
    await store.set_error("old account error")

    await store.set_account_identity(
        wallet="0x0000000000000000000000000000000000000003",
        clob_address="0x0000000000000000000000000000000000000004",
    )
    snapshot = await store.snapshot()

    assert snapshot.wallet == "0x0000000000000000000000000000000000000003"
    assert snapshot.clob_address == "0x0000000000000000000000000000000000000004"
    assert snapshot.positions == []
    assert snapshot.orders == []
    assert snapshot.balance is None
    assert snapshot.recent_trades == []
    assert snapshot.error is None


@pytest.mark.asyncio
async def test_store_suppresses_canceled_order_from_stale_rest_snapshot() -> None:
    store = PolymarketAccountStore()
    order = make_order("order-1", "0xabc", "up-token")
    await store.replace_orders([order])

    await store.suppress_canceled_orders(["order-1"])
    await store.replace_orders([order])
    await store.apply_order(order)

    snapshot = await store.snapshot("0xabc")
    assert snapshot.orders == []


@pytest.mark.asyncio
async def test_order_event_updates_store_and_trade_event_refreshes_snapshots(monkeypatch: pytest.MonkeyPatch) -> None:
    store = PolymarketAccountStore()
    broadcasts: list[str | None] = []
    monitor = PolymarketAccountMonitor()

    async def fake_refresh_account_snapshot() -> None:
        await store.replace_positions([make_position("0xabc", "up-token")])

    async def fake_broadcast_snapshot(condition_id: str | None) -> None:
        broadcasts.append(condition_id)

    async def fake_condition_ids() -> list[str]:
        return ["0xabc"]

    monkeypatch.setattr(polymarket_account_monitor, "polymarket_account_store", store)
    monkeypatch.setattr(monitor, "refresh_account_snapshot", fake_refresh_account_snapshot)
    monkeypatch.setattr(monitor, "broadcast_snapshot", fake_broadcast_snapshot)
    monkeypatch.setattr(polymarket_account_monitor.polymarket_up_down_store, "condition_ids", fake_condition_ids)

    await monitor.handle_raw_message(
        '{"event_type":"order","id":"order-1","market":"0xabc","asset_id":"up-token",'
        '"side":"BUY","price":"0.52","original_size":"20","size_matched":"0","status":"LIVE"}'
    )
    await monitor.handle_raw_message(
        '{"event_type":"trade","id":"trade-1","market":"0xabc","asset_id":"up-token",'
        '"side":"BUY","price":"0.52","size":"5","order_id":"order-1"}'
    )

    snapshot = await store.snapshot("0xabc")
    assert [order.id for order in snapshot.orders] == ["order-1"]
    assert [position.asset for position in snapshot.positions] == ["up-token"]
    assert [trade.id for trade in snapshot.recent_trades] == ["trade-1"]
    assert [trade.confirmation_status for trade in snapshot.recent_trades] == ["confirmed"]
    assert None in broadcasts
    assert "0xabc" in broadcasts


@pytest.mark.asyncio
async def test_trade_event_broadcasts_pending_before_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:
    store = PolymarketAccountStore()
    snapshots: list[tuple[str | None, str]] = []
    monitor = PolymarketAccountMonitor()

    async def fake_refresh_account_snapshot() -> str | None:
        return None

    async def fake_broadcast_snapshot(condition_id: str | None) -> None:
        snapshot = await store.snapshot(condition_id)
        if snapshot.recent_trades:
            snapshots.append((condition_id, snapshot.recent_trades[0].confirmation_status))

    async def fake_condition_ids() -> list[str]:
        return ["0xabc"]

    monkeypatch.setattr(polymarket_account_monitor, "polymarket_account_store", store)
    monkeypatch.setattr(monitor, "refresh_account_snapshot", fake_refresh_account_snapshot)
    monkeypatch.setattr(monitor, "broadcast_snapshot", fake_broadcast_snapshot)
    monkeypatch.setattr(polymarket_account_monitor.polymarket_up_down_store, "condition_ids", fake_condition_ids)

    await monitor.handle_raw_message(
        '{"event_type":"trade","id":"trade-1","market":"0xabc","asset_id":"up-token",'
        '"side":"BUY","price":"0.52","size":"5","order_id":"order-1"}'
    )

    assert ("0xabc", "pending") in snapshots
    assert ("0xabc", "confirmed") in snapshots


@pytest.mark.asyncio
async def test_user_ws_handles_nested_trade_data_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    store = PolymarketAccountStore()
    monitor = PolymarketAccountMonitor()

    async def fake_refresh_account_snapshot() -> str | None:
        return None

    async def fake_broadcast_snapshot(condition_id: str | None) -> None:
        return None

    async def fake_condition_ids() -> list[str]:
        return ["0xabc"]

    monkeypatch.setattr(polymarket_account_monitor, "polymarket_account_store", store)
    monkeypatch.setattr(monitor, "refresh_account_snapshot", fake_refresh_account_snapshot)
    monkeypatch.setattr(monitor, "broadcast_snapshot", fake_broadcast_snapshot)
    monkeypatch.setattr(polymarket_account_monitor.polymarket_up_down_store, "condition_ids", fake_condition_ids)

    await monitor.handle_raw_message(
        '{"type":"user","data":[{"event_type":"trade","id":"trade-1","market":"0xabc",'
        '"asset_id":"up-token","side":"BUY","price":"0.52","size":"5","order_id":"order-1"}]}'
    )

    snapshot = await store.snapshot("0xabc")
    assert [trade.id for trade in snapshot.recent_trades] == ["trade-1"]
    assert [trade.confirmation_status for trade in snapshot.recent_trades] == ["confirmed"]


@pytest.mark.asyncio
async def test_trade_event_marks_refresh_failed_when_snapshot_refresh_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    store = PolymarketAccountStore()
    monitor = PolymarketAccountMonitor()

    async def fake_refresh_account_snapshot() -> str | None:
        return "Polymarket account fetch partially failed: balance: RuntimeError"

    async def fake_broadcast_snapshot(condition_id: str | None) -> None:
        return None

    async def fake_condition_ids() -> list[str]:
        return ["0xabc"]

    monkeypatch.setattr(polymarket_account_monitor, "polymarket_account_store", store)
    monkeypatch.setattr(monitor, "refresh_account_snapshot", fake_refresh_account_snapshot)
    monkeypatch.setattr(monitor, "broadcast_snapshot", fake_broadcast_snapshot)
    monkeypatch.setattr(polymarket_account_monitor.polymarket_up_down_store, "condition_ids", fake_condition_ids)

    await monitor.handle_raw_message(
        '{"event_type":"trade","id":"trade-1","market":"0xabc","asset_id":"up-token",'
        '"side":"BUY","price":"0.52","size":"5","order_id":"order-1"}'
    )

    snapshot = await store.snapshot("0xabc")
    assert [trade.confirmation_status for trade in snapshot.recent_trades] == ["refresh_failed"]


@pytest.mark.asyncio
async def test_store_dedupes_repeated_trade_events() -> None:
    store = PolymarketAccountStore()

    await store.apply_trade(make_trade("trade-1", "0xabc", "up-token"))
    await store.apply_trade(make_trade("trade-1", "0xabc", "up-token"))

    snapshot = await store.snapshot("0xabc")
    assert [trade.id for trade in snapshot.recent_trades] == ["trade-1"]
    assert snapshot.recent_trades[0].confirmation_status == "pending"


@pytest.mark.asyncio
async def test_store_filters_trade_by_asset_when_trade_market_is_missing() -> None:
    store = PolymarketAccountStore()
    await store.replace_positions([make_position("0xabc", "up-token")])
    await store.apply_trade(make_trade("trade-1", None, "up-token"))

    matching_snapshot = await store.snapshot("0xabc")
    other_snapshot = await store.snapshot("0xdef")

    assert [trade.id for trade in matching_snapshot.recent_trades] == ["trade-1"]
    assert other_snapshot.recent_trades == []


@pytest.mark.asyncio
async def test_refresh_account_snapshot_fetches_independent_sources_concurrently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = PolymarketAccountStore()
    monitor = PolymarketAccountMonitor()
    started: set[str] = set()
    release = asyncio.Event()

    class FakeClient:
        async def fetch_trading_restriction(self):
            started.add("restriction")
            await wait_until_all_started()
            return PolymarketTradingRestriction(blocked=False, close_only=False, country="HK")

        async def fetch_positions(self, *, wallet: str, size_threshold: int):
            started.add("positions")
            await wait_until_all_started()
            release.set()
            return [make_position("0xabc", "up-token")]

        async def fetch_balance_allowance(self, *, credentials: RuntimePolymarketCredentials):
            started.add("balance")
            await wait_until_all_started()
            raise RuntimeError("slow balance")

        async def fetch_open_orders(self, *, credentials: RuntimePolymarketCredentials):
            started.add("orders")
            await wait_until_all_started()
            return [make_order("order-1", "0xabc", "up-token")]

    async def wait_until_all_started() -> None:
        for _ in range(20):
            if started == {"restriction", "positions", "balance", "orders"}:
                return
            await asyncio.sleep(0)
        raise AssertionError(f"not all fetches started: {started}")

    runtime_credentials = make_runtime_credentials()

    async def fake_resolve_runtime_credentials() -> RuntimePolymarketCredentials:
        return runtime_credentials

    monitor._client = FakeClient()  # type: ignore[assignment]
    monkeypatch.setattr(polymarket_account_monitor, "polymarket_account_store", store)
    monkeypatch.setattr(
        polymarket_account_monitor,
        "resolve_runtime_credentials",
        fake_resolve_runtime_credentials,
    )

    await monitor.refresh_account_snapshot()
    snapshot = await store.snapshot("0xabc")

    assert [position.asset for position in snapshot.positions] == ["up-token"]
    assert [order.id for order in snapshot.orders] == ["order-1"]
    assert snapshot.error == "Polymarket account fetch partially failed: balance: RuntimeError"
    assert "secret" not in snapshot.error


@pytest.mark.asyncio
async def test_refresh_account_snapshot_uses_runtime_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = PolymarketAccountStore()
    monitor = PolymarketAccountMonitor()
    runtime_credentials = RuntimePolymarketCredentials(
        source="db",
        credential_id="profile-1",
        signer_address="0x0000000000000000000000000000000000000001",
        funder_address="0x0000000000000000000000000000000000000002",
        signature_type=3,
        api_key="key",
        api_secret="secret",
        api_passphrase="pass",
    )
    calls: list[tuple[str, object]] = []

    class FakeClient:
        async def fetch_trading_restriction(self):
            calls.append(("restriction", "geoblock"))
            return PolymarketTradingRestriction(blocked=False, close_only=False, country="HK")

        async def fetch_positions(self, *, wallet: str, size_threshold: int):
            calls.append(("positions", wallet))
            return [make_position("0xabc", "up-token")]

        async def fetch_balance_allowance(self, *, credentials: RuntimePolymarketCredentials):
            calls.append(("balance", credentials))
            return PolymarketAccountBalance(cash=1, allowance=2)

        async def fetch_open_orders(self, *, credentials: RuntimePolymarketCredentials):
            calls.append(("orders", credentials))
            return [make_order("order-1", "0xabc", "up-token")]

    async def fake_resolve_runtime_credentials() -> RuntimePolymarketCredentials:
        return runtime_credentials

    monitor._client = FakeClient()  # type: ignore[assignment]
    monkeypatch.setattr(polymarket_account_monitor, "polymarket_account_store", store)
    monkeypatch.setattr(
        polymarket_account_monitor,
        "resolve_runtime_credentials",
        fake_resolve_runtime_credentials,
    )

    await monitor.refresh_account_snapshot()
    snapshot = await store.snapshot("0xabc")

    assert ("positions", runtime_credentials.funder_address) in calls
    assert ("balance", runtime_credentials) in calls
    assert ("orders", runtime_credentials) in calls
    assert ("restriction", "geoblock") in calls
    assert snapshot.wallet == runtime_credentials.funder_address
    assert snapshot.clob_address == runtime_credentials.signer_address


@pytest.mark.asyncio
async def test_user_ws_ignores_non_json_frames(monkeypatch: pytest.MonkeyPatch) -> None:
    monitor = PolymarketAccountMonitor()
    broadcasts: list[str | None] = []

    async def fake_broadcast_snapshot(condition_id: str | None) -> None:
        broadcasts.append(condition_id)

    monkeypatch.setattr(monitor, "broadcast_snapshot", fake_broadcast_snapshot)

    await monitor.handle_raw_message("")
    await monitor.handle_raw_message("PONG")
    await monitor.handle_raw_message("connected")

    assert broadcasts == []


@pytest.mark.asyncio
async def test_start_marks_user_ws_config_missing_without_clob_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    store = PolymarketAccountStore()
    states: list[tuple[str, str | None]] = []
    monitor = PolymarketAccountMonitor()

    async def fake_snapshot_loop() -> None:
        return None

    async def fake_subscription_watch_loop() -> None:
        return None

    async def fake_set_ws_state(state: str, error: str | None = None) -> None:
        states.append((state, error))
        await store.set_ws_state(state, error)

    monkeypatch.setattr(settings, "polymarket_user_ws_enabled", True)
    monkeypatch.setattr(settings, "polymarket_credentials_encryption_key", "")
    monkeypatch.setattr(monitor, "snapshot_loop", fake_snapshot_loop)
    monkeypatch.setattr(monitor, "subscription_watch_loop", fake_subscription_watch_loop)
    monkeypatch.setattr(monitor, "set_ws_state", fake_set_ws_state)

    await monitor.start()
    await monitor.stop()

    assert states[0][0] == "config_missing"
    assert "credentials" in (states[0][1] or "")


def test_user_subscription_payload_uses_condition_ids_and_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime_credentials = make_runtime_credentials()

    payload = user_subscription_payload(
        ["0xdef", "0xabc", "0xabc"],
        credentials=runtime_credentials,
        operation="subscribe",
    )

    assert payload["type"] == "user"
    assert payload["operation"] == "subscribe"
    assert payload["markets"] == ["0xabc", "0xdef"]
    assert payload["auth"] == {"apiKey": "key", "secret": "secret", "passphrase": "pass"}


@pytest.mark.asyncio
async def test_open_orders_uses_l2_credentials_without_signing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_clob_l2_request(self, method: str, endpoint: str, **kwargs):
        assert method == "GET"
        assert endpoint == "/data/orders"
        assert kwargs["params"]["next_cursor"] == "MA=="
        return {
            "data": [{"id": "order-1", "market": "0xabc", "asset_id": "up-token", "status": "LIVE"}],
            "next_cursor": "LTE=",
        }

    monkeypatch.setattr(PolymarketClient, "_clob_l2_request", fake_clob_l2_request)

    orders = await PolymarketClient().fetch_open_orders()

    assert orders[0].id == "order-1"


@pytest.mark.asyncio
async def test_balance_allowance_uses_l2_credentials_without_signing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_clob_l2_request(self, method: str, endpoint: str, **kwargs):
        assert method == "GET"
        assert endpoint == "/balance-allowance"
        assert kwargs["params"] == {"asset_type": "COLLATERAL", "signature_type": 3}
        return {"balance": "116050000", "allowance": "999000000"}

    monkeypatch.setattr(PolymarketClient, "_clob_l2_request", fake_clob_l2_request)

    balance = await PolymarketClient().fetch_balance_allowance(credentials=make_runtime_credentials())

    assert balance.cash == 116.05
    assert balance.allowance == 999


def test_account_state_endpoint_returns_filtered_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    store = PolymarketAccountStore()

    async def fake_snapshot(condition_id: str | None = None):
        await store.replace_positions([make_position("0xabc", "up-token")])
        await store.replace_orders([make_order("order-1", "0xabc", "up-token")])
        return await store.snapshot(condition_id)

    monkeypatch.setattr(polymarket_account_monitor.polymarket_account_store, "snapshot", fake_snapshot)
    import app.api.routes_polymarket as routes_polymarket

    monkeypatch.setattr(routes_polymarket.polymarket_account_store, "snapshot", fake_snapshot)

    app = create_app(enable_lifespan=False)
    client = TestClient(app)
    login_test_client(client)
    response = client.get("/api/polymarket/account-state/0xabc")

    assert response.status_code == 200
    body = response.json()
    assert body["condition_id"] == "0xabc"
    assert body["positions"][0]["asset"] == "up-token"
    assert body["orders"][0]["id"] == "order-1"


def test_cancel_order_endpoint_cancels_order(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    suppressed: list[list[str]] = []

    async def fake_cancel_order(self, order_id: str):  # noqa: ANN001
        calls.append(order_id)
        return {"canceled": [order_id], "not_canceled": {}}

    async def fake_suppress_canceled_orders(order_ids: list[str]) -> None:
        suppressed.append(order_ids)

    async def fake_refresh() -> None:
        return None

    import app.api.routes_polymarket as routes_polymarket

    monkeypatch.setattr(routes_polymarket.PolymarketClient, "cancel_order", fake_cancel_order)
    monkeypatch.setattr(routes_polymarket.polymarket_account_store, "suppress_canceled_orders", fake_suppress_canceled_orders)
    monkeypatch.setattr(routes_polymarket, "refresh_account_state_after_order", fake_refresh)

    app = create_app(enable_lifespan=False)
    client = TestClient(app)
    login_test_client(client)
    response = client.post("/api/polymarket/orders/order-123/cancel")

    assert response.status_code == 200
    assert response.json()["canceled"] == ["order-123"]
    assert calls == ["order-123"]
    assert suppressed == [["order-123"]]


def make_position(condition_id: str, asset: str) -> PolymarketAccountPosition:
    return PolymarketAccountPosition(
        condition_id=condition_id,
        asset=asset,
        title="BTC Up or Down",
        slug="btc-updown",
        event_slug="btc-updown-event",
        outcome="Up",
        size=10,
        avg_price=0.48,
        cur_price=0.56,
        current_value=5.6,
        cash_pnl=0.8,
        percent_pnl=0.16,
        redeemable=False,
        mergeable=False,
        end_date=datetime(2026, 6, 19, tzinfo=timezone.utc),
        raw={},
    )


def make_order(order_id: str, condition_id: str, asset: str) -> PolymarketAccountOrder:
    return PolymarketAccountOrder(
        id=order_id,
        market=condition_id,
        asset_id=asset,
        side="BUY",
        price=0.52,
        original_size=20,
        size_matched=0,
        remaining_size=20,
        order_type="GTC",
        status="LIVE",
        outcome="Up",
        created_at=None,
        updated_at=None,
        raw={},
    )


def make_trade(trade_id: str, condition_id: str | None, asset: str) -> PolymarketAccountTrade:
    return PolymarketAccountTrade(
        id=trade_id,
        market=condition_id,
        asset_id=asset,
        side="BUY",
        price=0.52,
        size=5,
        outcome="Up",
        timestamp=datetime(2026, 6, 19, tzinfo=timezone.utc),
        order_id="order-1",
        raw={},
    )


def make_runtime_credentials() -> RuntimePolymarketCredentials:
    return RuntimePolymarketCredentials(
        source="db",
        credential_id="profile-1",
        signer_address="0x0000000000000000000000000000000000000001",
        funder_address="0x0000000000000000000000000000000000000002",
        signature_type=3,
        api_key="key",
        api_secret="secret",
        api_passphrase="pass",
    )
