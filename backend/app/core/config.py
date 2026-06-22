from __future__ import annotations

from functools import cached_property
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
PROJECT_DIR = BACKEND_DIR.parent
DEFAULT_CONFIG_PATH = PROJECT_DIR / "config" / "app.yaml"
DEFAULT_YAML_CONFIG_PATH = PROJECT_DIR / "config" / "app.default.yaml"
DEFAULT_ENV_FILES = (PROJECT_DIR / ".env", BACKEND_DIR / ".env")


class AppYamlConfig(BaseModel):
    app: AppConfig = Field(default_factory=lambda: AppConfig())
    cors: CorsConfig = Field(default_factory=lambda: CorsConfig())
    auth: AuthConfig = Field(default_factory=lambda: AuthConfig())
    binance: BinanceConfig = Field(default_factory=lambda: BinanceConfig())
    polymarket: PolymarketConfig = Field(default_factory=lambda: PolymarketConfig())
    signals: SignalConfig = Field(default_factory=lambda: SignalConfig())
    telegram: TelegramConfig = Field(default_factory=lambda: TelegramConfig())

    model_config = ConfigDict(extra="ignore")


class AppConfig(BaseModel):
    name: str = "poly-auto-trading"
    env: str = "development"
    log_level: str = "INFO"


class CorsConfig(BaseModel):
    origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:5174",
            "http://127.0.0.1:5174",
        ]
    )


class AuthConfig(BaseModel):
    cookie_name: str = "poly_auto_session"
    session_ttl_seconds: int = 60 * 60 * 24 * 7
    cookie_secure: bool = False


class BinanceConfig(BaseModel):
    symbol: str = "BTCUSDT"
    archive_base_url: str = "https://data.binance.vision"
    archive_enabled: bool = True
    rest_base_urls: list[str] = Field(
        default_factory=lambda: [
            "https://api.binance.com",
            "https://api1.binance.com",
            "https://api2.binance.com",
            "https://api3.binance.com",
            "https://api4.binance.com",
        ]
    )
    ws_base_urls: list[str] = Field(
        default_factory=lambda: [
            "wss://stream.binance.com:9443",
            "wss://stream.binance.com:443",
        ]
    )
    intervals: list[str] = Field(default_factory=lambda: ["1m", "5m", "15m", "1h", "4h", "12h", "1d", "1w"])
    candle_history_limit: int = 500
    ws_enabled: bool = True


class PolymarketConfig(BaseModel):
    gamma_base_url: str = "https://gamma-api.polymarket.com"
    data_base_url: str = "https://data-api.polymarket.com"
    clob_base_url: str = "https://clob.polymarket.com"
    ws_enabled: bool = True
    ws_market_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    user_ws_enabled: bool = True
    ws_user_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/user"
    account_refresh_seconds: int = 30
    market_refresh_seconds: int = 60
    market_boundary_refresh_window_seconds: int = 3
    market_empty_retry_seconds: int = 5
    market_signal_refresh_min_seconds: int = 30
    ws_broadcast_interval_seconds: float = 0.2


class SignalConfig(BaseModel):
    interval_base_scores: dict[str, float] = Field(
        default_factory=lambda: {"1m": 1, "5m": 2, "15m": 3, "1h": 4, "4h": 5}
    )
    rsi_ema_diff_diff_bonus: dict[float, float] = Field(
        default_factory=lambda: {12: 0, 15: 1, 18: 2, 20: 3, 22: 4, 25: 5}
    )
    rsi_bonus: dict[float, float] = Field(default_factory=lambda: {70: 1, 80: 2, 90: 3})


class TelegramConfig(BaseModel):
    enabled_default: bool = False
    rsi_low: float = 30.0
    rsi_high: float = 70.0
    rsi_extreme_low: float = 20.0
    rsi_extreme_high: float = 80.0
    rsi_ema_diff_abs: float = 8.0
    close_warning_seconds: int = 15


class SecretSettings(BaseSettings):
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/poly_auto_trading"
    auth_password: str = ""
    auth_session_secret: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    polymarket_credentials_encryption_key: str = ""

    model_config = SettingsConfigDict(
        env_file=DEFAULT_ENV_FILES,
        env_prefix="",
        env_file_encoding="utf-8",
        extra="ignore",
    )


