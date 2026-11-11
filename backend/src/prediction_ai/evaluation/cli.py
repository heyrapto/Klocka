"""CLI for scoring resolved forecast records.

Usage:
    python -m prediction_ai.evaluation.cli --input resolved.jsonl
    python -m prediction_ai.evaluation.cli --input resolved.jsonl --output scorecard.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from prediction_ai.evaluation.scorecard import score_jsonl


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Score resolved prediction forecasts from a JSONL file."
    )
    parser.add_argument("--input", required=True, help="Path to resolved JSONL records")
    parser.add_argument("--output", help="Optional path for the JSON scorecard")
    parser.add_argument(
        "--bins",
        type=int,
        default=10,
        help="Number of equal-width calibration bins (default: 10)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    scorecard = score_jsonl(args.input, n_bins=args.bins)
    serialized = json.dumps(scorecard, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(serialized + "\n", encoding="utf-8")
    else:
        sys.stdout.write(serialized + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
