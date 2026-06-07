from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from app.core.config import settings
from app.schemas import IndicatorSnapshot, OrderbookSnapshot, PolyMarket, PreviewSignal
from app.services.state_store import StateStore, state_store


SignalSide = Literal["BUY_YES", "BUY_NO", "HOLD"]


class SignalService:
    def __init__(self, store: StateStore = state_store) -> None:
        self._store = store

    def latest_signal(self) -> dict[str, Any]:
        signals = self.signals(limit=1)
        if signals:
            return signals[0]
        return self._empty_signal()

    def preview_signal(self) -> PreviewSignal:
        snapshot = self._store.get_indicator_snapshot(settings.binance_symbol)
        created_at = datetime.now(timezone.utc)
        if snapshot is None:
            return PreviewSignal(
                id="preview-btcusdt-unavailable",
                symbol=settings.binance_symbol,
                side="HOLD",
                confidence=0.0,
                reason="Preview unavailable: Binance indicators have not been calculated yet.",
                actionable=False,
                uses_closed_candle=False,
                created_at=created_at,
                indicator_snapshot={"basis": "no_indicator_snapshot"},
            )

        side, confidence, reason, data = self._technical_view(snapshot)
        return PreviewSignal(
            id=f"preview-{settings.binance_symbol.lower()}-{int(created_at.timestamp())}",
            symbol=settings.binance_symbol,
            side=side,
            confidence=confidence,
            reason=f"Preview only: {reason}",
            actionable=False,
            uses_closed_candle=False,
            created_at=created_at,
            indicator_snapshot=data,
        )

    def signals(self, limit: int = 20) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc)
        snapshot = self._store.get_indicator_snapshot(settings.binance_symbol)
        markets = self._store.get_markets()
        signals: list[dict[str, Any]] = []

        if snapshot:
            signals.append(self._technical_signal(snapshot, now))

        for market in markets:
            signals.append(self._market_signal(market, now, snapshot))

        return signals[: max(limit, 0)]

    def _technical_signal(self, snapshot: IndicatorSnapshot, now: datetime) -> dict[str, Any]:
        side, confidence, reason, data = self._technical_view(snapshot)
        risk_blocked = side == "HOLD"
        return {
            "id": f"technical-{snapshot.symbol.lower()}-{int(snapshot.updated_at.timestamp())}",
            "market_id": settings.binance_symbol,
            "signal_type": "technical",
            "side": side,
            "confidence": confidence,
            "reason": reason,
            "risk_blocked": risk_blocked,
            "created_at": now.isoformat(),
            "indicator_snapshot": data,
        }

    def _market_signal(
        self,
        market: PolyMarket,
        now: datetime,
        snapshot: IndicatorSnapshot | None,
    ) -> dict[str, Any]:
        yes_book = self._store.get_orderbook(market.yes_token_id)
        no_book = self._store.get_orderbook(market.no_token_id)
        side, confidence, reason = self._market_view(market, yes_book, no_book, snapshot, now)
        risk_blocked = self._is_market_signal_blocked(side, yes_book, no_book, now)

        return {
            "id": f"market-{market.id}-{int(now.timestamp())}",
            "market_id": market.id,
            "signal_type": "market",
            "side": side,
            "confidence": confidence,
            "reason": reason,
            "risk_blocked": risk_blocked,
            "created_at": now.isoformat(),
            "indicator_snapshot": {
                "interval": market.interval,
                "event_slug": market.event_slug,
                "yes_bid": yes_book.best_bid if yes_book else None,
                "yes_ask": yes_book.best_ask if yes_book else None,
                "yes_spread": yes_book.spread if yes_book else None,
                "no_bid": no_book.best_bid if no_book else None,
                "no_ask": no_book.best_ask if no_book else None,
                "no_spread": no_book.spread if no_book else None,
                "seconds_to_end": _seconds_to_end(market, now),
            },
        }

    def _technical_view(self, snapshot: IndicatorSnapshot) -> tuple[SignalSide, float, str, dict[str, Any]]:
        one_minute = snapshot.intervals.get("1m")
        five_minute = snapshot.intervals.get("5m")
        rsi_1m = one_minute.rsi if one_minute else None
        rsi_5m = five_minute.rsi if five_minute else None
        trend_1m = one_minute.trend if one_minute else "insufficient_data"
        trend_5m = five_minute.trend if five_minute else "insufficient_data"

        data = {
            "rsi_1m": rsi_1m,
            "rsi_5m": rsi_5m,
            "trend_1m": trend_1m,
            "trend_5m": trend_5m,
        }

        if rsi_1m is None or rsi_5m is None:
            return "HOLD", 0.0, "Insufficient RSI data for technical signal.", data
        if rsi_1m >= 58 and rsi_5m >= 52 and trend_1m != "down":
            return "BUY_YES", min(0.9, 0.5 + (rsi_1m - 50) / 100), "Closed-candle RSI momentum favors UP / YES.", data
        if rsi_1m <= 42 and rsi_5m <= 48 and trend_1m != "up":
            return "BUY_NO", min(0.9, 0.5 + (50 - rsi_1m) / 100), "Closed-candle RSI momentum favors DOWN / NO.", data
        return "HOLD", 0.35, "Closed-candle indicators are mixed; no directional technical signal.", data

    def _market_view(
        self,
        market: PolyMarket,
        yes_book: OrderbookSnapshot | None,
        no_book: OrderbookSnapshot | None,
        snapshot: IndicatorSnapshot | None,
        now: datetime,
    ) -> tuple[SignalSide, float, str]:
        if not yes_book or not no_book:
            return "HOLD", 0.0, "Market signal unavailable: waiting for YES/NO orderbook snapshots."

        if _is_stale(yes_book, now) or _is_stale(no_book, now):
            return "HOLD", 0.0, "Market signal blocked: orderbook data is stale."

        seconds_to_end = _seconds_to_end(market, now)
        if seconds_to_end is not None and seconds_to_end < 60:
            return "HOLD", 0.0, "Market signal blocked: market is too close to settlement."

        yes_mid = _mid(yes_book)
        no_mid = _mid(no_book)
        if yes_mid is None or no_mid is None:
            return "HOLD", 0.0, "Market signal unavailable: missing best bid/ask."

        technical_side = "HOLD"
        if snapshot:
            technical_side, _, _, _ = self._technical_view(snapshot)

        if yes_mid >= 0.55 and technical_side == "BUY_YES":
            return "BUY_YES", min(0.88, 0.52 + abs(yes_mid - 0.5)), "Polymarket YES price and technical momentum agree."
        if no_mid >= 0.55 and technical_side == "BUY_NO":
            return "BUY_NO", min(0.88, 0.52 + abs(no_mid - 0.5)), "Polymarket NO price and technical momentum agree."

        return "HOLD", 0.3, "Market price action does not align with technical direction."

    def _is_market_signal_blocked(
        self,
        side: SignalSide,
        yes_book: OrderbookSnapshot | None,
        no_book: OrderbookSnapshot | None,
        now: datetime,
    ) -> bool:
        if side == "HOLD" or not yes_book or not no_book:
            return True
        book = yes_book if side == "BUY_YES" else no_book
        if _is_stale(book, now):
            return True
        if book.spread is None or book.spread > settings.max_spread:
            return True
        if book.liquidity is None or book.liquidity < settings.min_liquidity:
            return True
        return False

    def _empty_signal(self) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        return {
            "id": f"empty-{int(now.timestamp())}",
            "market_id": settings.binance_symbol,
            "signal_type": "system",
            "side": "HOLD",
            "confidence": 0.0,
            "reason": "No signal inputs are available yet.",
            "risk_blocked": True,
            "created_at": now.isoformat(),
            "indicator_snapshot": {},
        }


def _mid(book: OrderbookSnapshot) -> float | None:
    if book.best_bid is None or book.best_ask is None:
        return None
    return (book.best_bid + book.best_ask) / 2


def _is_stale(book: OrderbookSnapshot, now: datetime) -> bool:
    if book.updated_at is None:
        return True
    return (now - book.updated_at).total_seconds() > 10


def _seconds_to_end(market: PolyMarket, now: datetime) -> int | None:
    if market.end_time is None:
        return None
    return max(int((market.end_time - now).total_seconds()), 0)


signal_service = SignalService()
