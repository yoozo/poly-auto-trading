from __future__ import annotations

import logging
import logging.config
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

import structlog

from app.core.config import PROJECT_DIR, settings

SENSITIVE_KEYS = {
    "api_secret",
    "authorization",
    "cookie",
    "passphrase",
    "secret",
    "signature",
    "signed_order",
    "signedOrder",
    "token",
}
ETHEREUM_ADDRESS_PATTERN = re.compile(r"0x[a-fA-F0-9]{40}")


def configure_logging() -> None:
    level_name = settings.log_level.upper()
    level = getattr(logging, level_name, logging.INFO)
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]

    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    handlers: list[logging.Handler] = [
        logging.StreamHandler(sys.stdout),
    ]
    handlers[0].setFormatter(build_formatter(settings.log_format))

    if settings.log_file_enabled:
        log_path = resolve_log_path(settings.log_file_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_path,
            maxBytes=settings.log_file_max_bytes,
            backupCount=settings.log_file_backup_count,
            encoding="utf-8",
        )
        file_handler.setFormatter(build_formatter("json"))
        handlers.append(file_handler)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(level)
    for handler in handlers:
        handler.setLevel(level)
        root_logger.addHandler(handler)

    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(logger_name)
        logger.handlers.clear()
        logger.propagate = True


def build_formatter(log_format: str) -> logging.Formatter:
    renderer = (
        structlog.processors.JSONRenderer()
        if log_format.lower() == "json"
        else structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
    )
    return structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.ExtraAdder(),
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.format_exc_info,
        ],
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            redact_sensitive_values,
            renderer,
        ],
    )


def resolve_log_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return PROJECT_DIR / path


def redact_sensitive_values(_: Any, __: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    # 日志处理器是最后一道防线：调用方误传敏感字段时统一遮蔽，避免 signed order/secret 落盘。
    return {key: redact_value(key, value) for key, value in event_dict.items()}


def redact_value(key: str, value: Any) -> Any:
    if is_sensitive_key(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {child_key: redact_value(child_key, child_value) for child_key, child_value in value.items()}
    if isinstance(value, list):
        return [redact_value(key, item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_value(key, item) for item in value)
    if isinstance(value, str):
        return ETHEREUM_ADDRESS_PATTERN.sub(mask_address, value)
    return value


def is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(sensitive in normalized for sensitive in SENSITIVE_KEYS)


def mask_address(match: re.Match[str]) -> str:
    value = match.group(0)
    return f"{value[:6]}...{value[-4:]}"
