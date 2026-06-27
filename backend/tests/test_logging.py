from __future__ import annotations

import json

import structlog

from app.core import logging as app_logging
from app.core.config import settings


def test_structured_logging_redacts_sensitive_fields(monkeypatch, capsys) -> None:
    monkeypatch.setattr(settings, "log_format", "json")
    monkeypatch.setattr(settings, "log_file_enabled", False)
    monkeypatch.setattr(settings, "log_level", "INFO")

    app_logging.configure_logging()
    structlog.get_logger("tests.logging").warning(
        "credential_update_failed",
        api_secret="secret-value",
        payload={
            "signature": "0xsensitive",
            "safe": "visible",
            "wallet": "0x0000000000000000000000000000000000000001",
        },
    )

    output = capsys.readouterr().out.strip()
    record = json.loads(output)
    assert record["event"] == "credential_update_failed"
    assert record["api_secret"] == "[REDACTED]"
    assert record["payload"]["signature"] == "[REDACTED]"
    assert record["payload"]["safe"] == "visible"
    assert record["payload"]["wallet"] == "0x0000...0001"
