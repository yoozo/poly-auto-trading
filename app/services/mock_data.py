from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import sin
from random import Random

_rng = Random(42)


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


def get_status() -> dict:
    return {
        "ws": {
            "binance": "connected",
            "polymarket_market": "connected",
            "polymarket_user": "not_configured",
        },
        "scheduler": "running",
        "tracked_markets": 4,
        "last_error": None,
        "updated_at": _iso(datetime.now(timezone.utc)),
    }


def get_markets() -> list[dict]:
    now = datetime.now(timezone.utc)
    return [
        {
            "id": "btc-updown-5m-current",
            "title": "Bitcoin Up or Down - 5m",
            "interval": "5m",
            "condition_id": "0x5m-current",
            "yes_token_id": "yes-5m-current",
            "no_token_id": "no-5m-current",
            "end_time": _iso(now + timedelta(minutes=4)),
            "best_bid": 0.48,
            "best_ask": 0.51,
            "spread": 0.03,
            "liquidity": 3280.5,
            "status": "active",
        },
        {
            "id": "btc-updown-5m-next",
            "title": "Bitcoin Up or Down - Next 5m",
            "interval": "5m",
            "condition_id": "0x5m-next",
            "yes_token_id": "yes-5m-next",
            "no_token_id": "no-5m-next",
            "end_time": _iso(now + timedelta(minutes=9)),
            "best_bid": 0.46,
            "best_ask": 0.53,
            "spread": 0.07,
            "liquidity": 1150.0,
            "status": "watching",
        },
        {
            "id": "btc-updown-15m-current",
            "title": "Bitcoin Up or Down - 15m",
            "interval": "15m",
            "condition_id": "0x15m-current",
            "yes_token_id": "yes-15m-current",
            "no_token_id": "no-15m-current",
            "end_time": _iso(now + timedelta(minutes=13)),
            "best_bid": 0.52,
            "best_ask": 0.55,
            "spread": 0.03,
            "liquidity": 5022.0,
            "status": "active",
        },
        {
            "id": "btc-updown-15m-next",
            "title": "Bitcoin Up or Down - Next 15m",
            "interval": "15m",
            "condition_id": "0x15m-next",
            "yes_token_id": "yes-15m-next",
            "no_token_id": "no-15m-next",
            "end_time": _iso(now + timedelta(minutes=28)),
            "best_bid": 0.49,
            "best_ask": 0.52,
            "spread": 0.03,
            "liquidity": 4100.0,
            "status": "watching",
        },
    ]


def get_candles(symbol: str, interval: str, limit: int) -> list[dict]:
    step_minutes = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240}[interval]
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    start = now - timedelta(minutes=step_minutes * limit)
    price = 69350.0
    candles = []
    for index in range(limit):
        opened_at = start + timedelta(minutes=step_minutes * index)
        drift = sin(index / 7) * 55 + sin(index / 19) * 120
        open_price = price + drift
        close_price = open_price + sin(index / 3) * 45
        high = max(open_price, close_price) + 35 + _rng.random() * 20
        low = min(open_price, close_price) - 35 - _rng.random() * 20
        candles.append(
            {
                "symbol": symbol,
                "interval": interval,
                "open_time": _iso(opened_at),
                "close_time": _iso(opened_at + timedelta(minutes=step_minutes)),
                "open": round(open_price, 2),
                "high": round(high, 2),
                "low": round(low, 2),
                "close": round(close_price, 2),
                "volume": round(120.0 + abs(sin(index / 5)) * 80, 2),
                "is_closed": True,
            }
        )
    return candles


def get_indicators(symbol: str) -> dict:
    return {
        "symbol": symbol,
        "updated_at": _iso(datetime.now(timezone.utc)),
        "intervals": {
            "1m": {"rsi": 57.4, "bollinger": {"upper": 69920.5, "middle": 69570.2, "lower": 69219.9}, "trend": "up"},
            "5m": {"rsi": 61.8, "bollinger": {"upper": 70110.1, "middle": 69680.0, "lower": 69249.9}, "trend": "up"},
            "15m": {"rsi": 49.2, "bollinger": {"upper": 70480.8, "middle": 69750.6, "lower": 69020.4}, "trend": "flat"},
            "30m": {"rsi": 52.7, "bollinger": {"upper": 70620.1, "middle": 69880.4, "lower": 69140.7}, "trend": "flat"},
            "1h": {"rsi": 55.3, "bollinger": {"upper": 70950.2, "middle": 70010.5, "lower": 69070.8}, "trend": "up"},
            "4h": {"rsi": 47.9, "bollinger": {"upper": 71820.4, "middle": 70340.1, "lower": 68859.8}, "trend": "down"},
        },
    }


