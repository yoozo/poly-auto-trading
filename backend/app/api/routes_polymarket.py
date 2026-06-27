import logging
import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Response, WebSocket, WebSocketDisconnect
from fastapi.encoders import jsonable_encoder
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_websocket_auth
from app.db.session import get_session
from app.schemas.polymarket import (
    PolymarketAccountState,
    PolymarketCancelOrderResponse,
    PolymarketCredentialListResponse,
    PolymarketCredentialProfile,
    PolymarketCredentialUpdateRequest,
    PolymarketSignedOrderRequest,
    PolymarketSignedOrderResponse,
    PolymarketUpDownMarket,
)
from app.services.polymarket_account_monitor import polymarket_account_monitor
from app.services.polymarket_client import PolymarketClient, PolymarketInputError
from app.services.polymarket_account_store import polymarket_account_store
from app.services.polymarket_account_ws_hub import polymarket_account_ws_hub
from app.services.polymarket_credentials import (
    PolymarketCredentialError,
    RuntimePolymarketCredentials,
    credentials_encryption_configured,
    delete_credential_profile,
    get_active_credential_id,
    list_credential_profiles,
    resolve_runtime_credentials,
    set_active_credential_id,
    update_credential_label,
)
from app.services.polymarket_market_store import polymarket_up_down_store
from app.services.polymarket_ws_hub import polymarket_ws_hub

router = APIRouter(tags=["polymarket"])
logger = logging.getLogger(__name__)
POLYMARKET_BTC_UP_DOWN_INTERVALS = {"5m", "15m", "1h", "4h"}
POLYMARKET_BTC_UP_DOWN_LIST_LIMIT = 12
POLYMARKET_BTC_UP_DOWN_INCLUDE_RECENT_CLOSED = True


@dataclass(frozen=True)
class BtcUpDownMarketSubscribeMessage:
    interval: str
    market_id: str


@router.get("/polymarket/account-state", response_model=PolymarketAccountState)
async def account_state() -> PolymarketAccountState:
    return await polymarket_account_store.snapshot()


@router.post("/polymarket/account-state/refresh", response_model=PolymarketAccountState)
async def refresh_account_state() -> PolymarketAccountState:
    await refresh_account_state_after_order()
    return await polymarket_account_store.snapshot()


@router.get("/polymarket/credentials", response_model=PolymarketCredentialListResponse)
async def polymarket_credentials(
    session: AsyncSession = Depends(get_session),
) -> PolymarketCredentialListResponse:
    if not credentials_encryption_configured():
        return PolymarketCredentialListResponse(
            active_id=None,
            profiles=[],
            encryption_configured=False,
        )
    try:
        active_id = await get_active_credential_id(session)
        profiles = await list_credential_profiles(session)
        return PolymarketCredentialListResponse(
            active_id=active_id,
            profiles=[PolymarketCredentialProfile(**profile.__dict__) for profile in profiles],
            encryption_configured=True,
        )
    except PolymarketCredentialError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/polymarket/credentials/{credential_id}/activate", response_model=PolymarketCredentialListResponse)
async def activate_polymarket_credential(
    credential_id: str,
    session: AsyncSession = Depends(get_session),
) -> PolymarketCredentialListResponse:
    if not credentials_encryption_configured():
        raise HTTPException(status_code=400, detail="POLYMARKET_CREDENTIALS_ENCRYPTION_KEY is not configured")
    try:
        await set_active_credential_id(session, credential_id)
        await session.commit()
        polymarket_account_monitor.notify_credentials_changed()
        await refresh_account_state_after_order()
        return await polymarket_credentials(session)
    except PolymarketCredentialError as exc:
        await session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/polymarket/credentials/{credential_id}", response_model=PolymarketCredentialListResponse)
