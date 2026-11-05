import logging
import sys
import warnings

import structlog


def setup_logging(log_level: str = "INFO", *, json_output: bool = True) -> None:
    """Configure structlog with request/analysis context propagation."""
    level = getattr(logging, log_level.upper(), logging.INFO)

    # Suppress noisy but harmless third-party warnings that add no signal
    # torch emits this on macOS since it doesn't support distributed-training redirects
    warnings.filterwarnings("ignore", message="NOTE: Redirects are currently not supported in MacOs")
    # huggingface_hub probes for optional directories that many models don't have
    # The 404 response is handled gracefully by the library itself; no action required
    logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
    logging.getLogger("huggingface_hub.file_download").setLevel(logging.ERROR)
    # httpx logs every outbound HTTP request at INFO level by default; suppress to WARNING
    # so internal HuggingFace Hub probing calls never appear in production logs
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpx._client").setLevel(logging.WARNING)

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=level,
        force=True,
    )

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    renderer: structlog.types.Processor
    if json_output:
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=sys.stdout.isatty())

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
