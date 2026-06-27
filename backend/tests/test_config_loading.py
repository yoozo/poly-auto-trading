from __future__ import annotations

from pathlib import Path

from app.core.config import Settings, load_yaml_config


def test_settings_loads_yaml_and_env_secrets(tmp_path: Path) -> None:
    config_path = tmp_path / "app.yaml"
    env_path = tmp_path / ".env"
    config_path.write_text(
        """
app:
  env: yaml-env
  log_level: DEBUG
  log_format: json
  log_file_enabled: false
  log_file_path: /tmp/poly-test.log
  log_file_max_bytes: 2048
  log_file_backup_count: 2
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
                "POLYMARKET_CREDENTIALS_ENCRYPTION_KEY=secret-key",
                "APP_ENV=env-should-not-win",
            ]
        ),
        encoding="utf-8",
    )

    settings = Settings(config_path=config_path, _env_file=env_path)

    assert settings.app_env == "yaml-env"
    assert settings.log_level == "DEBUG"
    assert settings.log_format == "json"
    assert settings.log_file_enabled is False
    assert settings.log_file_path == "/tmp/poly-test.log"
    assert settings.log_file_max_bytes == 2048
    assert settings.log_file_backup_count == 2
    assert settings.database_url == "postgresql+asyncpg://example"
    assert settings.auth_password == "secret-password"
    assert settings.cors_origins == ["http://yaml.local"]
    assert settings.binance_rest_base_urls == ["https://binance-a.example", "https://binance-b.example"]
    assert settings.binance_ws_base_urls == ["wss://binance.example/ws"]
    assert settings.binance_intervals == ["1m", "4h"]
    assert settings.signal_interval_base_scores == {
        "1m": 3.0,
        "5m": 2.0,
        "15m": 3.0,
        "1h": 4.0,
        "4h": 8.0,
    }
    assert settings.signal_rsi_ema_diff_diff_bonus == [
        (10.0, 1.0),
        (12.0, 0.0),
        (15.0, 1.0),
        (19.0, 2.0),
        (20.0, 2.0),
    ]
    assert settings.signal_rsi_bonus == [
        (70.0, 1.0),
        (75.0, 1.0),
        (80.0, 2.0),
        (90.0, 3.0),
    ]
    assert settings.telegram_enabled_default is True
    assert settings.polymarket_account_refresh_seconds == 11
    assert settings.polymarket_credentials_encryption_key == "secret-key"


def test_yaml_config_merges_default_file_with_local_overrides(tmp_path: Path) -> None:
    config_path = tmp_path / "app.yaml"
    config_path.write_text(
        """
app:
  env: local-only
binance:
  intervals:
    - 1m
polymarket:
  account_refresh_seconds: 12
""",
        encoding="utf-8",
    )

    yaml_config = load_yaml_config(config_path)

    assert yaml_config.app.env == "local-only"
    assert yaml_config.app.name == "poly-auto-trading"
    assert yaml_config.binance.symbol == "BTCUSDT"
    assert yaml_config.binance.intervals == ["1m"]
    assert yaml_config.polymarket.gamma_base_url == "https://gamma-api.polymarket.com"
    assert yaml_config.polymarket.account_refresh_seconds == 12
