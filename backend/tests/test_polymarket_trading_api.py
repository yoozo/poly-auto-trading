from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.db.models import AppSetting, PolymarketCredential
from app.db.session import get_session
from app.main import create_app
from app.schemas.polymarket import PolymarketAccountPosition, PolymarketTradingRestriction
from app.services.polymarket_account_store import polymarket_account_store
from app.services.polymarket_credentials import (
    RuntimePolymarketCredentials,
    import_polymarket_credential,
    parse_import_payload,
)
from conftest import login_test_client

SIGNER = "0x0000000000000000000000000000000000000001"
FUNDER = "0x0000000000000000000000000000000000000002"


def test_credentials_list_returns_config_hint_without_encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "polymarket_credentials_encryption_key", "")
    app = create_app(enable_lifespan=False)
    client = TestClient(app)
    login_test_client(client)

    response = client.get("/api/polymarket/credentials")

    assert response.status_code == 200
    assert response.json() == {"active_id": None, "profiles": [], "encryption_configured": False}


def test_credentials_api_lists_activates_and_rejects_active_delete(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        settings,
        "polymarket_credentials_encryption_key",
        Fernet.generate_key().decode("utf-8"),
    )
    sessionmaker = make_sessionmaker()
    profile_id = seed_profile(sessionmaker)
    app = create_app(enable_lifespan=False)

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with sessionmaker() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    client = TestClient(app)
    login_test_client(client)

    list_response = client.get("/api/polymarket/credentials")
    update_response = client.patch(f"/api/polymarket/credentials/{profile_id}", json={"label": "Renamed"})
    activate_response = client.post(f"/api/polymarket/credentials/{profile_id}/activate")
    delete_response = client.delete(f"/api/polymarket/credentials/{profile_id}")

    assert list_response.status_code == 200
    assert list_response.json()["profiles"][0]["funder_address"] == FUNDER
    assert "secret" not in str(list_response.json())
    assert update_response.status_code == 200
    assert update_response.json()["profiles"][0]["label"] == "Renamed"
    assert "secret" not in str(update_response.json())
    assert activate_response.status_code == 200
    assert activate_response.json()["active_id"] == profile_id
    assert delete_response.status_code == 400
    assert "active" in delete_response.json()["detail"].lower()


def test_post_signed_order_submits_sanitized_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime_credentials = RuntimePolymarketCredentials(
        source="db",
        credential_id="profile-1",
        signer_address=SIGNER,
        funder_address=FUNDER,
        signature_type=3,
        api_key="api-key-owner",
        api_secret="api-secret",
        api_passphrase="api-pass",
    )
    submitted: dict = {}

    async def fake_resolve_runtime_credentials() -> RuntimePolymarketCredentials:
        return runtime_credentials

    async def fake_post_signed_order(self, **kwargs):  # noqa: ANN001
        submitted.update(kwargs)
        return {"success": True, "orderID": "0xorder", "status": "live", "errorMsg": ""}

    async def fake_fetch_trading_restriction(self):  # noqa: ANN001
        return PolymarketTradingRestriction(blocked=False, close_only=False, country="HK")

    async def fake_refresh() -> None:
        return None

    import app.api.routes_polymarket as routes_polymarket

    monkeypatch.setattr(routes_polymarket, "resolve_runtime_credentials", fake_resolve_runtime_credentials)
    monkeypatch.setattr(routes_polymarket.PolymarketClient, "post_signed_order", fake_post_signed_order)
    monkeypatch.setattr(routes_polymarket.PolymarketClient, "fetch_trading_restriction", fake_fetch_trading_restriction)
    monkeypatch.setattr(routes_polymarket, "refresh_account_state_after_order", fake_refresh)

    app = create_app(enable_lifespan=False)
    client = TestClient(app)
    login_test_client(client)
    signed_order = make_signed_order(side="BUY", maker_amount="500000", taker_amount="1000000")

    response = client.post(
        "/api/polymarket/orders/signed",
        json={
            "signed_order": signed_order,
            "token_id": "token-1",
            "side": "BUY",
            "price": 0.5,
            "size": 1,
            "order_type": "GTC",
            "post_only": True,
        },
    )

    assert response.status_code == 200
    assert response.json()["order_id"] == "0xorder"
    assert submitted["signed_order"] == signed_order
    assert submitted["credentials"] == runtime_credentials
    assert "api-secret" not in str(response.json())