async def update_polymarket_credential(
    credential_id: str,
    request: PolymarketCredentialUpdateRequest,
    session: AsyncSession = Depends(get_session),
) -> PolymarketCredentialListResponse:
    if not credentials_encryption_configured():
        raise HTTPException(status_code=400, detail="POLYMARKET_CREDENTIALS_ENCRYPTION_KEY is not configured")
    try:
        await update_credential_label(session, credential_id, request.label)
        await session.commit()
        return await polymarket_credentials(session)
    except PolymarketCredentialError as exc:
        await session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/polymarket/credentials/{credential_id}", status_code=204)
async def delete_polymarket_credential(
    credential_id: str,
    session: AsyncSession = Depends(get_session),
) -> Response:
    if not credentials_encryption_configured():
        raise HTTPException(status_code=400, detail="POLYMARKET_CREDENTIALS_ENCRYPTION_KEY is not configured")
    try:
        await delete_credential_profile(session, credential_id)
        await session.commit()
        return Response(status_code=204)
    except PolymarketCredentialError as exc:
        await session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/polymarket/orders/{order_id}/cancel", response_model=PolymarketCancelOrderResponse)
async def cancel_polymarket_order(order_id: str) -> PolymarketCancelOrderResponse:
    try:
        raw = await PolymarketClient().cancel_order(order_id)
        canceled = raw.get("canceled") if isinstance(raw.get("canceled"), list) else []
        canceled_order_ids = [str(item) for item in canceled]
        await polymarket_account_store.suppress_canceled_orders(canceled_order_ids)
        await refresh_account_state_after_order()
        not_canceled = raw.get("not_canceled") if isinstance(raw.get("not_canceled"), dict) else {}
        return PolymarketCancelOrderResponse(
            canceled=canceled_order_ids,
            not_canceled=not_canceled,
            raw=raw,
        )
    except PolymarketInputError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.warning("Failed to cancel Polymarket order", extra={"order_id": order_id}, exc_info=exc)
        raise HTTPException(status_code=502, detail=f"Polymarket 撤单失败: {exc}") from exc


@router.post("/polymarket/orders/signed", response_model=PolymarketSignedOrderResponse)
async def post_signed_polymarket_order(
    request: PolymarketSignedOrderRequest,
) -> PolymarketSignedOrderResponse:
    try:
        credentials = await resolve_runtime_credentials()
        if credentials is None:
            raise PolymarketInputError("Polymarket CLOB API credentials are not configured")
        client = PolymarketClient()
        validate_signed_order_request(request, credentials)
        await validate_trading_restriction(request, await client.fetch_trading_restriction())
        raw = await client.post_signed_order(
            signed_order=request.signed_order,
            order_type=request.order_type,
            post_only=request.post_only,
            defer_exec=request.defer_exec,
            credentials=credentials,
        )
        await refresh_account_state_after_order()
        return PolymarketSignedOrderResponse(
            success=bool(raw.get("success")) if "success" in raw else None,
            order_id=str(raw.get("orderID") or raw.get("order_id") or "") or None,
            status=str(raw.get("status") or "") or None,
            raw=raw,
        )
    except PolymarketInputError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        detail = polymarket_http_error_detail("Polymarket 下单提交失败", exc)
        logger.warning("Failed to post signed Polymarket order: %s", detail, exc_info=exc)
        raise HTTPException(status_code=502, detail=detail) from exc
    except Exception as exc:
        logger.warning("Failed to post signed Polymarket order", exc_info=exc)
        raise HTTPException(status_code=502, detail=f"Polymarket 下单提交失败: {type(exc).__name__}") from exc


