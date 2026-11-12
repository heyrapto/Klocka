"""Bounded backfill of historical market prices from Polymarket's CLOB.

Polymarket publishes ``GET /prices-history`` for a CLOB token id.  Bayse does
not publish a documented historical-price endpoint, so Bayse history is captured
forward from the decision-time snapshots written by the analysis runner.

Backfill is deliberately conservative:

* only already-resolved forecast/feedback pairs are visited;
* pagination is capped and keyed on ``(forecasted_at, forecast_id)``;
* the requested window is bounded and downsampled before persistence;
* a missing token id or provider failure skips one outcome, never the page.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Protocol

import structlog
from pydantic import BaseModel, ConfigDict, Field

from prediction_ai.core.exceptions import (
    CircuitOpenException,
    ProviderTimeoutException,
    UpstreamProviderException,
)
from prediction_ai.db.sqlite import SQLiteStore
from prediction_ai.evaluation.history_targets import (
    HistoryTargetError,
    pair_price_target,
)
from prediction_ai.schemas.price_history import OutcomePriceHistory

logger = structlog.get_logger(__name__)

MAX_PAGE_SIZE = 100
DEFAULT_MAX_PAGES = 200
DEFAULT_LOOKBACK_HOURS = 6.0
MAX_LOOKBACK_HOURS = 24.0 * 7
MAX_BACKFILL_WINDOW = timedelta(days=365)
MAX_FIDELITY_MINUTES = 1440


class OutcomeHistoryFetcher(Protocol):
    """Minimal client contract, kept narrow for deterministic tests."""

    async def fetch_outcome_history(
        self,
        *,
        platform_market_id: str,
        outcome_id: str,
        token_id: str,
        label: str | None,
        start: datetime,
        end: datetime,
        fidelity_minutes: int | None = None,
    ) -> OutcomePriceHistory: ...


class BackfillSummary(BaseModel):
    """Auditable account of what a backfill did and skipped."""

    model_config = ConfigDict(extra="forbid")

    pairs_scanned: int = 0
    pairs_requested: int = 0
    outcomes_requested: int = 0
    points_fetched: int = 0
    points_inserted: int = 0
    skipped: int = 0
    skipped_reasons: dict[str, int] = Field(default_factory=dict)
    fetch_failures: int = 0
    pages: int = 0
    truncated: bool = False
    category: str | None = None


def _skip(summary: BackfillSummary, reason: str) -> None:
    summary.skipped += 1
    summary.skipped_reasons[reason] = summary.skipped_reasons.get(reason, 0) + 1


def _select_fidelity(window: timedelta, requested: int | None) -> int:
    if requested is not None:
        if not 1 <= requested <= MAX_FIDELITY_MINUTES:
            raise ValueError(
                f"fidelity_minutes must be between 1 and {MAX_FIDELITY_MINUTES}"
            )
        return requested

    minutes = max(1.0, window.total_seconds() / 60.0)
    if minutes <= 24 * 60:
        return 1
    if minutes <= 7 * 24 * 60:
        return 5
    if minutes <= 90 * 24 * 60:
        return 15
    return 60


def _cursor(pair: dict[str, Any]) -> tuple[str, str] | None:
    forecast = pair.get("forecast")
    if not isinstance(forecast, dict):
        return None
    forecasted_at = forecast.get("forecasted_at")
    forecast_id = forecast.get("forecast_id") or forecast.get("id")
    if not forecasted_at or not forecast_id:
        return None
    return str(forecasted_at), str(forecast_id)


async def backfill_polymarket_price_history(
    store: SQLiteStore,
    client: OutcomeHistoryFetcher,
    *,
    category: str | None = None,
    lookback_hours: float = DEFAULT_LOOKBACK_HOURS,
    page_limit: int = MAX_PAGE_SIZE,
    max_pages: int = DEFAULT_MAX_PAGES,
    fidelity_minutes: int | None = None,
) -> BackfillSummary:
    """Fetch and persist Polymarket history for resolved crypto forecasts."""
    if not 1 <= page_limit <= MAX_PAGE_SIZE:
        raise ValueError(f"page_limit must be between 1 and {MAX_PAGE_SIZE}")
    if max_pages < 1:
        raise ValueError("max_pages must be at least 1")
    if not 0 < lookback_hours <= MAX_LOOKBACK_HOURS:
        raise ValueError(
            f"lookback_hours must be in (0, {MAX_LOOKBACK_HOURS}]"
        )
    if fidelity_minutes is not None and not 1 <= fidelity_minutes <= MAX_FIDELITY_MINUTES:
        raise ValueError(
            f"fidelity_minutes must be between 1 and {MAX_FIDELITY_MINUTES}"
        )

    lookback = timedelta(hours=lookback_hours)
    summary = BackfillSummary(category=category)
    cursor_at: str | None = None
    cursor_id: str | None = None

    while summary.pages < max_pages:
        pairs = await store.list_resolved_forecast_feedback(
            limit=page_limit,
            category=category,
            after_forecasted_at=cursor_at,
            after_forecast_id=cursor_id,
        )
        summary.pages += 1
        if not pairs:
            break

        for pair in pairs:
            summary.pairs_scanned += 1
            try:
                target = pair_price_target(pair)
            except HistoryTargetError as exc:
                _skip(summary, exc.reason)
                continue

            if target.platform != "polymarket":
                _skip(summary, "platform_not_supported")
                continue

            end = (
                target.settlement_at
                if target.settlement_at is not None
                and target.settlement_at > target.forecasted_at
                else target.resolved_at
            )
            if end is None or end <= target.forecasted_at:
                # No usable settlement timestamp: capture only the entry
                # lookback instead of inventing a closing time.
                end = target.forecasted_at + lookback
            start = target.forecasted_at - lookback
            if end - start > MAX_BACKFILL_WINDOW:
                # Preserve the pre-forecast context; a longer market simply has
                # no closing-line coverage until forward snapshots exist.
                end = start + MAX_BACKFILL_WINDOW
            if end <= start:
                _skip(summary, "empty_window")
                continue

            fidelity = _select_fidelity(end - start, fidelity_minutes)
            summary.pairs_requested += 1
            for outcome in target.outcomes:
                if not outcome.token_id:
                    _skip(summary, "missing_token_id")
                    continue
                summary.outcomes_requested += 1
                try:
                    history = await client.fetch_outcome_history(
                        platform_market_id=target.platform_market_id,
                        outcome_id=outcome.outcome_id,
                        token_id=outcome.token_id,
                        label=outcome.label or None,
                        start=start,
                        end=end,
                        fidelity_minutes=fidelity,
                    )
                except (
                    CircuitOpenException,
                    ProviderTimeoutException,
                    UpstreamProviderException,
                    ValueError,
                ) as exc:
                    summary.fetch_failures += 1
                    logger.warning(
                        "Price-history fetch failed",
                        platform=target.platform,
                        platform_market_id=target.platform_market_id,
                        outcome_id=outcome.outcome_id,
                        error=type(exc).__name__,
                    )
                    continue

                summary.points_fetched += len(history.points)
                summary.points_inserted += await store.record_price_history(history)

        if len(pairs) < page_limit:
            break
        next_cursor = _cursor(pairs[-1])
        if next_cursor is None:
            summary.truncated = True
            break
        next_at, next_id = next_cursor
        if next_at == cursor_at and next_id == cursor_id:
            summary.truncated = True
            break
        cursor_at, cursor_id = next_at, next_id
    else:
        summary.truncated = True

    return summary


__all__ = [
    "BackfillSummary",
    "DEFAULT_LOOKBACK_HOURS",
    "DEFAULT_MAX_PAGES",
    "MAX_BACKFILL_WINDOW",
    "MAX_PAGE_SIZE",
    "OutcomeHistoryFetcher",
    "backfill_polymarket_price_history",
]