def test_post_signed_market_order_skips_limit_price_size_equality(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime_credentials = RuntimePolymarketCredentials(
        source="db",
        credential_id="profile-1",
        signer_address=SIGNER,
        funder_address=FUNDER,
        signature_type=3,
        api_key="api-key-owner",
        api_secret="api-secret",
        api_passphrase="api-pass",
    )
    submitted: dict = {}

    async def fake_resolve_runtime_credentials() -> RuntimePolymarketCredentials:
        return runtime_credentials

    async def fake_post_signed_order(self, **kwargs):  # noqa: ANN001
        submitted.update(kwargs)
        return {"success": True, "orderID": "0xmarket", "status": "matched", "errorMsg": ""}

    async def fake_fetch_trading_restriction(self):  # noqa: ANN001
        return PolymarketTradingRestriction(blocked=False, close_only=False, country="HK")

    async def fake_refresh() -> None:
        return None

    import app.api.routes_polymarket as routes_polymarket

    monkeypatch.setattr(routes_polymarket, "resolve_runtime_credentials", fake_resolve_runtime_credentials)
    monkeypatch.setattr(routes_polymarket.PolymarketClient, "post_signed_order", fake_post_signed_order)
    monkeypatch.setattr(routes_polymarket.PolymarketClient, "fetch_trading_restriction", fake_fetch_trading_restriction)
    monkeypatch.setattr(routes_polymarket, "refresh_account_state_after_order", fake_refresh)

    app = create_app(enable_lifespan=False)
    client = TestClient(app)
    login_test_client(client)

    response = client.post(
        "/api/polymarket/orders/signed",
        json={
            "signed_order": make_signed_order(side="BUY", maker_amount="2500000", taker_amount="4550000"),
            "token_id": "token-1",
            "side": "BUY",
            "price": 0.01,
            "size": 1,
            "order_type": "FOK",
            "post_only": False,
        },
    )

    assert response.status_code == 200
    assert response.json()["order_id"] == "0xmarket"
    assert submitted["order_type"] == "FOK"
    assert submitted["post_only"] is False


def test_post_signed_order_http_error_does_not_echo_response_body(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime_credentials = RuntimePolymarketCredentials(
        source="db",
        credential_id="profile-1",
        signer_address=SIGNER,
        funder_address=FUNDER,
        signature_type=3,
        api_key="api-key-owner",
        api_secret="api-secret",
        api_passphrase="api-pass",
    )

    async def fake_resolve_runtime_credentials() -> RuntimePolymarketCredentials:
        return runtime_credentials

    async def fake_post_signed_order(self, **kwargs):  # noqa: ANN001
        request = httpx.Request("POST", "https://clob.polymarket.com/order")
        response = httpx.Response(
            400,
            request=request,
            text='{"error":"bad order","signature":"0xsensitive-signature","api_secret":"api-secret"}',
        )
        raise httpx.HTTPStatusError("bad request", request=request, response=response)

    async def fake_fetch_trading_restriction(self):  # noqa: ANN001
        return PolymarketTradingRestriction(blocked=False, close_only=False, country="HK")

    import app.api.routes_polymarket as routes_polymarket

    monkeypatch.setattr(routes_polymarket, "resolve_runtime_credentials", fake_resolve_runtime_credentials)
    monkeypatch.setattr(routes_polymarket.PolymarketClient, "post_signed_order", fake_post_signed_order)
    monkeypatch.setattr(routes_polymarket.PolymarketClient, "fetch_trading_restriction", fake_fetch_trading_restriction)

    app = create_app(enable_lifespan=False)
    client = TestClient(app)
    login_test_client(client)

    response = client.post(
        "/api/polymarket/orders/signed",
        json={
            "signed_order": make_signed_order(side="BUY", maker_amount="500000", taker_amount="1000000"),
            "token_id": "token-1",
            "side": "BUY",
            "price": 0.5,
            "size": 1,
        },
    )

    assert response.status_code == 502
    detail = response.json()["detail"]
    assert detail == "Polymarket 下单提交失败: HTTP 400 Bad Request: bad order"
    assert "0xsensitive-signature" not in detail
    assert "api-secret" not in detail


def test_post_signed_order_http_error_drops_sensitive_error_message(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime_credentials = RuntimePolymarketCredentials(
        source="db",
        credential_id="profile-1",
        signer_address=SIGNER,
        funder_address=FUNDER,
        signature_type=3,
        api_key="api-key-owner",
        api_secret="api-secret",
        api_passphrase="api-pass",
    )

    async def fake_resolve_runtime_credentials() -> RuntimePolymarketCredentials:
        return runtime_credentials

    async def fake_post_signed_order(self, **kwargs):  # noqa: ANN001
        request = httpx.Request("POST", "https://clob.polymarket.com/order")
        response = httpx.Response(
            400,
            request=request,
            json={"error": "invalid signature 0xsensitive-signature api_secret=api-secret"},
        )
        raise httpx.HTTPStatusError("bad request", request=request, response=response)

    async def fake_fetch_trading_restriction(self):  # noqa: ANN001
        return PolymarketTradingRestriction(blocked=False, close_only=False, country="HK")

    import app.api.routes_polymarket as routes_polymarket

    monkeypatch.setattr(routes_polymarket, "resolve_runtime_credentials", fake_resolve_runtime_credentials)
    monkeypatch.setattr(routes_polymarket.PolymarketClient, "post_signed_order", fake_post_signed_order)
    monkeypatch.setattr(routes_polymarket.PolymarketClient, "fetch_trading_restriction", fake_fetch_trading_restriction)

    app = create_app(enable_lifespan=False)
    client = TestClient(app)
    login_test_client(client)

    response = client.post(
        "/api/polymarket/orders/signed",
        json={
            "signed_order": make_signed_order(side="BUY", maker_amount="500000", taker_amount="1000000"),
            "token_id": "token-1",
            "side": "BUY",
            "price": 0.5,
            "size": 1,
        },
    )

    assert response.status_code == 502
    detail = response.json()["detail"]
    assert detail == "Polymarket 下单提交失败: HTTP 400 Bad Request"
    assert "0xsensitive-signature" not in detail
    assert "api-secret" not in detail


def test_post_signed_order_region_restricted_returns_403(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime_credentials = RuntimePolymarketCredentials(
        source="db",
        credential_id="profile-1",
        signer_address=SIGNER,
        funder_address=FUNDER,
        signature_type=3,
        api_key="api-key-owner",
        api_secret="api-secret",
        api_passphrase="api-pass",
    )
    asyncio.run(
        polymarket_account_store.replace_positions(
            [
                PolymarketAccountPosition(
                    condition_id="condition-1",
                    asset="token-1",
                    title=None,
                    slug=None,
                    event_slug=None,
                    outcome="Up",
                    size=1,
                    avg_price=None,
                    cur_price=None,
                    current_value=None,
                    cash_pnl=None,
                    percent_pnl=None,
                    redeemable=False,
                    mergeable=False,
                    end_date=None,
                    raw={},
                )
            ]
        )
    )

    async def fake_resolve_runtime_credentials() -> RuntimePolymarketCredentials:
        return runtime_credentials

    async def fake_post_signed_order(self, **kwargs):  # noqa: ANN001
        request = httpx.Request("POST", "https://clob.polymarket.com/order")
        response = httpx.Response(
            403,
            request=request,
            json={
                "error": "Trading restricted in your region, please refer to available regions - https://docs.polymarket.com/developers/CLOB/geoblock",
                "api_secret": "api-secret",
            },
        )
        raise httpx.HTTPStatusError("forbidden", request=request, response=response)

    async def fake_fetch_trading_restriction(self):  # noqa: ANN001
        return PolymarketTradingRestriction(blocked=True, close_only=True, country="SG")

    import app.api.routes_polymarket as routes_polymarket

    monkeypatch.setattr(routes_polymarket, "resolve_runtime_credentials", fake_resolve_runtime_credentials)
    monkeypatch.setattr(routes_polymarket.PolymarketClient, "post_signed_order", fake_post_signed_order)
    monkeypatch.setattr(routes_polymarket.PolymarketClient, "fetch_trading_restriction", fake_fetch_trading_restriction)

    app = create_app(enable_lifespan=False)
    client = TestClient(app)
    login_test_client(client)

    response = client.post(
        "/api/polymarket/orders/signed",
        json={
            "signed_order": make_signed_order(side="SELL", maker_amount="1000000", taker_amount="500000"),
            "token_id": "token-1",
            "side": "SELL",
            "price": 0.5,
            "size": 1,
        },
    )

    assert response.status_code == 403
    detail = response.json()["detail"]
    assert "Trading restricted in your region" in detail
    assert "api-secret" not in detail


def test_cancel_order_region_restricted_returns_403(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_cancel_order(self, order_id: str):  # noqa: ANN001
        request = httpx.Request("DELETE", "https://clob.polymarket.com/order")
        response = httpx.Response(
            403,
            request=request,
            json={
                "error": "Trading restricted in your region, please refer to available regions - https://docs.polymarket.com/developers/CLOB/geoblock",
                "api_secret": "api-secret",
            },
        )
        raise httpx.HTTPStatusError("forbidden", request=request, response=response)

    import app.api.routes_polymarket as routes_polymarket

    monkeypatch.setattr(routes_polymarket.PolymarketClient, "cancel_order", fake_cancel_order)

    app = create_app(enable_lifespan=False)
    client = TestClient(app)
    login_test_client(client)

    response = client.post("/api/polymarket/orders/0xorder/cancel")

    assert response.status_code == 403
    detail = response.json()["detail"]
    assert detail.startswith("Polymarket 撤单失败: HTTP 403 Forbidden")
    assert "Trading restricted in your region" in detail
    assert "api-secret" not in detail


def test_cancel_order_http_error_does_not_echo_response_body(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_cancel_order(self, order_id: str):  # noqa: ANN001
        request = httpx.Request("DELETE", "https://clob.polymarket.com/order")
        response = httpx.Response(
            404,
            request=request,
            json={
                "error": "order not found",
                "signature": "0xsensitive-signature",
                "api_secret": "api-secret",
            },
        )
        raise httpx.HTTPStatusError("not found", request=request, response=response)

    import app.api.routes_polymarket as routes_polymarket

    monkeypatch.setattr(routes_polymarket.PolymarketClient, "cancel_order", fake_cancel_order)

    app = create_app(enable_lifespan=False)
    client = TestClient(app)
    login_test_client(client)

    response = client.post("/api/polymarket/orders/0xorder/cancel")

    assert response.status_code == 502
    detail = response.json()["detail"]
    assert detail == "Polymarket 撤单失败: HTTP 404 Not Found: order not found"
    assert "0xsensitive-signature" not in detail
    assert "api-secret" not in detail


def test_cancel_order_http_error_drops_sensitive_error_message(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_cancel_order(self, order_id: str):  # noqa: ANN001
        request = httpx.Request("DELETE", "https://clob.polymarket.com/order")
        response = httpx.Response(
            400,
            request=request,
            json={"message": "cancel rejected: signature 0xsensitive-signature api_secret=api-secret"},
        )
        raise httpx.HTTPStatusError("bad request", request=request, response=response)

    import app.api.routes_polymarket as routes_polymarket

    monkeypatch.setattr(routes_polymarket.PolymarketClient, "cancel_order", fake_cancel_order)

    app = create_app(enable_lifespan=False)
    client = TestClient(app)
    login_test_client(client)

    response = client.post("/api/polymarket/orders/0xorder/cancel")

    assert response.status_code == 502
    detail = response.json()["detail"]
    assert detail == "Polymarket 撤单失败: HTTP 400 Bad Request"
    assert "0xsensitive-signature" not in detail
    assert "api-secret" not in detail


def test_post_signed_order_rejects_close_only_buy(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime_credentials = RuntimePolymarketCredentials(
        source="db",
        credential_id="profile-1",
        signer_address=SIGNER,
        funder_address=FUNDER,
        signature_type=3,
        api_key="api-key-owner",
        api_secret="api-secret",
        api_passphrase="api-pass",
    )

    async def fake_resolve_runtime_credentials() -> RuntimePolymarketCredentials:
        return runtime_credentials

    async def fake_fetch_trading_restriction(self):  # noqa: ANN001
        return PolymarketTradingRestriction(blocked=True, close_only=True, country="SG")

    import app.api.routes_polymarket as routes_polymarket

    monkeypatch.setattr(routes_polymarket, "resolve_runtime_credentials", fake_resolve_runtime_credentials)
    monkeypatch.setattr(routes_polymarket.PolymarketClient, "fetch_trading_restriction", fake_fetch_trading_restriction)
    app = create_app(enable_lifespan=False)
    client = TestClient(app)
    login_test_client(client)

    response = client.post(
        "/api/polymarket/orders/signed",
        json={
            "signed_order": make_signed_order(side="BUY", maker_amount="500000", taker_amount="1000000"),
            "token_id": "token-1",
            "side": "BUY",
            "price": 0.5,
            "size": 1,
        },
    )

    assert response.status_code == 400
    assert "close-only" in response.json()["detail"]


def test_post_signed_order_rejects_close_only_sell_above_position(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime_credentials = RuntimePolymarketCredentials(
        source="db",
        credential_id="profile-1",
        signer_address=SIGNER,
        funder_address=FUNDER,
        signature_type=3,
        api_key="api-key-owner",
        api_secret="api-secret",
        api_passphrase="api-pass",
    )
    asyncio.run(
        polymarket_account_store.replace_positions(
            [
                PolymarketAccountPosition(
                    condition_id="condition-1",
                    asset="token-1",
                    title=None,
                    slug=None,
                    event_slug=None,
                    outcome="Up",
                    size=0.5,
                    avg_price=None,
                    cur_price=None,
                    current_value=None,
                    cash_pnl=None,
                    percent_pnl=None,
                    redeemable=False,
                    mergeable=False,
                    end_date=None,
                    raw={},
                )
            ]
        )
    )

    async def fake_resolve_runtime_credentials() -> RuntimePolymarketCredentials:
        return runtime_credentials

    async def fake_fetch_trading_restriction(self):  # noqa: ANN001
        return PolymarketTradingRestriction(blocked=True, close_only=True, country="SG")

    import app.api.routes_polymarket as routes_polymarket

    monkeypatch.setattr(routes_polymarket, "resolve_runtime_credentials", fake_resolve_runtime_credentials)
    monkeypatch.setattr(routes_polymarket.PolymarketClient, "fetch_trading_restriction", fake_fetch_trading_restriction)
    app = create_app(enable_lifespan=False)
    client = TestClient(app)
    login_test_client(client)

    response = client.post(
        "/api/polymarket/orders/signed",
        json={
            "signed_order": make_signed_order(side="SELL", maker_amount="1000000", taker_amount="500000"),
            "token_id": "token-1",
            "side": "SELL",
            "price": 0.5,
            "size": 1,
        },
    )

    assert response.status_code == 400
    assert "SELL size" in response.json()["detail"]


def test_post_signed_market_sell_uses_signed_size_for_close_only(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime_credentials = RuntimePolymarketCredentials(
        source="db",
        credential_id="profile-1",
        signer_address=SIGNER,
        funder_address=FUNDER,
        signature_type=3,
        api_key="api-key-owner",
        api_secret="api-secret",
        api_passphrase="api-pass",
    )
    asyncio.run(
        polymarket_account_store.replace_positions(
            [
                PolymarketAccountPosition(
                    condition_id="condition-1",
                    asset="token-1",
                    title=None,
                    slug=None,
                    event_slug=None,
                    outcome="Up",
                    size=0.5,
                    avg_price=None,
                    cur_price=None,
                    current_value=None,
                    cash_pnl=None,
                    percent_pnl=None,
                    redeemable=False,
                    mergeable=False,
                    end_date=None,
                    raw={},
                )
            ]
        )
    )

    async def fake_resolve_runtime_credentials() -> RuntimePolymarketCredentials:
        return runtime_credentials

    async def fake_fetch_trading_restriction(self):  # noqa: ANN001
        return PolymarketTradingRestriction(blocked=True, close_only=True, country="SG")

    import app.api.routes_polymarket as routes_polymarket

    monkeypatch.setattr(routes_polymarket, "resolve_runtime_credentials", fake_resolve_runtime_credentials)
    monkeypatch.setattr(routes_polymarket.PolymarketClient, "fetch_trading_restriction", fake_fetch_trading_restriction)
    app = create_app(enable_lifespan=False)
    client = TestClient(app)
    login_test_client(client)

    response = client.post(
        "/api/polymarket/orders/signed",
        json={
            "signed_order": make_signed_order(side="SELL", maker_amount="1000000", taker_amount="500000"),
            "token_id": "token-1",
            "side": "SELL",
            "price": 0.5,
            "size": 0.01,
            "order_type": "FOK",
            "post_only": False,
        },
    )

    assert response.status_code == 400
    assert "SELL size" in response.json()["detail"]


def test_post_signed_order_rejects_mismatched_maker(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime_credentials = RuntimePolymarketCredentials(
        source="db",
        credential_id="profile-1",
        signer_address=SIGNER,
        funder_address=FUNDER,
        signature_type=3,
        api_key="api-key-owner",
        api_secret="api-secret",
        api_passphrase="api-pass",
    )

    async def fake_resolve_runtime_credentials() -> RuntimePolymarketCredentials:
        return runtime_credentials

    import app.api.routes_polymarket as routes_polymarket

    monkeypatch.setattr(routes_polymarket, "resolve_runtime_credentials", fake_resolve_runtime_credentials)
    app = create_app(enable_lifespan=False)
    client = TestClient(app)
    login_test_client(client)
    signed_order = make_signed_order(
        maker="0x0000000000000000000000000000000000000003",
        side="BUY",
        maker_amount="500000",
        taker_amount="1000000",
    )

    response = client.post(
        "/api/polymarket/orders/signed",
        json={
            "signed_order": signed_order,
            "token_id": "token-1",
            "side": "BUY",
            "price": 0.5,
            "size": 1,
        },
    )

    assert response.status_code == 400
    assert "maker" in response.json()["detail"]


@pytest.mark.asyncio
async def test_client_post_signed_order_uses_l2_owner_and_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime_credentials = RuntimePolymarketCredentials(
        source="db",
        credential_id="profile-1",
        signer_address=SIGNER,
        funder_address=FUNDER,
        signature_type=3,
        api_key="api-key-owner",
        api_secret="api-secret",
        api_passphrase="api-pass",
    )
    captured: dict = {}

    async def fake_clob_l2_request(self, method: str, endpoint: str, **kwargs):  # noqa: ANN001
        captured.update({"method": method, "endpoint": endpoint, **kwargs})
        return {"success": True, "orderID": "0xorder", "status": "live"}

    from app.services.polymarket_client import PolymarketClient

    monkeypatch.setattr(PolymarketClient, "_clob_l2_request", fake_clob_l2_request)
    signed_order = make_signed_order(side="BUY", maker_amount="500000", taker_amount="1000000")
    signed_order["salt"] = "123"

    raw = await PolymarketClient().post_signed_order(
        signed_order=signed_order,
        order_type="GTC",
        post_only=True,
        defer_exec=False,
        credentials=runtime_credentials,
    )

    assert raw["orderID"] == "0xorder"
    assert captured["method"] == "POST"
    assert captured["endpoint"] == "/order"
    assert captured["credentials"] == runtime_credentials
    wire_order = dict(signed_order)
    wire_order["salt"] = 123
    assert captured["body"] == {
        "order": wire_order,
        "owner": "api-key-owner",
        "orderType": "GTC",
        "deferExec": False,
        "postOnly": True,
    }


def make_sessionmaker() -> async_sessionmaker:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    async def init() -> async_sessionmaker:
        async with engine.begin() as connection:
            await connection.run_sync(AppSetting.__table__.create)
            await connection.run_sync(PolymarketCredential.__table__.create)
        return async_sessionmaker(engine, expire_on_commit=False)

    import asyncio

    return asyncio.run(init())


def seed_profile(sessionmaker: async_sessionmaker) -> str:
    async def seed() -> str:
        async with sessionmaker() as session:
            async with session.begin():
                profile = await import_polymarket_credential(
                    session,
                    parse_import_payload(
                        {
                            "label": "Main",
                            "signer_address": SIGNER,
                            "funder_address": FUNDER,
                            "signature_type": 3,
                            "api_key": "api-key-owner",
                            "api_secret": "api-secret",
                            "api_passphrase": "api-pass",
                        }
                    ),
                )
                return profile.id

    import asyncio

    return asyncio.run(seed())


def make_signed_order(
    *,
    maker: str = FUNDER,
    signer: str = FUNDER,
    side: str,
    maker_amount: str,
    taker_amount: str,
) -> dict:
    return {
        "salt": 123,
        "maker": maker,
        "signer": signer,
        "tokenId": "token-1",
        "makerAmount": maker_amount,
        "takerAmount": taker_amount,
        "side": side,
        "expiration": "0",
        "timestamp": "1735689600000",
        "metadata": "0x00",
        "builder": "0x0000000000000000000000000000000000000000000000000000000000000000",
        "signatureType": 3,
        "signature": "0xsig",
    }