@router.websocket("/ws/polymarket/btc-up-down")
async def btc_up_down_websocket(
    websocket: WebSocket,
    interval: str = Query("5m", pattern="^(5m|15m|1h|4h)$"),
) -> None:
    if not await require_websocket_auth(websocket):
        return
    current_interval = interval
    await polymarket_ws_hub.connect(websocket, current_interval)
    try:
        await send_btc_up_down_current_market_snapshot(websocket, current_interval)
        await send_btc_up_down_markets_snapshot(websocket, current_interval)
        while True:
            raw_message = await websocket.receive_text()
            # 浏览器不能发 WebSocket 协议层 ping，这里提供应用层 ping/pong 供前端测后端回包 RTT。
            if await send_btc_up_down_pong(websocket, raw_message):
                continue
            market_subscription = parse_btc_up_down_market_subscribe_message(raw_message)
            if market_subscription is not None:
                previous_active_markets = await polymarket_ws_hub.active_market_ids()
                market = await polymarket_up_down_store.get_market_in_interval(
                    market_subscription.interval,
                    market_subscription.market_id,
                )
                if market is None:
                    logger.debug(
                        "Ignoring stale Polymarket market subscription",
                        extra={"interval": market_subscription.interval, "market_id": market_subscription.market_id},
                    )
                    continue
                await polymarket_ws_hub.replace_market_subscription(
                    websocket,
                    market_subscription.interval,
                    market_subscription.market_id,
                )
                current_interval = market_subscription.interval
                await notify_polymarket_market_subscription_changed(previous_active_markets)
                await send_btc_up_down_market_snapshot(websocket, market_subscription.interval, market)
                continue
            next_interval = parse_btc_up_down_subscribe_message(raw_message)
            if next_interval is None:
                continue
            if next_interval == current_interval:
                await send_btc_up_down_current_market_snapshot(websocket, current_interval)
                await send_btc_up_down_markets_snapshot(websocket, current_interval)
                continue
            previous_active_markets = await polymarket_ws_hub.active_market_ids()
            # interval 切换只影响列表视图；单 market 盘口订阅由前端选中 market 后显式发起。
            await polymarket_ws_hub.replace_interval_subscription(websocket, current_interval, next_interval)
            current_interval = next_interval
            await notify_polymarket_market_subscription_changed(previous_active_markets)
            await send_btc_up_down_current_market_snapshot(websocket, current_interval)
            await send_btc_up_down_markets_snapshot(websocket, current_interval)
    except WebSocketDisconnect:
        previous_active_markets = await polymarket_ws_hub.active_market_ids()
        await polymarket_ws_hub.disconnect(websocket, current_interval)
        await notify_polymarket_market_subscription_changed(previous_active_markets)


def parse_btc_up_down_subscribe_message(raw_message: str) -> str | None:
    try:
        payload = json.loads(raw_message)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or payload.get("type") != "polymarket.btc_up_down.subscribe":
        return None
    interval = payload.get("interval")
    return interval if isinstance(interval, str) and interval in POLYMARKET_BTC_UP_DOWN_INTERVALS else None


def parse_btc_up_down_market_subscribe_message(raw_message: str) -> BtcUpDownMarketSubscribeMessage | None:
    try:
        payload = json.loads(raw_message)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or payload.get("type") != "polymarket.btc_up_down.market.subscribe":
        return None
    interval = payload.get("interval")
    market_id = payload.get("market_id")
    if not isinstance(interval, str) or interval not in POLYMARKET_BTC_UP_DOWN_INTERVALS:
        return None
    if not isinstance(market_id, str) or not market_id.strip():
        return None
    return BtcUpDownMarketSubscribeMessage(interval=interval, market_id=market_id.strip())


async def send_btc_up_down_pong(websocket: WebSocket, raw_message: str) -> bool:
    try:
        payload = json.loads(raw_message)
    except json.JSONDecodeError:
        return False
    if not isinstance(payload, dict) or payload.get("type") != "polymarket.btc_up_down.ping":
        return False
    await websocket.send_json(
        {
            "type": "polymarket.btc_up_down.pong",
            "request_id": payload.get("request_id"),
        }
    )
    return True


