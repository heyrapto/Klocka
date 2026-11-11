"""Export resolved forecast/feedback pairs from SQLite into evaluation JSONL.

Usage:
    python -m prediction_ai.evaluation.export_cli \
        --sqlite-path ./var/prediction_intelligence.db \
        --category crypto \
        --output resolved.jsonl \
        --summary export_summary.json

Every page is capped at 100 rows and pagination is keyed on
``(forecasted_at, forecast_id)`` so repeated exports are stable.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from prediction_ai.db.sqlite import SQLiteStore
from prediction_ai.evaluation.exporter import to_jsonl, write_jsonl
from prediction_ai.evaluation.sqlite_export import (
    DEFAULT_MAX_PAGES,
    MAX_PAGE_SIZE,
    SqliteExportResult,
    export_records_from_sqlite,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export resolved forecast/feedback pairs from SQLite."
    )
    parser.add_argument(
        "--sqlite-path",
        required=True,
        help="Path to the SQLite database (read-only intent; the file must exist)",
    )
    parser.add_argument(
        "--category",
        help="Optional case-insensitive feedback category filter (e.g. crypto)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=MAX_PAGE_SIZE,
        help=f"Rows per page, 1-{MAX_PAGE_SIZE} (default: {MAX_PAGE_SIZE})",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=DEFAULT_MAX_PAGES,
        help=f"Hard page cap for one export (default: {DEFAULT_MAX_PAGES})",
    )
    parser.add_argument(
        "--output",
        help="Destination JSONL path. Omit to write JSONL to stdout.",
    )
    parser.add_argument(
        "--summary",
        help="Optional path for the JSON export summary. Always printed to stderr.",
    )
    return parser


def _summary_payload(result: SqliteExportResult) -> dict[str, object]:
    return {
        "exported": result.exported,
        "skipped": result.skipped,
        "skipped_reasons": result.skipped_reasons,
        "pages": result.pages,
        "truncated": result.truncated,
        "category": result.category,
    }


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not 1 <= args.limit <= MAX_PAGE_SIZE:
        parser.error(f"--limit must be between 1 and {MAX_PAGE_SIZE}")
    if args.max_pages < 1:
        parser.error("--max-pages must be at least 1")

    database = Path(args.sqlite_path)
    if not database.is_file():
        parser.error(f"--sqlite-path does not exist or is not a file: {database}")

    store = SQLiteStore(db_path=str(database))
    result = asyncio.run(
        export_records_from_sqlite(
            store,
            category=args.category,
            page_limit=args.limit,
            max_pages=args.max_pages,
        )
    )

    if args.output:
        write_jsonl(result.records, args.output)
    else:
        sys.stdout.write(to_jsonl(result.records))

    payload = _summary_payload(result)
    serialized = json.dumps(payload, indent=2, sort_keys=True)
    if args.summary:
        Path(args.summary).write_text(serialized + "\n", encoding="utf-8")
    sys.stderr.write(serialized + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
