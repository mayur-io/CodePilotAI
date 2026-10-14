"""Observability: structured logging setup for CodePilotAI.

Uses ``structlog`` so every log line is machine-parseable JSON in production and
human-readable in development, while still flowing through Django's ``LOGGING``
config so third-party libraries are captured too.
"""

from __future__ import annotations

import json
import logging
import logging.config
import sys
from datetime import UTC, datetime
from typing import Any

import structlog


class JSONFormatter(logging.Formatter):
    """Django ``LOGGING`` formatter emitting one JSON object per line.

    ``configure_structlog`` installs a structlog-backed handler on the root
    logger, which normally does the rendering. This formatter exists so that the
    ``LOGGING`` dict in settings stays valid and any handler configured before
    ``AppConfig.ready()`` still produces structured output.
    """

    def format(self, record: logging.LogRecord) -> str:
        """Render a record as a single JSON line."""
        return json.dumps(
            {
                "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            },
            default=str,
            separators=(",", ":"),
        )


def json_formatter(_record: logging.LogRecord) -> str:
    """Format function usable as a Django ``LOGGING`` formatter."""
    return JSONFormatter().format(_record)


def configure_structlog(*, debug: bool, level: str = "INFO") -> None:
    """Route stdlib logging through structlog so all output shares one format."""
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    renderer: Any = structlog.processors.JSONRenderer()
    if debug:
        renderer = structlog.dev.ConsoleRenderer(colors=sys.stdout.isatty())

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.processors.format_exc_info,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
        foreign_pre_chain=shared_processors,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(logging.DEBUG if debug else getattr(logging, level.upper(), logging.INFO))


def get_logger(name: str) -> Any:
    """Return a bound structlog logger for ``name``."""
    return structlog.get_logger(name)
