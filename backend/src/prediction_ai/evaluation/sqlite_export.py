"""Bounded export of forecast/feedback pairs from the SQLite ledger.

The repository query caps every page at 100 rows.  This module only advances the
cursor and aggregates results; it never relaxes that bound and never fetches a
label that is not already persisted.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from prediction_ai.db.sqlite import SQLiteStore
from prediction_ai.evaluation.exporter import (
    export_pairs,
    forecast_feedback_to_record,
)
from prediction_ai.evaluation.scorecard import EvaluationRecord

MAX_PAGE_SIZE = 100
DEFAULT_MAX_PAGES = 200


class SqliteExportResult(BaseModel):
    """Records plus an auditable account of what was skipped or truncated."""

    model_config = ConfigDict(extra="forbid")

    records: list[EvaluationRecord]
    exported: int
    skipped: int
    skipped_reasons: dict[str, int] = Field(default_factory=dict)
    pages: int
    truncated: bool
    category: str | None = None


async def export_records_from_sqlite(
    store: SQLiteStore,
    *,
    category: str | None = None,
    page_limit: int = MAX_PAGE_SIZE,
    max_pages: int = DEFAULT_MAX_PAGES,
) -> SqliteExportResult:
    """Page through resolved pairs in ``(forecasted_at, forecast_id)`` order."""
    if not 1 <= page_limit <= MAX_PAGE_SIZE:
        raise ValueError(f"page_limit must be between 1 and {MAX_PAGE_SIZE}")
    if max_pages < 1:
        raise ValueError("max_pages must be at least 1")

    records: list[EvaluationRecord] = []
    reasons: dict[str, int] = {}
    pages = 0
    truncated = False
    cursor_at: str | None = None
    cursor_id: str | None = None

    while pages < max_pages:
        pairs = await store.list_resolved_forecast_feedback(
            limit=page_limit,
            category=category,
            after_forecasted_at=cursor_at,
            after_forecast_id=cursor_id,
        )
        pages += 1
        page_records, summary = export_pairs(pairs, page_limit=page_limit)
        records.extend(page_records)
        for reason, count in summary.skipped_reasons.items():
            reasons[reason] = reasons.get(reason, 0) + count

        if len(pairs) < page_limit:
            break
        next_cursor = summary.next_cursor
        if next_cursor is None:
            break
        next_at = next_cursor["after_forecasted_at"]
        next_id = next_cursor["after_forecast_id"]
        if next_at == cursor_at and next_id == cursor_id:
            # Defensive: a cursor that does not advance would loop forever.
            truncated = True
            break
        cursor_at, cursor_id = next_at, next_id
    else:
        truncated = True

    return SqliteExportResult(
        records=records,
        exported=len(records),
        skipped=sum(reasons.values()),
        skipped_reasons=reasons,
        pages=pages,
        truncated=truncated,
        category=category,
    )


__all__ = [
    "DEFAULT_MAX_PAGES",
    "MAX_PAGE_SIZE",
    "SqliteExportResult",
    "export_records_from_sqlite",
    "export_pairs",
    "forecast_feedback_to_record",
]