class Settings:
    def __init__(
        self,
        *,
        config_path: Path | str | None = DEFAULT_CONFIG_PATH,
        _env_file: Any = DEFAULT_ENV_FILES,
        **overrides: Any,
    ) -> None:
        yaml_config = load_yaml_config(config_path)
        secrets = SecretSettings(_env_file=_env_file)

        # 非敏感配置只来自 YAML；secrets 单独来自 .env，业务层仍通过 flat settings 读取。
        self.app_name = yaml_config.app.name
        self.app_env = yaml_config.app.env
        self.log_level = yaml_config.app.log_level
        self.database_url = secrets.database_url

        self.cors_origins_raw = join_csv(yaml_config.cors.origins)

        self.auth_password = secrets.auth_password
        self.auth_session_secret = secrets.auth_session_secret
        self.auth_cookie_name = yaml_config.auth.cookie_name
        self.auth_session_ttl_seconds = yaml_config.auth.session_ttl_seconds
        self.auth_cookie_secure = yaml_config.auth.cookie_secure

        self.binance_symbol = yaml_config.binance.symbol
        self.binance_archive_base_url = yaml_config.binance.archive_base_url
        self.binance_archive_enabled = yaml_config.binance.archive_enabled
        self.binance_rest_base_url = first_or_default(
            yaml_config.binance.rest_base_urls, "https://api.binance.com"
        )
        self.binance_ws_base_url = first_or_default(
            yaml_config.binance.ws_base_urls, "wss://stream.binance.com:9443"
        )
        self.binance_rest_base_urls_raw = join_csv(yaml_config.binance.rest_base_urls)
        self.binance_ws_base_urls_raw = join_csv(yaml_config.binance.ws_base_urls)
        self.binance_intervals_raw = join_csv(yaml_config.binance.intervals)
        self.candle_history_limit = yaml_config.binance.candle_history_limit
        self.binance_ws_enabled = yaml_config.binance.ws_enabled

        self.polymarket_gamma_base_url = yaml_config.polymarket.gamma_base_url
        self.polymarket_data_base_url = yaml_config.polymarket.data_base_url
        self.polymarket_clob_base_url = yaml_config.polymarket.clob_base_url
        self.polymarket_ws_enabled = yaml_config.polymarket.ws_enabled
        self.polymarket_ws_market_url = yaml_config.polymarket.ws_market_url
        self.polymarket_user_ws_enabled = yaml_config.polymarket.user_ws_enabled
        self.polymarket_ws_user_url = yaml_config.polymarket.ws_user_url
        self.polymarket_credentials_encryption_key = secrets.polymarket_credentials_encryption_key
        self.polymarket_account_refresh_seconds = yaml_config.polymarket.account_refresh_seconds
        self.polymarket_market_refresh_seconds = yaml_config.polymarket.market_refresh_seconds
        self.polymarket_market_boundary_refresh_window_seconds = (
            yaml_config.polymarket.market_boundary_refresh_window_seconds
        )
        self.polymarket_market_empty_retry_seconds = yaml_config.polymarket.market_empty_retry_seconds
        self.polymarket_market_signal_refresh_min_seconds = (
            yaml_config.polymarket.market_signal_refresh_min_seconds
        )
        self.polymarket_ws_broadcast_interval_seconds = (
            yaml_config.polymarket.ws_broadcast_interval_seconds
        )

        self.telegram_bot_token = secrets.telegram_bot_token
        self.telegram_chat_id = secrets.telegram_chat_id
        self.telegram_enabled_default = yaml_config.telegram.enabled_default
        self.telegram_rsi_low = yaml_config.telegram.rsi_low
        self.telegram_rsi_high = yaml_config.telegram.rsi_high
        self.telegram_rsi_extreme_low = yaml_config.telegram.rsi_extreme_low
        self.telegram_rsi_extreme_high = yaml_config.telegram.rsi_extreme_high
        self.telegram_rsi_ema_diff_abs = yaml_config.telegram.rsi_ema_diff_abs
        self.telegram_close_warning_seconds = yaml_config.telegram.close_warning_seconds

        self.signal_interval_base_scores_raw = mapping_to_raw(
            yaml_config.signals.interval_base_scores
        )
        self.signal_rsi_ema_diff_diff_bonus_raw = mapping_to_raw(
            yaml_config.signals.rsi_ema_diff_diff_bonus
        )
        self.signal_rsi_bonus_raw = mapping_to_raw(yaml_config.signals.rsi_bonus)

        override_keys = set(overrides)
        for key, value in overrides.items():
            if key.startswith("_"):
                continue
            setattr(self, key, value)
        if "binance_rest_base_url" in override_keys and "binance_rest_base_urls_raw" not in override_keys:
            self.binance_rest_base_urls_raw = None
        if "binance_ws_base_url" in override_keys and "binance_ws_base_urls_raw" not in override_keys:
            self.binance_ws_base_urls_raw = None

    @cached_property
    def cors_origins(self) -> list[str]:
        return parse_csv(self.cors_origins_raw)

    @cached_property
    def binance_intervals(self) -> list[str]:
        return parse_csv(self.binance_intervals_raw)

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


def load_yaml_config(config_path: Path | str | None) -> AppYamlConfig:
    if config_path is None:
        return AppYamlConfig()
    raw = deep_merge_yaml(
        read_yaml_config_file(DEFAULT_YAML_CONFIG_PATH),
        read_yaml_config_file(config_path),
    )
    return AppYamlConfig.model_validate(raw)


def read_yaml_config_file(config_path: Path | str) -> dict[str, Any]:
    path = Path(config_path)
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as file:
        raw = yaml.safe_load(file) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"Config file must contain a YAML object: {path}")
    return raw


def deep_merge_yaml(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        base_value = result.get(key)
        # 本地 app.yaml 只需要写差异；dict 递归合并，列表和标量按本地值整体覆盖。
        if isinstance(base_value, dict) and isinstance(value, dict):
            result[key] = deep_merge_yaml(base_value, value)
        else:
            result[key] = value
    return result


def first_or_default(values: list[str], default: str) -> str:
    return values[0] if values else default


def join_csv(values: list[Any]) -> str:
    return ",".join(str(value).strip().rstrip("/") for value in values if str(value).strip())


def mapping_to_raw(mapping: dict[Any, Any]) -> str:
    return ",".join(f"{key}:{value}" for key, value in mapping.items())


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
