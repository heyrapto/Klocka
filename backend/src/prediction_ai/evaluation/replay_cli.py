"""CLI for deterministic point-in-time decision and blend replay.

Examples:
    python -m prediction_ai.evaluation.replay_cli \
        --sqlite-path ./var/prediction_intelligence.db \
        --category crypto \
        --thresholds 0.02,0.03,0.04,0.05,0.06,0.08

    python -m prediction_ai.evaluation.replay_cli \
        --sqlite-path ./var/prediction_intelligence.db \
        --category crypto \
        --max-quote-age-seconds 60 \
        --block-resolution-risk \
        --output replay.json

The replay uses only the immutable forecast snapshot.  It never fetches a
price, calls an LLM, or uses an observation after the forecast timestamp.
"""

from __future__ import annotations

import argparse
import asyncio
from decimal import Decimal, InvalidOperation
from pathlib import Path

from prediction_ai.core.config import Settings, get_settings
from prediction_ai.db.sqlite import SQLiteStore
from prediction_ai.evaluation.cli_support import configure_cli_logging, emit_json
from prediction_ai.evaluation.replay import (
    DEFAULT_MAX_PAGES,
    DEFAULT_THRESHOLDS,
    MAX_PAGE_SIZE,
    MAX_THRESHOLD_VARIANTS,
    ReplayDefaults,
    ReplayPolicy,
    build_replay_report,
)


def _decimal_argument(value: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise argparse.ArgumentTypeError(f"invalid decimal: {value}") from error
    if not parsed.is_finite():
        raise argparse.ArgumentTypeError("value must be finite")
    return parsed


def _threshold_list(value: str) -> list[Decimal]:
    thresholds: list[Decimal] = []
    for raw in value.split(","):
        stripped = raw.strip()
        if not stripped:
            continue
        parsed = _decimal_argument(stripped)
        if not Decimal("0.0") < parsed < Decimal("1.0"):
            raise argparse.ArgumentTypeError(
                f"threshold must lie strictly between 0 and 1: {stripped}"
            )
        thresholds.append(parsed)
    if not thresholds:
        raise argparse.ArgumentTypeError("at least one threshold is required")
    if len(thresholds) > MAX_THRESHOLD_VARIANTS:
        raise argparse.ArgumentTypeError(
            f"at most {MAX_THRESHOLD_VARIANTS} thresholds are allowed"
        )
    return thresholds


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Replay stored forecast decisions under alternative policy "
            "parameters and compare stored probability components."
        )
    )
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
        "--thresholds",
        type=_threshold_list,
        default=list(DEFAULT_THRESHOLDS),
        help=(
            "Comma-separated total required-edge thresholds to sweep "
            f"(default: {','.join(str(item) for item in DEFAULT_THRESHOLDS)})"
        ),
    )
    parser.add_argument(
        "--max-spread",
        type=_decimal_argument,
        help="Override the spread cap for the sweep (default: stored or settings)",
    )
    parser.add_argument(
        "--max-quote-age-seconds",
        type=_decimal_argument,
        help="Override the quote-age cap for the sweep (default: stored or settings)",
    )
    parser.add_argument(
        "--min-depth-usd",
        type=_decimal_argument,
        help="Override the minimum depth floor for the sweep (default: stored or settings)",
    )
    parser.add_argument(
        "--block-resolution-risk",
        action="store_true",
        help="Abstain when blocking resolution-risk flags are present",
    )
    parser.add_argument(
        "--include-samples",
        action="store_true",
        help="Include per-forecast baseline decisions in the report",
    )
    parser.add_argument(
        "--output",
        help="Optional JSON destination. Omit to print JSON to stdout.",
    )
    return parser


def _open_store(path: str) -> SQLiteStore:
    database = Path(path)
    if not database.is_file():
        raise FileNotFoundError(f"SQLite database does not exist: {database}")
    store = SQLiteStore(db_path=str(database))
    store.initialize()
    return store


def _defaults(settings: Settings) -> ReplayDefaults:
    return ReplayDefaults(
        required_edge=(
            Decimal(str(settings.DECISION_MIN_EDGE))
            + Decimal(str(settings.DECISION_UNCERTAINTY_MARGIN))
        ),
        max_spread=Decimal(str(settings.DECISION_MAX_SPREAD)),
        max_quote_age_seconds=Decimal(str(settings.DECISION_MAX_QUOTE_AGE_SECONDS)),
        min_depth_usd=Decimal(str(settings.DECISION_MIN_DEPTH_USD)),
        target_order_usd=Decimal(str(settings.DECISION_TARGET_ORDER_USD)),
        block_on_resolution_risk=False,
        kelly_fraction=Decimal(str(settings.DECISION_KELLY_FRACTION)),
    )


async def _run(args: argparse.Namespace, settings: Settings) -> dict[str, object]:
    store = _open_store(args.sqlite_path)
    sweep_policy = ReplayPolicy(
        name="sweep",
        max_spread=args.max_spread,
        max_quote_age_seconds=args.max_quote_age_seconds,
        min_depth_usd=args.min_depth_usd,
        block_on_resolution_risk=True if args.block_resolution_risk else None,
    )
    report = await build_replay_report(
        store,
        category=args.category,
        thresholds=args.thresholds,
        page_limit=args.page_limit,
        max_pages=args.max_pages,
        defaults=_defaults(settings),
        sweep_policy=sweep_policy,
        include_samples=args.include_samples,
    )
    return report.model_dump(mode="json")


def _validate_bounds(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if not 1 <= args.page_limit <= MAX_PAGE_SIZE:
        parser.error(f"--page-limit must be between 1 and {MAX_PAGE_SIZE}")
    if args.max_pages < 1:
        parser.error("--max-pages must be at least 1")
    for name in ("max_spread", "max_quote_age_seconds", "min_depth_usd"):
        value = getattr(args, name)
        if value is not None and value <= 0:
            parser.error(f"--{name.replace('_', '-')} must be positive")


def main(argv: list[str] | None = None) -> int:
    configure_cli_logging()
    parser = build_parser()
    args = parser.parse_args(argv)
    _validate_bounds(parser, args)

    try:
        payload = asyncio.run(_run(args, get_settings()))
    except FileNotFoundError as exc:
        parser.error(str(exc))

    emit_json(payload, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
