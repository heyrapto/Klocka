"""CLI for historical market-price ingestion and market-relative evaluation.

Examples:
    python -m prediction_ai.evaluation.history_cli backfill \
        --sqlite-path ./var/prediction_intelligence.db \
        --category crypto \
        --lookback-hours 6

    python -m prediction_ai.evaluation.history_cli report \
        --sqlite-path ./var/prediction_intelligence.db \
        --category crypto \
        --output closing_line.json
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from prediction_ai.core.config import Settings, get_settings
from prediction_ai.db.sqlite import SQLiteStore
from prediction_ai.evaluation.cli_support import configure_cli_logging, emit_json
from prediction_ai.evaluation.closing_line import (
    DEFAULT_MAX_PAGES,
    MAX_PAGE_SIZE,
    build_closing_line_report,
)
from prediction_ai.evaluation.history_backfill import (
    DEFAULT_LOOKBACK_HOURS,
    MAX_FIDELITY_MINUTES,
    MAX_LOOKBACK_HOURS,
    backfill_polymarket_price_history,
)
from prediction_ai.integrations.http import ProviderHttpClient
from prediction_ai.integrations.network import build_resilient_client
from prediction_ai.integrations.price_history import PolymarketPriceHistoryClient


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--sqlite-path",
        required=True,
        help="Path to the SQLite database file",
    )
    parser.add_argument(
        "--category",
        help="Optional case-insensitive feedback category filter (e.g. crypto)",
    )
    parser.add_argument(
        "--page-limit",
        type=int,
        default=MAX_PAGE_SIZE,
        help=f"Rows per page, 1-{MAX_PAGE_SIZE} (default: {MAX_PAGE_SIZE})",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=DEFAULT_MAX_PAGES,
        help=f"Hard page cap (default: {DEFAULT_MAX_PAGES})",
    )
    parser.add_argument(
        "--output",
        help="Optional JSON destination. Omit to print JSON to stdout.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Backfill historical prices or report closing-line accuracy."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    backfill = subparsers.add_parser(
        "backfill",
        help="Fetch Polymarket CLOB history for resolved forecasts",
    )
    _add_common_arguments(backfill)
    backfill.add_argument(
        "--lookback-hours",
        type=float,
        default=DEFAULT_LOOKBACK_HOURS,
        help=(
            "Hours of pre-forecast context to fetch "
            f"(0-{MAX_LOOKBACK_HOURS:g}, default: {DEFAULT_LOOKBACK_HOURS:g})"
        ),
    )
    backfill.add_argument(
        "--fidelity-minutes",
        type=int,
        help=(
            "Optional fixed CLOB fidelity in minutes "
            f"(1-{MAX_FIDELITY_MINUTES}); default adapts to window length"
        ),
    )

    report = subparsers.add_parser(
        "report",
        help="Score forecasts against entry and closing market prices",
    )
    _add_common_arguments(report)
    report.add_argument(
        "--include-samples",
        action="store_true",
        help="Include per-forecast samples in the JSON report",
    )
    return parser


def _open_store(path: str) -> SQLiteStore:
    database = Path(path)
    if not database.is_file():
        raise FileNotFoundError(f"SQLite database does not exist: {database}")
    store = SQLiteStore(db_path=str(database))
    store.initialize()
    return store


def _validate_bounds(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if not 1 <= args.page_limit <= MAX_PAGE_SIZE:
        parser.error(f"--page-limit must be between 1 and {MAX_PAGE_SIZE}")
    if args.max_pages < 1:
        parser.error("--max-pages must be at least 1")


async def _run_backfill(args: argparse.Namespace, settings: Settings) -> dict[str, object]:
    store = _open_store(args.sqlite_path)
    async with build_resilient_client(settings) as http_client:
        clob = ProviderHttpClient(
            http_client,
            settings,
            provider="polymarket_clob",
            base_url=settings.POLYMARKET_CLOB_API_URL,
        )
        client = PolymarketPriceHistoryClient(settings, clob)
        summary = await backfill_polymarket_price_history(
            store,
            client,
            category=args.category,
            lookback_hours=args.lookback_hours,
            page_limit=args.page_limit,
            max_pages=args.max_pages,
            fidelity_minutes=args.fidelity_minutes,
        )
    return summary.model_dump(mode="json")


async def _run_report(args: argparse.Namespace) -> dict[str, object]:
    store = _open_store(args.sqlite_path)
    report = await build_closing_line_report(
        store,
        category=args.category,
        page_limit=args.page_limit,
        max_pages=args.max_pages,
    )
    payload = report.model_dump(mode="json")
    if not args.include_samples:
        payload.pop("records", None)
    return payload


def main(argv: list[str] | None = None) -> int:
    configure_cli_logging()
    parser = build_parser()
    args = parser.parse_args(argv)
    _validate_bounds(parser, args)

    try:
        if args.command == "backfill":
            if not 0 < args.lookback_hours <= MAX_LOOKBACK_HOURS:
                parser.error(
                    f"--lookback-hours must be in (0, {MAX_LOOKBACK_HOURS:g}]"
                )
            if (
                args.fidelity_minutes is not None
                and not 1 <= args.fidelity_minutes <= MAX_FIDELITY_MINUTES
            ):
                parser.error(
                    f"--fidelity-minutes must be between 1 and {MAX_FIDELITY_MINUTES}"
                )
            payload = asyncio.run(_run_backfill(args, get_settings()))
        else:
            payload = asyncio.run(_run_report(args))
    except FileNotFoundError as exc:
        parser.error(str(exc))

    emit_json(payload, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
