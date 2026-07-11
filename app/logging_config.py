"""Structured logging configuration with JSON formatter."""

import json as _json
import logging
import sys
from datetime import UTC, datetime
from typing import Any


class JSONFormatter(logging.Formatter):
    """Formats log records as single-line JSON objects for structured logging."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=UTC
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exception"] = self.formatException(record.exc_info)

        if record.stack_info:
            log_entry["stack_info"] = self.formatStack(record.stack_info)

        # Include any extra fields attached to the record
        standard_attrs = {
            "name", "msg", "args", "created", "relativeCreated", "thread",
            "threadName", "msecs", "pathname", "filename", "module", "funcName",
            "levelno", "exc_text", "exc_info", "stack_info", "lineno", "levelname",
            "message", "processName", "process", "taskName",
        }
        for key, value in record.__dict__.items():
            if key not in standard_attrs and not key.startswith("_"):
                log_entry[key] = value

        return _json.dumps(log_entry, default=str)


def setup_logging(level: str = "INFO") -> None:
    """Configure structured JSON logging for the application.

    Args:
        level: The root log level (DEBUG, INFO, WARNING, ERROR, CRITICAL).
    """
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Remove any existing handlers to avoid duplicates on re-init
    root_logger.handlers.clear()

    # Console handler with JSON formatter
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.DEBUG)
    console_handler.setFormatter(JSONFormatter())
    root_logger.addHandler(console_handler)

    # Suppress noisy third-party loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Get a named logger instance.

    Args:
        name: Logger name, typically __name__ from the calling module.

    Returns:
        A configured logging.Logger instance.
    """
    return logging.getLogger(name)
