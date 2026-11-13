"""Shared helpers for evaluation CLIs that emit JSON on stdout."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import structlog


def configure_cli_logging() -> None:
    """Keep JSON/stdout clean by routing structured logs to stderr."""
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stderr,
        level=logging.INFO,
        force=True,
    )
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.dev.ConsoleRenderer(colors=False),
        ],
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=False,
    )


def emit_json(payload: dict[str, object], output: str | None) -> None:
    """Write indented JSON to a file or stdout, echoing to stderr on file output."""
    serialized = json.dumps(payload, indent=2, sort_keys=True, default=str)
    if output:
        Path(output).write_text(serialized + "\n", encoding="utf-8")
        sys.stderr.write(serialized + "\n")
    else:
        sys.stdout.write(serialized + "\n")


__all__ = ["configure_cli_logging", "emit_json"]
