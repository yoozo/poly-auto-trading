from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from threading import RLock

from app.schemas import (
    Candle,
    IndicatorSnapshot,
    Interval,
    OrderbookSnapshot,
    PolyMarket,
    RuntimeStatus,
    ServiceHealth,
    ServiceState,
)


DEFAULT_CANDLE_LIMIT = 1000


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class StateStore:
    """Thread-safe in-memory state shared by background services and API routes."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._candles: dict[tuple[str, Interval], list[Candle]] = defaultdict(list)
        self._indicators: dict[str, IndicatorSnapshot] = {}
        self._markets: dict[str, PolyMarket] = {}
        self._orderbooks: dict[str, OrderbookSnapshot] = {}
        self._services: dict[str, ServiceHealth] = {
            "binance_rest": ServiceHealth(name="binance_rest", state="idle"),
            "binance_ws": ServiceHealth(name="binance_ws", state="idle"),
            "polymarket_market_refresh": ServiceHealth(name="polymarket_market_refresh", state="idle"),
            "polymarket_market_ws": ServiceHealth(name="polymarket_market_ws", state="idle"),
        }
        self._scheduler_state = "stopped"

    def upsert_candles(
        self,
        symbol: str,
        interval: Interval,
        candles: list[Candle],
        max_items: int = DEFAULT_CANDLE_LIMIT,
    ) -> None:
        normalized_symbol = symbol.upper()
        key = (normalized_symbol, interval)
        with self._lock:
            by_open_time = {candle.open_time: candle for candle in self._candles[key]}
            for candle in candles:
                by_open_time[candle.open_time] = candle.model_copy(update={"symbol": normalized_symbol, "interval": interval})
            self._candles[key] = sorted(by_open_time.values(), key=lambda candle: candle.open_time)[-max_items:]

    def get_candles(self, symbol: str, interval: Interval, limit: int) -> list[Candle]:
        key = (symbol.upper(), interval)
        with self._lock:
            return list(self._candles.get(key, []))[-limit:]

    def set_indicator_snapshot(self, snapshot: IndicatorSnapshot) -> None:
        with self._lock:
            self._indicators[snapshot.symbol.upper()] = snapshot.model_copy(update={"symbol": snapshot.symbol.upper()})

    def get_indicator_snapshot(self, symbol: str) -> IndicatorSnapshot | None:
        with self._lock:
            return self._indicators.get(symbol.upper())

    def set_markets(self, markets: list[PolyMarket]) -> None:
        with self._lock:
            self._markets = {market.id: market for market in markets}

    def get_markets(self) -> list[PolyMarket]:
        with self._lock:
            return sorted(
                self._markets.values(),
                key=lambda market: (market.end_time or datetime.max.replace(tzinfo=timezone.utc), market.id),
            )

    def get_market_token_ids(self) -> list[str]:
        with self._lock:
            token_ids: list[str] = []
            for market in self._markets.values():
                token_ids.extend([market.yes_token_id, market.no_token_id])
            return [token_id for token_id in token_ids if token_id]

    def set_orderbook(self, snapshot: OrderbookSnapshot) -> None:
        with self._lock:
            self._orderbooks[snapshot.token_id] = snapshot

    def get_orderbook(self, token_id: str | None = None) -> OrderbookSnapshot | None:
        with self._lock:
            if token_id:
                return self._orderbooks.get(token_id)
            for market in self.get_markets():
                for candidate in (market.yes_token_id, market.no_token_id):
                    if candidate in self._orderbooks:
                        return self._orderbooks[candidate]
            return next(iter(self._orderbooks.values()), None)

    def set_scheduler_state(self, state: str) -> None:
        with self._lock:
            self._scheduler_state = state

    def set_service_health(self, name: str, state: ServiceState, last_error: str | None = None) -> None:
        with self._lock:
            self._services[name] = ServiceHealth(
                name=name,
                state=state,
                last_update=utc_now(),
                last_error=last_error,
            )

    def get_service_health(self, name: str) -> ServiceHealth | None:
        with self._lock:
            return self._services.get(name)

    def get_runtime_status(self) -> RuntimeStatus:
        with self._lock:
            last_error = next(
                (service.last_error for service in self._services.values() if service.last_error),
                None,
            )
            return RuntimeStatus(
                services=dict(self._services),
                scheduler=self._scheduler_state,
                tracked_markets=len(self._markets),
                last_error=last_error,
                updated_at=utc_now(),
            )


state_store = StateStore()