def get_latest_orderbook(token_id: str | None = None) -> dict:
    selected = token_id or "yes-5m-current"
    return {
        "token_id": selected,
        "best_bid": 0.48,
        "best_ask": 0.51,
        "spread": 0.03,
        "liquidity": 3280.5,
        "updated_at": _iso(datetime.now(timezone.utc)),
        "bids": [{"price": 0.48, "size": 760}, {"price": 0.47, "size": 540}, {"price": 0.46, "size": 980}],
        "asks": [{"price": 0.51, "size": 610}, {"price": 0.52, "size": 455}, {"price": 0.53, "size": 930}],
    }


def get_latest_signal() -> dict:
    return get_signals(limit=1)[0]


def get_preview_signal() -> dict:
    return {
        "id": "preview-btcusdt-current",
        "symbol": "BTCUSDT",
        "side": "BUY_YES",
        "confidence": 0.56,
        "reason": "Preview only: current forming candle is leaning upward, but closed-candle confirmation is required.",
        "actionable": False,
        "uses_closed_candle": False,
        "created_at": _iso(datetime.now(timezone.utc)),
        "source": "preview",
        "indicator_snapshot": {
            "basis": "mock_current_candle",
            "rsi_preview": 58.1,
            "bb_position_preview": "upper_half",
        },
    }


def get_signals(limit: int = 20) -> list[dict]:
    now = datetime.now(timezone.utc)
    signals = []
    for index in range(limit):
        blocked = index % 4 == 1
        signals.append(
            {
                "id": f"sig-{index + 1}",
                "market_id": "btc-updown-5m-current" if index % 2 == 0 else "btc-updown-15m-current",
                "side": "BUY_YES" if index % 3 != 0 else "BUY_NO",
                "confidence": round(0.58 + (index % 5) * 0.04, 2),
                "reason": "RSI momentum with acceptable spread" if not blocked else "Blocked by spread filter",
                "risk_blocked": blocked,
                "created_at": _iso(now - timedelta(minutes=index * 3)),
                "indicator_snapshot": {"rsi_1m": 57.4, "rsi_5m": 61.8, "bb_position": "upper_half"},
            }
        )
    return signals


def get_orders() -> list[dict]:
    return [
        {
            "id": "dry-run-order-001",
            "market_id": "btc-updown-5m-current",
            "side": "BUY",
            "price": 0.48,
            "size": 20,
            "filled_size": 20,
            "status": "filled",
            "updated_at": _iso(datetime.now(timezone.utc) - timedelta(minutes=8)),
        },
        {
            "id": "dry-run-sell-001",
            "market_id": "btc-updown-5m-current",
            "side": "SELL",
            "price": 0.54,
            "size": 20,
            "filled_size": 0,
            "status": "open",
            "updated_at": _iso(datetime.now(timezone.utc) - timedelta(minutes=7)),
        },
    ]


def get_notifications() -> list[dict]:
    return [
        {
            "id": "note-001",
            "event_type": "SIGNAL_CREATED",
            "message": "BUY_YES signal created for BTC 5m market.",
            "status": "sent",
            "sent_at": _iso(datetime.now(timezone.utc) - timedelta(minutes=10)),
        },
        {
            "id": "note-002",
            "event_type": "ORDER_FILLED",
            "message": "Dry-run buy order filled, sell order placed.",
            "status": "sent",
            "sent_at": _iso(datetime.now(timezone.utc) - timedelta(minutes=8)),
        },
    ]


def get_stats_summary() -> dict:
    return {
        "signals_total": 126,
        "signals_blocked": 31,
        "win_rate": 0.54,
        "average_spread": 0.034,
        "average_fill_latency_ms": 820,
        "dry_run_pnl_usdc": 18.42,
        "updated_at": _iso(datetime.now(timezone.utc)),
    }
