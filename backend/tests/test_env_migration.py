from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml


def load_migration_module():
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "migrate_env_to_yaml.py"
    spec = importlib.util.spec_from_file_location("migrate_env_to_yaml", script_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_moves_non_secret_values_and_keeps_secrets(tmp_path: Path) -> None:
    module = load_migration_module()
    env_path = tmp_path / ".env"
    yaml_path = tmp_path / "config" / "app.yaml"
    env_path.write_text(
        "\n".join(
            [
                "APP_ENV=from-env",
                "API_CORS_ORIGINS=http://a.local,http://b.local",
                "BINANCE_INTERVALS_RAW=1m,4h",
                "SIGNAL_RSI_BONUS_RAW=70:1,80:2",
                "DATABASE_URL=postgresql+asyncpg://secret",
                "AUTH_PASSWORD=secret-password",
                "POLYMARKET_CLOB_API_KEY=key",
                "UNKNOWN_SECRET=keep-me",
            ]
        ),
        encoding="utf-8",
    )

    result = module.migrate_env_to_yaml(env_path, yaml_path)

    assert "backup:" in result
    assert (tmp_path / ".env.bak").exists()
    yaml_payload = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    assert yaml_payload["app"]["env"] == "from-env"
    assert yaml_payload["cors"]["origins"] == ["http://a.local", "http://b.local"]
    assert yaml_payload["binance"]["intervals"] == ["1m", "4h"]
    assert yaml_payload["signals"]["rsi_bonus"] == {70.0: 1.0, 80.0: 2.0}
    env_payload = env_path.read_text(encoding="utf-8")
    assert "DATABASE_URL=postgresql+asyncpg://secret" in env_payload
    assert "AUTH_PASSWORD=secret-password" in env_payload
    assert "POLYMARKET_CLOB_API_KEY=key" in env_payload
    assert "UNKNOWN_SECRET=keep-me" in env_payload
    assert "APP_ENV=" not in env_payload
    assert "BINANCE_INTERVALS_RAW=" not in env_payload


def test_migration_dry_run_does_not_write_files(tmp_path: Path) -> None:
    module = load_migration_module()
    env_path = tmp_path / ".env"
    yaml_path = tmp_path / "config" / "app.yaml"
    original_env = "APP_ENV=from-env\nAUTH_PASSWORD=secret\n"
    env_path.write_text(original_env, encoding="utf-8")

    output = module.migrate_env_to_yaml(env_path, yaml_path, dry_run=True)

    assert "Dry run" in output
    assert env_path.read_text(encoding="utf-8") == original_env
    assert not yaml_path.exists()
    assert not (tmp_path / ".env.bak").exists()


def test_migration_merges_existing_yaml_and_timestamped_backup(tmp_path: Path) -> None:
    module = load_migration_module()
    env_path = tmp_path / ".env"
    yaml_path = tmp_path / "config" / "app.yaml"
    env_path.write_text("APP_ENV=from-env\nAUTH_PASSWORD=secret\n", encoding="utf-8")
    (tmp_path / ".env.bak").write_text("old backup", encoding="utf-8")
    yaml_path.parent.mkdir(parents=True)
    yaml_path.write_text(
        """
binance:
  intervals:
    - 5m
telegram:
  rsi_low: 25
""",
        encoding="utf-8",
    )

    module.migrate_env_to_yaml(env_path, yaml_path)

    backups = sorted(path.name for path in tmp_path.glob(".env.bak*"))
    assert backups[0] == ".env.bak"
    assert len(backups) == 2
    yaml_payload = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    assert yaml_payload["app"]["env"] == "from-env"
    assert yaml_payload["binance"]["intervals"] == ["5m"]
    assert yaml_payload["telegram"]["rsi_low"] == 25
