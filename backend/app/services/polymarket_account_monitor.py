from __future__ import annotations

import asyncio
import json
import logging
from json import JSONDecodeError
from typing import Any

import websockets
from fastapi.encoders import jsonable_encoder

from app.core.config import settings
from app.services.polymarket_account_store import (
    TRADE_CONFIRMED,
    TRADE_REFRESH_FAILED,
    polymarket_account_store,
)
from app.services.polymarket_account_ws_hub import polymarket_account_ws_hub
from app.services.polymarket_client import (
    PolymarketClient,
    normalize_account_order,
    normalize_account_trade,
)
from app.services.polymarket_credentials import (
    RuntimePolymarketCredentials,
    resolve_runtime_credentials,
)
from app.services.polymarket_market_store import polymarket_up_down_store
from app.services.service_health import service_health_store

logger = logging.getLogger(__name__)


class PolymarketUserSubscriptionChanged(RuntimeError):
    pass


class PolymarketAccountMonitor:
    """私有账户监控：公开 REST 快照给页面定基准，authenticated User WS 负责实时 order/trade。"""

    def __init__(self) -> None:
        self._client = PolymarketClient()
        self._tasks: list[asyncio.Task] = []
        self._condition_change_event = asyncio.Event()
        self._credential_change_event = asyncio.Event()
        self._last_condition_ids: set[str] = set()

    async def start(self) -> None:
        self._tasks = [
            asyncio.create_task(self.subscription_watch_loop(), name="polymarket-account-subscriptions"),
        ]
        if not settings.polymarket_user_ws_enabled:
            await self.set_ws_state("idle")
            return
        self._tasks.append(asyncio.create_task(self.ws_loop(), name="polymarket-account-user-ws"))

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks = []
        await self.set_ws_state("stopped")

    async def snapshot_loop(self) -> None:
        while True:
            try:
                await self.refresh_account_snapshot()
                await self.broadcast_all_snapshots()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Polymarket account snapshot refresh failed", exc_info=exc)
            await asyncio.sleep(max(5, settings.polymarket_account_refresh_seconds))

    async def subscription_watch_loop(self) -> None:
        while True:
            try:
                condition_ids = set(await polymarket_up_down_store.condition_ids())
                if condition_ids != self._last_condition_ids:
                    self._last_condition_ids = condition_ids
                    self._condition_change_event.set()
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Polymarket account subscription watch failed")
                await asyncio.sleep(5)

    async def refresh_account_snapshot(self) -> str | None:
        credentials = await resolve_runtime_credentials()
        wallet = credentials.funder_address if credentials else ""
        await polymarket_account_store.set_account_identity(
            wallet=wallet,
            clob_address=credentials.signer_address if credentials else None,
        )
        fetches = [("restriction", self._client.fetch_trading_restriction())]
        if wallet:
            fetches.append(("positions", self._client.fetch_positions(wallet=wallet, size_threshold=0)))
        if credentials:
            fetches.extend(
                [
                    ("balance", self._client.fetch_balance_allowance(credentials=credentials)),
                    ("orders", self._client.fetch_open_orders(credentials=credentials)),
                ]
            )
        if not fetches:
            await polymarket_account_store.set_error(None)
            return None

        # 仓位、余额、挂单来自不同接口，互不依赖；并发拉取可以避免慢接口拖住其他账户状态。
        results = await asyncio.gather(*(fetch for _, fetch in fetches), return_exceptions=True)
        errors: list[str] = []
        for (name, _), result in zip(fetches, results, strict=True):
            if isinstance(result, Exception):
                errors.append(f"{name}: {type(result).__name__}")
                continue
            if name == "positions":
                await polymarket_account_store.replace_positions(result)
            elif name == "balance":
                await polymarket_account_store.replace_balance(result)
            elif name == "orders":
                await polymarket_account_store.replace_orders(result)
            elif name == "restriction":
                await polymarket_account_store.replace_trading_restriction(result)
        error = f"Polymarket account fetch partially failed: {'; '.join(errors)}" if errors else None
        await polymarket_account_store.set_error(error)
        return error

    async def ws_loop(self) -> None:
        backoff = 1.0
        while True:
            try:
                if await resolve_runtime_credentials() is None:
                    await self.set_ws_state("config_missing", "Polymarket CLOB API credentials are not configured")
                    try:
                        await asyncio.wait_for(self._credential_change_event.wait(), timeout=10)
                    except TimeoutError:
                        continue
                    self._credential_change_event.clear()
                await self._ws_once()
                backoff = 1.0
            except asyncio.CancelledError:
                raise
            except PolymarketUserSubscriptionChanged:
                await self.set_ws_state("reconnecting")
                backoff = 1.0
            except Exception as exc:
                logger.exception("Polymarket user websocket failed")
                await self.set_ws_state("reconnecting", str(exc))
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def _ws_once(self) -> None:
        condition_ids = await self._wait_for_condition_ids()
        credentials = await resolve_runtime_credentials()
        if credentials is None:
            await self.set_ws_state("config_missing", "Polymarket CLOB API credentials are not configured")
            return
        await self.set_ws_state("connecting")
        async with websockets.connect(settings.polymarket_ws_user_url, ping_interval=None) as websocket:
            await websocket.send(json.dumps(user_subscription_payload(condition_ids, credentials=credentials)))
            await self.set_ws_state("running")
            ping_task = asyncio.create_task(self._ping_loop(websocket), name="polymarket-account-ping")
            condition_task = asyncio.create_task(self._condition_change_event.wait(), name="polymarket-account-condition-change")
            credential_task = asyncio.create_task(self._credential_change_event.wait(), name="polymarket-account-credential-change")
            receive_task = asyncio.create_task(websocket.recv(), name="polymarket-account-recv")
            try:
                while True:
                    done, pending = await asyncio.wait(
                        {condition_task, credential_task, receive_task},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if credential_task in done:
                        self._credential_change_event.clear()
                        raise PolymarketUserSubscriptionChanged
                    if condition_task in done:
                        self._condition_change_event.clear()
                        updated_ids = await polymarket_up_down_store.condition_ids()
                        await websocket.send(
                            json.dumps(
                                user_subscription_payload(
                                    updated_ids,
                                    credentials=credentials,
                                    operation="subscribe",
                                )
                            )
                        )
                        condition_task = asyncio.create_task(
                            self._condition_change_event.wait(),
                            name="polymarket-account-condition-change",
                        )
                    if receive_task in done:
                        raw_message = receive_task.result()
                        await self.handle_raw_message(raw_message)
                        receive_task = asyncio.create_task(websocket.recv(), name="polymarket-account-recv")
                    for task in pending:
                        if task.done():
                            task.result()
            finally:
                ping_task.cancel()
                condition_task.cancel()
                credential_task.cancel()
                receive_task.cancel()
                await cancel_tasks(ping_task, condition_task, credential_task, receive_task)

    async def _wait_for_condition_ids(self) -> list[str]:
        while True:
            condition_ids = await polymarket_up_down_store.condition_ids()
            if condition_ids:
                return condition_ids
            await asyncio.sleep(3)

    async def _ping_loop(self, websocket: Any) -> None:
        while True:
            await asyncio.sleep(10)
            await websocket.send("PING")

    async def handle_raw_message(self, raw_message: str | bytes) -> None:
        text = (raw_message.decode() if isinstance(raw_message, bytes) else raw_message).strip()
        if text.upper() in {"PONG", "PING"}:
            return
        if not text:
            return
        try:
            payload = json.loads(text)
        except JSONDecodeError:
            logger.debug("Ignoring non-JSON Polymarket user websocket frame", extra={"message": text[:120]})
            return
        messages = payload if isinstance(payload, list) else [payload]
        changed_conditions: set[str | None] = set()
        trade_ids: set[str] = set()
        for message in messages:
            if not isinstance(message, dict):
                continue
            for event_type, row in normalize_account_events(message):
                if event_type == "order":
                    order = normalize_account_order(row)
                    await polymarket_account_store.apply_order(order)
                    changed_conditions.add(order.market)
                elif event_type == "trade":
                    trade = normalize_account_trade(row)
                    await polymarket_account_store.apply_trade(trade)
                    changed_conditions.add(trade.market)
                    trade_ids.add(trade.id)
        if trade_ids:
            await self.broadcast_changed_snapshots(changed_conditions)
            snapshot_error = None
            try:
                snapshot_error = await self.refresh_account_snapshot()
            except Exception:
                logger.warning("Polymarket account snapshot refresh after trade failed", exc_info=True)
                snapshot_error = "exception"
            await polymarket_account_store.mark_trades_confirmation(
                trade_ids,
                TRADE_REFRESH_FAILED if snapshot_error else TRADE_CONFIRMED,
            )
            changed_conditions.update(await polymarket_up_down_store.condition_ids())
        await self.broadcast_changed_snapshots(changed_conditions)

    async def broadcast_changed_snapshots(self, condition_ids: set[str | None]) -> None:
        await self.broadcast_snapshot(None)
        for condition_id in sorted(condition for condition in condition_ids if condition):
            await self.broadcast_snapshot(condition_id)

    async def broadcast_all_snapshots(self) -> None:
        await self.broadcast_snapshot(None)
        for condition_id in await polymarket_up_down_store.condition_ids():
            await self.broadcast_snapshot(condition_id)

    async def broadcast_snapshot(self, condition_id: str | None) -> None:
        state = await polymarket_account_store.snapshot(condition_id)
        await polymarket_account_ws_hub.broadcast(
            {
                "type": "polymarket.account_state.snapshot",
                "condition_id": condition_id,
                "state": jsonable_encoder(state),
            },
            condition_id=condition_id,
        )

    async def set_ws_state(self, state: str, error: str | None = None) -> None:
        await polymarket_account_store.set_ws_state(state, error)
        service_health_store.set(
            "polymarket_user_ws",
            state,
            last_error=error,
            metadata={"endpoint": settings.polymarket_ws_user_url},
        )

    def notify_credentials_changed(self) -> None:
        self._credential_change_event.set()


def user_subscription_payload(
    condition_ids: list[str],
    *,
    credentials: RuntimePolymarketCredentials,
    operation: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": "user",
        "markets": sorted(set(condition_ids)),
        "auth": {
            "apiKey": credentials.api_key,
            "secret": credentials.api_secret,
            "passphrase": credentials.api_passphrase,
        },
    }
    if operation:
        payload["operation"] = operation
    return payload


def normalize_account_events(message: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    direct_type = account_event_type(message)
    if direct_type in {"order", "trade"}:
        events.append((direct_type, message))

    for key, event_type in (("orders", "order"), ("order", "order"), ("trades", "trade"), ("trade", "trade")):
        for row in dict_rows(message.get(key)):
            events.append((event_type, row))

    for row in dict_rows(message.get("data")):
        nested_type = account_event_type(row)
        if nested_type in {"order", "trade"}:
            events.append((nested_type, row))
    return dedupe_account_events(events)


def account_event_type(row: dict[str, Any]) -> str:
    event_type = str(row.get("event_type") or "").lower()
    if event_type in {"order", "trade"}:
        return event_type
    value_type = str(row.get("type") or "").lower()
    if value_type == "trade":
        return "trade"
    if value_type in {"placement", "update", "cancellation", "order"}:
        return "order"
    return ""


def dict_rows(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    if isinstance(value, dict):
        return [value]
    return []


def dedupe_account_events(events: list[tuple[str, dict[str, Any]]]) -> list[tuple[str, dict[str, Any]]]:
    seen: set[tuple[str, int]] = set()
    result: list[tuple[str, dict[str, Any]]] = []
    for event_type, row in events:
        key = (event_type, id(row))
        if key in seen:
            continue
        seen.add(key)
        result.append((event_type, row))
    return result


async def cancel_tasks(*tasks: asyncio.Task) -> None:
    for task in tasks:
        task.cancel()
    for task in tasks:
        try:
            await task
        except (asyncio.CancelledError, websockets.exceptions.ConnectionClosed):
            pass


polymarket_account_monitor = PolymarketAccountMonitor()
