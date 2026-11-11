"""CLI for purged walk-forward calibration reports.

Examples:
    python -m prediction_ai.evaluation.calibrate_cli \
        --input resolved.jsonl \
        --output calibration_report.json \
        --artifact-dir calibration_review

    python -m prediction_ai.evaluation.calibrate_cli \
        --sqlite-path ./var/prediction_intelligence.db \
        --category crypto \
        --artifact-dir calibration_review

Artifacts are written with ``deployment_status="review_required"``.  This CLI
never edits environment configuration, model weights, or runtime thresholds.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from prediction_ai.db.sqlite import SQLiteStore
from prediction_ai.evaluation.calibration import (
    DEFAULT_ECE_TARGET,
    DEFAULT_FOLDS,
    DEFAULT_MIN_CLASS_SAMPLES,
    DEFAULT_MIN_TRAIN_SAMPLES,
    CalibrationArtifact,
    CalibrationMethod,
    CalibrationReport,
    InsufficientDataError,
    build_artifact,
    build_calibration_report,
)
from prediction_ai.evaluation.scorecard import EvaluationRecord, load_jsonl
from prediction_ai.evaluation.sqlite_export import (
    DEFAULT_MAX_PAGES,
    MAX_PAGE_SIZE,
    export_records_from_sqlite,
)

ARTIFACT_METHODS: tuple[CalibrationMethod, ...] = ("platt", "isotonic")


def _decimal_argument(value: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise argparse.ArgumentTypeError(f"invalid decimal: {value}") from error
    if not parsed.is_finite():
        raise argparse.ArgumentTypeError("value must be finite")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build a purged walk-forward calibration report from resolved "
            "forecast records. Artifacts are review-only."
        )
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", help="Resolved evaluation JSONL path")
    source.add_argument(
        "--sqlite-path",
        help="SQLite database path; records are exported with bounded pagination",
    )
    parser.add_argument(
        "--category",
        help="Case-insensitive feedback category filter (SQLite source only)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=MAX_PAGE_SIZE,
        help=f"SQLite rows per page, 1-{MAX_PAGE_SIZE} (default: {MAX_PAGE_SIZE})",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=DEFAULT_MAX_PAGES,
        help=f"SQLite page cap (default: {DEFAULT_MAX_PAGES})",
    )
    parser.add_argument(
        "--output",
        help="Report JSON path. Omit to write the report to stdout.",
    )
    parser.add_argument(
        "--artifact-dir",
        help="Optional directory for review-only calibrator artifacts",
    )
    parser.add_argument("--folds", type=int, default=DEFAULT_FOLDS)
    parser.add_argument("--min-train-samples", type=int, default=DEFAULT_MIN_TRAIN_SAMPLES)
    parser.add_argument("--min-class-samples", type=int, default=DEFAULT_MIN_CLASS_SAMPLES)
    parser.add_argument("--bins", type=int, default=10)
    parser.add_argument(
        "--ece-target",
        type=_decimal_argument,
        default=DEFAULT_ECE_TARGET,
        help="Maximum calibrated ECE for a positive recommendation (default: 0.05)",
    )
    return parser


async def _load_records(
    args: argparse.Namespace,
) -> tuple[list[EvaluationRecord], dict[str, Any]]:
    if args.input:
        return load_jsonl(args.input), {"source": "jsonl", "path": args.input}

    database = Path(args.sqlite_path)
    store = SQLiteStore(db_path=str(database))
    result = await export_records_from_sqlite(
        store,
        category=args.category,
        page_limit=args.limit,
        max_pages=args.max_pages,
    )
    summary = {
        "source": "sqlite",
        "path": str(database),
        "exported": result.exported,
        "skipped": result.skipped,
        "skipped_reasons": result.skipped_reasons,
        "pages": result.pages,
        "truncated": result.truncated,
        "category": result.category,
    }
    return list(result.records), summary


def _write_artifacts(
    records: list[EvaluationRecord],
    artifact_dir: Path,
    *,
    min_train_samples: int,
    min_class_samples: int,
) -> tuple[dict[str, str], dict[str, str]]:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifacts: dict[str, str] = {}
    skips: dict[str, str] = {}
    for method in ARTIFACT_METHODS:
        try:
            artifact: CalibrationArtifact = build_artifact(
                records,
                method,
                min_samples=min_train_samples,
                min_class_samples=min_class_samples,
            )
        except InsufficientDataError as error:
            skips[method] = error.reason
            continue
        path = artifact_dir / f"calibration_{method}.json"
        path.write_text(artifact.model_dump_json(indent=2) + "\n", encoding="utf-8")
        artifacts[method] = str(path)
    return artifacts, skips


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.input and args.category:
        parser.error("--category is only supported with --sqlite-path")
    if not 1 <= args.limit <= MAX_PAGE_SIZE:
        parser.error(f"--limit must be between 1 and {MAX_PAGE_SIZE}")
    if args.max_pages < 1:
        parser.error("--max-pages must be at least 1")
    if args.folds < 1:
        parser.error("--folds must be at least 1")
    if args.min_train_samples < 1:
        parser.error("--min-train-samples must be at least 1")
    if args.min_class_samples < 1:
        parser.error("--min-class-samples must be at least 1")
    if args.bins < 1:
        parser.error("--bins must be at least 1")
    if not (Decimal("0") < args.ece_target < Decimal("1")):
        parser.error("--ece-target must lie strictly between 0 and 1")
    if args.sqlite_path and not Path(args.sqlite_path).is_file():
        parser.error(f"--sqlite-path does not exist or is not a file: {args.sqlite_path}")

    records, source_summary = asyncio.run(_load_records(args))
    report = build_calibration_report(
        records,
        folds=args.folds,
        min_train_samples=args.min_train_samples,
        min_class_samples=args.min_class_samples,
        n_bins=args.bins,
        ece_target=args.ece_target,
    )

    artifacts: dict[str, str] = {}
    skips: dict[str, str] = {}
    if args.artifact_dir:
        artifacts, skips = _write_artifacts(
            records,
            Path(args.artifact_dir),
            min_train_samples=args.min_train_samples,
            min_class_samples=args.min_class_samples,
        )
    report = CalibrationReport.model_validate(
        report.model_copy(update={"artifacts": artifacts, "artifact_skips": skips}).model_dump()
    )

    serialized = report.model_dump_json(indent=2)
    if args.output:
        Path(args.output).write_text(serialized + "\n", encoding="utf-8")
    else:
        sys.stdout.write(serialized + "\n")

    summary = {
        "source": source_summary,
        "verdict": report.verdict,
        "recommended_method": report.recommended_method,
        "recommendation_reason": report.recommendation_reason,
        "eligible_records": report.dataset.eligible_count,
        "artifacts": report.artifacts,
        "artifact_skips": report.artifact_skips,
        "deployment_status": report.deployment_status,
    }
    sys.stderr.write(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