async def send_btc_up_down_markets_snapshot(websocket: WebSocket, interval: str) -> None:
    try:
        markets = await ensure_btc_up_down_markets(interval)
    except Exception as exc:
        logger.warning("Failed to prepare Polymarket BTC up/down markets snapshot", exc_info=exc)
        await send_btc_up_down_error(websocket, f"Polymarket 数据获取失败: {exc}")
        return
    await websocket.send_json(
        {
            "type": "polymarket.btc_up_down.markets.snapshot",
            "interval": interval,
            "markets": jsonable_encoder(markets),
        }
    )


async def send_btc_up_down_snapshot(websocket: WebSocket, interval: str) -> None:
    await send_btc_up_down_markets_snapshot(websocket, interval)


async def send_btc_up_down_current_market_snapshot(websocket: WebSocket, interval: str) -> None:
    # 当前 market 已由 monitor 基础订阅预热；先发它可以让前端切 interval 时不用等待完整列表。
    market = await polymarket_up_down_store.current_market(interval)
    if market is None:
        return
    await send_btc_up_down_market_snapshot(websocket, interval, market)


async def send_btc_up_down_market_snapshot(websocket: WebSocket, interval: str, market: PolymarketUpDownMarket) -> None:
    await websocket.send_json(
        {
            "type": "polymarket.btc_up_down.market.snapshot",
            "interval": interval,
            "market": jsonable_encoder(market),
        }
    )


async def send_btc_up_down_error(websocket: WebSocket, message: str) -> None:
    await websocket.send_json({"type": "polymarket.btc_up_down.error", "message": message})


async def ensure_btc_up_down_markets(interval: str) -> list[PolymarketUpDownMarket]:
    cached = await polymarket_up_down_store.list_markets(interval, limit=POLYMARKET_BTC_UP_DOWN_LIST_LIMIT)
    if cached:
        return cached
    markets = await PolymarketClient().fetch_btc_up_down_markets(
        interval=interval,
        limit=POLYMARKET_BTC_UP_DOWN_LIST_LIMIT,
        include_recent_closed=POLYMARKET_BTC_UP_DOWN_INCLUDE_RECENT_CLOSED,
    )
    await polymarket_up_down_store.replace_markets(interval, markets)
    return await polymarket_up_down_store.list_markets(interval, limit=POLYMARKET_BTC_UP_DOWN_LIST_LIMIT)


async def notify_polymarket_market_subscription_changed(previous_active_markets: set[str]) -> None:
    if await polymarket_ws_hub.active_market_ids() == previous_active_markets:
        return
    # 前端只订阅单个 market，但上游 WS 订阅的是所有活跃 market 的 token 并集；变化时触发 monitor 重连。
    from app.services.polymarket_monitor import polymarket_market_monitor

    polymarket_market_monitor.notify_token_subscription_changed()


@router.websocket("/ws/polymarket/account-state")
async def account_state_websocket(
    websocket: WebSocket,
    condition_id: str | None = Query(None),
) -> None:
    if not await require_websocket_auth(websocket):
        return
    await polymarket_account_ws_hub.connect(websocket, condition_id)
    try:
        state = await polymarket_account_store.snapshot(condition_id)
        await websocket.send_json(
            {
                "type": "polymarket.account_state.snapshot",
                "condition_id": condition_id,
                "state": jsonable_encoder(state),
            }
        )
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await polymarket_account_ws_hub.disconnect(websocket, condition_id)


async def refresh_account_state_after_order() -> None:
    try:
        await polymarket_account_monitor.refresh_account_snapshot()
        await polymarket_account_monitor.broadcast_all_snapshots()
    except Exception as exc:
        logger.warning("Polymarket account-state refresh after order operation failed", exc_info=exc)


