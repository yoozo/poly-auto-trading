from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "poly-auto-trading"
    app_env: str = "development"
    database_url: str = "sqlite:///./poly_auto_trading.db"
    binance_symbol: str = "BTCUSDT"
    binance_rest_base_urls: list[str] = [
        "https://api.binance.com",
        "https://api1.binance.com",
        "https://api2.binance.com",
        "https://api3.binance.com",
    ]
    binance_ws_base_urls: list[str] = ["wss://stream.binance.com:9443"]
    candle_history_limit: int = 300
    polymarket_gamma_base_url: str = "https://gamma-api.polymarket.com"
    polymarket_market_ws_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    polymarket_refresh_seconds: int = 30
    polymarket_ws_ping_seconds: int = 10
    polymarket_ws_resubscribe_seconds: int = 30
    polymarket_slug_lookback_count: int = 4
    polymarket_slug_window_count: int = 6
    polymarket_use_events_fallback: bool = False
    trading_enabled: bool = False
    dry_run: bool = True
    max_order_usdc: float = 25.0
    max_daily_loss_usdc: float = 100.0
    max_spread: float = 0.04
    min_liquidity: float = 100.0
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    config_file: str = "config.yaml"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    @property
    def effective_binance_rest_base_urls(self) -> list[str]:
        return self.binance_rest_base_urls

    @property
    def effective_binance_ws_base_urls(self) -> list[str]:
        return self.binance_ws_base_urls


def load_settings() -> Settings:
    config_file = Path(os.getenv("CONFIG_FILE", "config.yaml"))
    return Settings(**_load_yaml_config(config_file))


def _load_yaml_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as file:
        raw = yaml.safe_load(file) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"Config file must contain a mapping: {path}")
    return _flatten_config(raw)


def _flatten_config(raw: dict[str, Any]) -> dict[str, Any]:
    flattened: dict[str, Any] = {}

    app = raw.get("app", {})
    database = raw.get("database", {})
    binance = raw.get("binance", {})
    polymarket = raw.get("polymarket", {})
    trading = raw.get("trading", {})
    risk = raw.get("risk", {})
    frontend = raw.get("frontend", {})

    if app:
        flattened["app_name"] = app.get("name")
        flattened["app_env"] = app.get("env")
    if database:
        flattened["database_url"] = database.get("url")
    if binance:
        flattened["binance_symbol"] = binance.get("symbol")
        flattened["binance_rest_base_urls"] = binance.get("rest_base_urls")
        flattened["binance_ws_base_urls"] = binance.get("ws_base_urls")
        flattened["candle_history_limit"] = binance.get("candle_history_limit")
    if polymarket:
        flattened["polymarket_gamma_base_url"] = polymarket.get("gamma_base_url")
        flattened["polymarket_market_ws_url"] = polymarket.get("market_ws_url")
        flattened["polymarket_refresh_seconds"] = polymarket.get("refresh_seconds")
        flattened["polymarket_ws_ping_seconds"] = polymarket.get("ws_ping_seconds")
        flattened["polymarket_ws_resubscribe_seconds"] = polymarket.get("ws_resubscribe_seconds")
        flattened["polymarket_slug_lookback_count"] = polymarket.get("slug_lookback_count")
        flattened["polymarket_slug_window_count"] = polymarket.get("slug_window_count")
        flattened["polymarket_use_events_fallback"] = polymarket.get("use_events_fallback")
    if trading:
        flattened["trading_enabled"] = trading.get("enabled")
        flattened["dry_run"] = trading.get("dry_run")
    if risk:
        flattened["max_order_usdc"] = risk.get("max_order_usdc")
        flattened["max_daily_loss_usdc"] = risk.get("max_daily_loss_usdc")
        flattened["max_spread"] = risk.get("max_spread")
        flattened["min_liquidity"] = risk.get("min_liquidity")
    if frontend:
        flattened["cors_origins"] = frontend.get("cors_origins")

    return {key: value for key, value in flattened.items() if value is not None}


settings = load_settings()
