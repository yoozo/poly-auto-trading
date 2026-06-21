#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import yaml

PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_ENV_FILE = PROJECT_DIR / ".env"
DEFAULT_YAML_FILE = PROJECT_DIR / "config" / "app.yaml"

SENSITIVE_KEYS = {
    "DATABASE_URL",
    "AUTH_PASSWORD",
    "AUTH_SESSION_SECRET",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "POLYMARKET_CLOB_ADDRESS",
    "POLYMARKET_CLOB_API_KEY",
    "POLYMARKET_CLOB_SECRET",
    "POLYMARKET_CLOB_PASSPHRASE",
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate non-secret .env values into config/app.yaml.")
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--yaml-file", type=Path, default=DEFAULT_YAML_FILE)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    result = migrate_env_to_yaml(args.env_file, args.yaml_file, dry_run=args.dry_run)
    print(result)


def migrate_env_to_yaml(env_file: Path, yaml_file: Path, *, dry_run: bool = False) -> str:
    env_values = read_env_file(env_file)
    existing_yaml = read_yaml_file(yaml_file)
    migrated_yaml = merge_yaml_values(existing_yaml, env_values)
    remaining_env = remaining_env_values(env_values)

    if dry_run:
        return dry_run_summary(env_file, yaml_file, migrated_yaml, remaining_env)

    backup_path = backup_env_file(env_file)
    yaml_file.parent.mkdir(parents=True, exist_ok=True)
    yaml_file.write_text(dump_yaml(migrated_yaml), encoding="utf-8")
    env_file.write_text(dump_env(remaining_env), encoding="utf-8")
    return f"Migrated {env_file} -> {yaml_file}; backup: {backup_path}"


def read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        raise FileNotFoundError(f"Env file not found: {path}")
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        values[key] = unquote_env_value(value.strip())
    return values


def read_yaml_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"YAML file must contain an object: {path}")
    return payload


def merge_yaml_values(existing: dict[str, Any], env_values: dict[str, str]) -> dict[str, Any]:
    result = deep_copy(existing)
    for key, path, converter in env_mappings():
        if key not in env_values:
            continue
        set_nested(result, path, converter(env_values[key]))
    return result


def remaining_env_values(env_values: dict[str, str]) -> dict[str, str]:
    migrated_keys = {key for key, _, _ in env_mappings()}
    # 未识别的 key 保守留在 .env，避免误删外部部署注入的 secret。
    return {
        key: value
        for key, value in env_values.items()
        if key in SENSITIVE_KEYS or key not in migrated_keys
    }