def validate_signed_order_request(
    request: PolymarketSignedOrderRequest,
    credentials: RuntimePolymarketCredentials,
) -> None:
    order = request.signed_order
    maker = normalized_address(order.get("maker"))
    signer = normalized_address(order.get("signer"))
    if maker != credentials.funder_address:
        raise PolymarketInputError("signed_order maker does not match active funder")
    valid_signers = {credentials.signer_address}
    if credentials.signature_type == 3:
        valid_signers.add(credentials.funder_address)
    if signer not in valid_signers:
        raise PolymarketInputError("signed_order signer does not match active profile")
    if str(order.get("tokenId") or "") != request.token_id:
        raise PolymarketInputError("signed_order tokenId does not match request token_id")
    if str(order.get("side") or "").upper() != request.side:
        raise PolymarketInputError("signed_order side does not match request side")
    validate_order_amounts(request)


async def validate_trading_restriction(request: PolymarketSignedOrderRequest, restriction: object) -> None:
    close_only = bool(getattr(restriction, "close_only", False))
    blocked = bool(getattr(restriction, "blocked", False))
    country = getattr(restriction, "country", None)
    if blocked and not close_only:
        raise PolymarketInputError(f"Polymarket trading is blocked for current region: {country or 'unknown'}")
    if not close_only:
        return
    if request.side == "BUY":
        raise PolymarketInputError("当前地区为 close-only，只允许卖出已有 shares，不允许 BUY")
    available_size = await current_token_position_size(request.token_id)
    signed_amounts = signed_order_amounts(request)
    if signed_amounts.size > available_size:
        raise PolymarketInputError(
            f"当前地区为 close-only，SELL size 不能超过当前 token 持仓 {available_size.normalize()}"
        )


async def current_token_position_size(token_id: str) -> Decimal:
    snapshot = await polymarket_account_store.snapshot()
    total = Decimal("0")
    normalized_token_id = token_id.lower()
    for position in snapshot.positions:
        if not position.asset or position.asset.lower() != normalized_token_id or position.size is None:
            continue
        try:
            total += Decimal(str(position.size))
        except InvalidOperation:
            continue
    return total


@dataclass(frozen=True)
class SignedOrderAmounts:
    size: Decimal
    price: Decimal


def signed_order_amounts(request: PolymarketSignedOrderRequest) -> SignedOrderAmounts:
    order = request.signed_order
    try:
        maker_amount = Decimal(str(order.get("makerAmount")))
        taker_amount = Decimal(str(order.get("takerAmount")))
    except (InvalidOperation, TypeError) as exc:
        raise PolymarketInputError("signed_order makerAmount/takerAmount must be numeric") from exc
    if maker_amount <= 0 or taker_amount <= 0:
        raise PolymarketInputError("signed_order makerAmount/takerAmount must be positive")
    unit = Decimal("1000000")
    if request.side == "BUY":
        return SignedOrderAmounts(size=taker_amount / unit, price=maker_amount / taker_amount)
    return SignedOrderAmounts(size=maker_amount / unit, price=taker_amount / maker_amount)


def validate_order_amounts(request: PolymarketSignedOrderRequest) -> None:
    signed_amounts = signed_order_amounts(request)
    if request.order_type in {"FOK", "FAK"}:
        return
    try:
        request_price = Decimal(str(request.price))
        request_size = Decimal(str(request.size))
    except (InvalidOperation, TypeError) as exc:
        raise PolymarketInputError("order price/size must be numeric") from exc
    if abs(signed_amounts.size - request_size) > Decimal("0.000001"):
        raise PolymarketInputError("signed_order size does not match request size")
    if abs(signed_amounts.price - request_price) > Decimal("0.000001"):
        raise PolymarketInputError("signed_order price does not match request price")


def normalized_address(value: object) -> str:
    text = str(value or "").strip().lower()
    if not text.startswith("0x"):
        raise PolymarketInputError("signed_order contains invalid address")
    return text


def polymarket_http_error_detail(prefix: str, exc: httpx.HTTPStatusError) -> str:
    response = exc.response
    return f"{prefix}: HTTP {response.status_code} {response.reason_phrase}"
