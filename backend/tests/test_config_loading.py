from __future__ import annotations

from pathlib import Path

from app.core.config import Settings


def test_settings_loads_yaml_and_env_secrets(tmp_path: Path) -> None:
    config_path = tmp_path / "app.yaml"
    env_path = tmp_path / ".env"
    config_path.write_text(
        """
app:
  env: yaml-env
  log_level: DEBUG
cors:
  origins:
    - http://yaml.local
binance:
  rest_base_urls:
    - https://binance-a.example
    - https://binance-b.example
  ws_base_urls:
    - wss://binance.example/ws
  intervals:
    - 1m
    - 4h
signals:
  interval_base_scores:
    1m: 3
    4h: 8
  rsi_ema_diff_diff_bonus:
    10: 1
    20: 2
  rsi_bonus:
    75: 1
telegram:
  enabled_default: true
polymarket:
  account_refresh_seconds: 11
""",
        encoding="utf-8",
    )
    env_path.write_text(
        "\n".join(
            [
                "DATABASE_URL=postgresql+asyncpg://example",
                "AUTH_PASSWORD=secret-password",
                "AUTH_SESSION_SECRET=secret-session",
                "POLYMARKET_CLOB_API_KEY=key",
                "POLYMARKET_CLOB_SECRET=secret",
                "POLYMARKET_CLOB_PASSPHRASE=pass",
                "POLYMARKET_CLOB_ADDRESS=0x0000000000000000000000000000000000000001",
                "APP_ENV=env-should-not-win",
            ]
        ),
        encoding="utf-8",
    )

    settings = Settings(config_path=config_path, _env_file=env_path)

    assert settings.app_env == "yaml-env"
    assert settings.log_level == "DEBUG"
    assert settings.database_url == "postgresql+asyncpg://example"
    assert settings.auth_password == "secret-password"
    assert settings.cors_origins == ["http://yaml.local"]
    assert settings.binance_rest_base_urls == ["https://binance-a.example", "https://binance-b.example"]
    assert settings.binance_ws_base_urls == ["wss://binance.example/ws"]
    assert settings.binance_intervals == ["1m", "4h"]
    assert settings.signal_interval_base_scores == {"1m": 3.0, "4h": 8.0}
    assert settings.signal_rsi_ema_diff_diff_bonus == [(10.0, 1.0), (20.0, 2.0)]
    assert settings.signal_rsi_bonus == [(75.0, 1.0)]
    assert settings.telegram_enabled_default is True
    assert settings.polymarket_account_refresh_seconds == 11
    assert settings.polymarket_clob_api_key == "key"
