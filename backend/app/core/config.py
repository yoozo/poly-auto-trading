from functools import cached_property

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "poly-auto-trading"
    app_env: str = "development"
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/poly_auto_trading"
    cors_origins_raw: str = Field(
        default="http://localhost:5173,http://127.0.0.1:5173",
        validation_alias="API_CORS_ORIGINS",
    )
    binance_symbol: str = "BTCUSDT"
    binance_rest_base_url: str = "https://api.binance.com"
    binance_ws_base_url: str = "wss://stream.binance.com:9443"
    binance_intervals_raw: str = "1m,5m,15m,30m,1h,4h"
    candle_history_limit: int = 500
    binance_ws_enabled: bool = True
    log_level: str = "INFO"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @cached_property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins_raw.split(",") if origin.strip()]

    @cached_property
    def binance_intervals(self) -> list[str]:
        return [interval.strip() for interval in self.binance_intervals_raw.split(",") if interval.strip()]


settings = Settings()