def env_mappings() -> list[tuple[str, tuple[str, ...], Callable[[str], Any]]]:
    return [
        ("APP_ENV", ("app", "env"), str),
        ("LOG_LEVEL", ("app", "log_level"), str),
        ("API_CORS_ORIGINS", ("cors", "origins"), csv_list),
        ("AUTH_COOKIE_NAME", ("auth", "cookie_name"), str),
        ("AUTH_SESSION_TTL_SECONDS", ("auth", "session_ttl_seconds"), int),
        ("AUTH_COOKIE_SECURE", ("auth", "cookie_secure"), bool_value),
        ("BINANCE_SYMBOL", ("binance", "symbol"), str),
        ("BINANCE_REST_BASE_URL", ("binance", "rest_base_urls"), lambda value: [strip_url(value)]),
        ("BINANCE_WS_BASE_URL", ("binance", "ws_base_urls"), lambda value: [strip_url(value)]),
        ("BINANCE_REST_BASE_URLS_RAW", ("binance", "rest_base_urls"), csv_list),
        ("BINANCE_WS_BASE_URLS_RAW", ("binance", "ws_base_urls"), csv_list),
        ("BINANCE_INTERVALS_RAW", ("binance", "intervals"), csv_list),
        ("CANDLE_HISTORY_LIMIT", ("binance", "candle_history_limit"), int),
        ("BINANCE_WS_ENABLED", ("binance", "ws_enabled"), bool_value),
        ("POLYMARKET_GAMMA_BASE_URL", ("polymarket", "gamma_base_url"), strip_url),
        ("POLYMARKET_DATA_BASE_URL", ("polymarket", "data_base_url"), strip_url),
        ("POLYMARKET_CLOB_BASE_URL", ("polymarket", "clob_base_url"), strip_url),
        ("POLYMARKET_WS_ENABLED", ("polymarket", "ws_enabled"), bool_value),
        ("POLYMARKET_WS_MARKET_URL", ("polymarket", "ws_market_url"), strip_url),
        ("POLYMARKET_USER_WS_ENABLED", ("polymarket", "user_ws_enabled"), bool_value),
        ("POLYMARKET_WS_USER_URL", ("polymarket", "ws_user_url"), strip_url),
        ("POLYMARKET_POSITION_WALLET", ("polymarket", "position_wallet"), str),
        ("POLYMARKET_ACCOUNT_REFRESH_SECONDS", ("polymarket", "account_refresh_seconds"), int),
        ("POLYMARKET_MARKET_REFRESH_SECONDS", ("polymarket", "market_refresh_seconds"), int),
        (
            "POLYMARKET_MARKET_BOUNDARY_REFRESH_WINDOW_SECONDS",
            ("polymarket", "market_boundary_refresh_window_seconds"),
            int,
        ),
        ("POLYMARKET_MARKET_EMPTY_RETRY_SECONDS", ("polymarket", "market_empty_retry_seconds"), int),
        (
            "POLYMARKET_MARKET_SIGNAL_REFRESH_MIN_SECONDS",
            ("polymarket", "market_signal_refresh_min_seconds"),
            int,
        ),
        (
            "POLYMARKET_WS_BROADCAST_INTERVAL_SECONDS",
            ("polymarket", "ws_broadcast_interval_seconds"),
            float,
        ),
        ("SIGNAL_INTERVAL_BASE_SCORES_RAW", ("signals", "interval_base_scores"), mapping_value),
        (
            "SIGNAL_RSI_EMA_DIFF_DIFF_BONUS_RAW",
            ("signals", "rsi_ema_diff_diff_bonus"),
            numeric_mapping_value,
        ),
        ("SIGNAL_RSI_BONUS_RAW", ("signals", "rsi_bonus"), numeric_mapping_value),
        ("TELEGRAM_ENABLED_DEFAULT", ("telegram", "enabled_default"), bool_value),
        ("TELEGRAM_RSI_LOW", ("telegram", "rsi_low"), float),
        ("TELEGRAM_RSI_HIGH", ("telegram", "rsi_high"), float),
        ("TELEGRAM_RSI_EXTREME_LOW", ("telegram", "rsi_extreme_low"), float),
        ("TELEGRAM_RSI_EXTREME_HIGH", ("telegram", "rsi_extreme_high"), float),
        ("TELEGRAM_RSI_EMA_DIFF_ABS", ("telegram", "rsi_ema_diff_abs"), float),
        ("TELEGRAM_CLOSE_WARNING_SECONDS", ("telegram", "close_warning_seconds"), int),
    ]


def backup_env_file(env_file: Path) -> Path:
    backup = env_file.with_name(f"{env_file.name}.bak")
    if backup.exists():
        suffix = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = env_file.with_name(f"{env_file.name}.bak.{suffix}")
    shutil.copy2(env_file, backup)
    return backup


def dry_run_summary(
    env_file: Path,
    yaml_file: Path,
    yaml_payload: dict[str, Any],
    env_payload: dict[str, str],
) -> str:
    return "\n".join(
        [
            f"Dry run for {env_file} -> {yaml_file}",
            "",
            "YAML preview:",
            dump_yaml(yaml_payload).rstrip(),
            "",
            "Secrets .env preview:",
            dump_env(env_payload).rstrip(),
        ]
    )


def dump_yaml(payload: dict[str, Any]) -> str:
    return yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)


def dump_env(values: dict[str, str]) -> str:
    lines = [f"{key}={quote_env_value(value)}" for key, value in values.items()]
    return "\n".join(lines) + ("\n" if lines else "")


def set_nested(payload: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    cursor = payload
    for segment in path[:-1]:
        existing = cursor.get(segment)
        if not isinstance(existing, dict):
            existing = {}
            cursor[segment] = existing
        cursor = existing
    cursor[path[-1]] = value


def csv_list(value: str) -> list[str]:
    return [strip_url(item) for item in value.split(",") if item.strip()]


def mapping_value(value: str) -> dict[str, float]:
    result: dict[str, float] = {}
    for item in value.split(","):
        if not item.strip():
            continue
        key, raw_number = item.split(":", 1)
        result[key.strip()] = float(raw_number.strip())
    return result


def numeric_mapping_value(value: str) -> dict[float, float]:
    return {float(key): number for key, number in mapping_value(value).items()}


def bool_value(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def strip_url(value: str) -> str:
    return value.strip().rstrip("/")


def unquote_env_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def quote_env_value(value: str) -> str:
    if not value:
        return ""
    if any(char.isspace() for char in value) or "#" in value:
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


def deep_copy(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: deep_copy(item) for key, item in value.items()}
    if isinstance(value, list):
        return [deep_copy(item) for item in value]
    return value


if __name__ == "__main__":
    main()
