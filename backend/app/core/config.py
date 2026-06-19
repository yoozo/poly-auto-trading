from functools import cached_property
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
PROJECT_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    app_name: str = "poly-auto-trading"
    app_env: str = "development"
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/poly_auto_trading"
    cors_origins_raw: str = Field(
        default="http://localhost:5173,http://127.0.0.1:5173,http://localhost:5174,http://127.0.0.1:5174",
        validation_alias="API_CORS_ORIGINS",
    )
    binance_symbol: str = "BTCUSDT"
    binance_rest_base_url: str = "https://api.binance.com"
    binance_ws_base_url: str = "wss://stream.binance.com:9443"
    binance_rest_base_urls_raw: str | None = None
    binance_ws_base_urls_raw: str | None = None
    binance_intervals_raw: str = "1m,5m,15m,1h,4h"
    candle_history_limit: int = 500
    binance_ws_enabled: bool = True
    polymarket_gamma_base_url: str = "https://gamma-api.polymarket.com"
    polymarket_data_base_url: str = "https://data-api.polymarket.com"
    polymarket_clob_base_url: str = "https://clob.polymarket.com"
    polymarket_ws_enabled: bool = True
    polymarket_ws_market_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    polymarket_user_ws_enabled: bool = True
    polymarket_ws_user_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/user"
    polymarket_position_wallet: str = ""
    polymarket_clob_api_key: str = ""
    polymarket_clob_secret: str = ""
    polymarket_clob_passphrase: str = ""
    polymarket_clob_address: str = ""
    polymarket_account_refresh_seconds: int = 30
    polymarket_market_refresh_seconds: int = 60
    polymarket_market_boundary_refresh_window_seconds: int = 3
    polymarket_market_empty_retry_seconds: int = 5
    polymarket_market_signal_refresh_min_seconds: int = 30
    polymarket_ws_broadcast_interval_seconds: float = 0.2
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_enabled_default: bool = False
    telegram_rsi_low: float = 30.0
    telegram_rsi_high: float = 70.0
    telegram_rsi_extreme_low: float = 20.0
    telegram_rsi_extreme_high: float = 80.0
    telegram_rsi_ema_diff_abs: float = 8.0
    signal_interval_base_scores_raw: str = "1m:1,5m:2,15m:3,1h:4,4h:5"
    signal_rsi_ema_diff_diff_bonus_raw: str = "12:0,15:1,18:2,20:3,22:4,25:5"
    signal_rsi_bonus_raw: str = "70:1,80:2,90:3"
    telegram_close_warning_seconds: int = 15
    auth_password: str = ""
    auth_session_secret: str = ""
    auth_cookie_name: str = "poly_auto_session"
    auth_session_ttl_seconds: int = 60 * 60 * 24 * 7
    auth_cookie_secure: bool = False
    log_level: str = "INFO"

    model_config = SettingsConfigDict(
        env_file=(PROJECT_DIR / ".env", BACKEND_DIR / ".env"),
        env_prefix="",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @cached_property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins_raw.split(",") if origin.strip()]

    @cached_property
    def binance_intervals(self) -> list[str]:
        return [
            interval.strip()
            for interval in self.binance_intervals_raw.split(",")
            if interval.strip()
        ]

    @cached_property
    def signal_interval_base_scores(self) -> dict[str, float]:
        return parse_float_mapping(self.signal_interval_base_scores_raw)

    @cached_property
    def signal_rsi_ema_diff_diff_bonus(self) -> list[tuple[float, float]]:
        mapping = parse_numeric_float_mapping(self.signal_rsi_ema_diff_diff_bonus_raw)
        return sorted(mapping.items(), key=lambda item: item[0])

    @cached_property
    def signal_rsi_bonus(self) -> list[tuple[float, float]]:
        mapping = parse_numeric_float_mapping(self.signal_rsi_bonus_raw)
        return sorted(mapping.items(), key=lambda item: item[0])

    @cached_property
    def binance_rest_base_urls(self) -> list[str]:
        urls = parse_csv(self.binance_rest_base_urls_raw)
        if urls:
            return urls
        return unique_urls(
            [
                self.binance_rest_base_url,
                "https://api.binance.com",
                "https://api1.binance.com",
                "https://api2.binance.com",
                "https://api3.binance.com",
                "https://api4.binance.com",
            ]
        )

    @cached_property
    def binance_ws_base_urls(self) -> list[str]:
        urls = parse_csv(self.binance_ws_base_urls_raw)
        if urls:
            return urls
        return unique_urls(
            [
                self.binance_ws_base_url,
                "wss://stream.binance.com:9443",
                "wss://stream.binance.com:443",
            ]
        )


def parse_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip().rstrip("/") for item in value.split(",") if item.strip()]


def parse_float_mapping(value: str | None) -> dict[str, float]:
    result: dict[str, float] = {}
    if not value:
        return result
    for item in value.split(","):
        if not item.strip():
            continue
        key, raw_number = item.split(":", 1)
        result[key.strip()] = float(raw_number.strip())
    return result


def parse_numeric_float_mapping(value: str | None) -> dict[float, float]:
    result: dict[float, float] = {}
    if not value:
        return result
    for item in value.split(","):
        if not item.strip():
            continue
        parts = item.split(":")
        if len(parts) != 2:
            raise ValueError(f"Invalid mapping item '{item}'")
        raw_key, raw_number = parts
        try:
            result[float(raw_key.strip())] = float(raw_number.strip())
        except ValueError as exc:
            raise ValueError(f"Invalid mapping item '{item}'") from exc
    return result


def unique_urls(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for url in urls:
        normalized = url.strip().rstrip("/")
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


settings = Settings()
