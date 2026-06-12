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
    binance_rest_base_urls_raw: str | None = None
    binance_ws_base_urls_raw: str | None = None
    binance_intervals_raw: str = "1m,5m,15m,30m,1h,4h,1d"
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
