"""Market-baseline and closing-line (CLV) evaluation from stored price history.

The report answers a stricter question than the scorecard: did the model beat
the *market* at the moment the forecast was made, and did the price move in the
model's direction by settlement?  Entry market probability comes from the
forecast snapshot when available, otherwise from the latest stored price at or
before the forecast timestamp.  The closing price is the latest stored price at
or before the settlement timestamp — never a later, post-resolution print.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from prediction_ai.db.sqlite import SQLiteStore
from prediction_ai.evaluation.history_targets import (
    ForecastPriceTarget,
    HistoryTargetError,
    pair_price_target,
)
from prediction_ai.evaluation.scorecard import EvaluationRecord

MAX_PAGE_SIZE = 100
DEFAULT_MAX_PAGES = 200
_TRADE_RECOMMENDATIONS = frozenset({"BUY_YES", "BUY_NO"})


def _as_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _as_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _mean(values: Sequence[Decimal]) -> Decimal | None:
    if not values:
        return None
    return sum(values, Decimal("0")) / Decimal(len(values))


def _brier(probability: Decimal, outcome_yes: int) -> Decimal:
    return (probability - Decimal(outcome_yes)) ** 2


def _skill_score(model_brier: Decimal, market_brier: Decimal) -> Decimal | None:
    if market_brier <= 0:
        return None
    return Decimal("1.0") - (model_brier / market_brier)


class ClosingLineSample(BaseModel):
    """One forecast evaluated against the market at entry and at settlement."""

    model_config = ConfigDict(extra="forbid")

    forecast_id: str
    platform: str
    platform_market_id: str
    actual_outcome: str
    outcome_yes: int = Field(ge=0, le=1)
    recommendation: str
    model_probability_yes: Decimal = Field(ge=Decimal("0.0"), le=Decimal("1.0"))
    entry_market_probability_yes: Decimal | None = Field(
        default=None, ge=Decimal("0.0"), le=Decimal("1.0")
    )
    entry_observed_at: datetime | None = None
    entry_source: str | None = None
    closing_market_probability_yes: Decimal | None = Field(
        default=None, ge=Decimal("0.0"), le=Decimal("1.0")
    )
    closing_observed_at: datetime | None = None
    closing_source: str | None = None
    model_brier: Decimal
    entry_model_brier: Decimal | None = None
    entry_market_brier: Decimal | None = None
    closing_model_brier: Decimal | None = None
    closing_market_brier: Decimal | None = None
    clv: Decimal | None = None


class ClosingLineReport(BaseModel):
    """Aggregate market-relative accuracy and CLV."""

    model_config = ConfigDict(extra="forbid")

    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    category: str | None = None
    pairs_scanned: int = 0
    samples: int = 0
    skipped: int = 0
    skipped_reasons: dict[str, int] = Field(default_factory=dict)
    pages: int = 0
    truncated: bool = False
    entry_coverage: Decimal | None = None
    closing_coverage: Decimal | None = None
    model_brier: Decimal | None = None
    entry_model_brier: Decimal | None = None
    entry_market_brier: Decimal | None = None
    bss_vs_entry_market: Decimal | None = None
    closing_model_brier: Decimal | None = None
    closing_market_brier: Decimal | None = None
    bss_vs_closing_market: Decimal | None = None
    trade_count: int = 0
    mean_clv: Decimal | None = None
    positive_clv_rate: Decimal | None = None
    records: list[ClosingLineSample] = Field(default_factory=list)


def _skip(report: ClosingLineReport, reason: str) -> None:
    report.skipped += 1
    report.skipped_reasons[reason] = report.skipped_reasons.get(reason, 0) + 1


async def _latest_price(
    store: SQLiteStore,
    target: ForecastPriceTarget,
    cutoff: datetime,
) -> tuple[Decimal, datetime, str] | None:
    row = await store.get_latest_price_before(
        target.platform,
        target.platform_market_id,
        target.affirmative_outcome_id,
        cutoff,
    )
    if row is None:
        return None
    price = _as_decimal(row.get("price"))
    observed_at = _as_datetime(row.get("observed_at"))
    if price is None or observed_at is None:
        return None
    return price, observed_at, str(row.get("source") or "unknown")


def _entry_cost(record: EvaluationRecord) -> Decimal | None:
    recommendation = record.recommendation.upper()
    if recommendation == "BUY_YES":
        return record.effective_cost_yes or record.ask_yes
    if recommendation == "BUY_NO":
        return record.effective_cost_no or record.ask_no
    return None


def _clv(record: EvaluationRecord, closing_probability: Decimal) -> Decimal | None:
    cost = _entry_cost(record)
    if cost is None:
        return None
    if record.recommendation.upper() == "BUY_YES":
        closing_side = closing_probability
    elif record.recommendation.upper() == "BUY_NO":
        closing_side = Decimal("1.0") - closing_probability
    else:
        return None
    return closing_side - cost


async def build_closing_line_report(
    store: SQLiteStore,
    *,
    category: str | None = None,
    page_limit: int = MAX_PAGE_SIZE,
    max_pages: int = DEFAULT_MAX_PAGES,
) -> ClosingLineReport:
    """Page through resolved pairs and score them against stored market prices."""
    if not 1 <= page_limit <= MAX_PAGE_SIZE:
        raise ValueError(f"page_limit must be between 1 and {MAX_PAGE_SIZE}")
    if max_pages < 1:
        raise ValueError("max_pages must be at least 1")

    report = ClosingLineReport(category=category)
    cursor_at: str | None = None
    cursor_id: str | None = None

    while report.pages < max_pages:
        pairs = await store.list_resolved_forecast_feedback(
            limit=page_limit,
            category=category,
            after_forecasted_at=cursor_at,
            after_forecast_id=cursor_id,
        )
        report.pages += 1
        if not pairs:
            break

        for pair in pairs:
            report.pairs_scanned += 1
            try:
                target = pair_price_target(pair)
            except HistoryTargetError as exc:
                _skip(report, exc.reason)
                continue

            record = target.record
            outcome_yes = record.outcome_yes
            model_probability = record.probability_yes

            entry_probability = record.market_probability_yes
            entry_observed_at = target.entry_observed_at or target.forecasted_at
            entry_source = "forecast_snapshot" if entry_probability is not None else None
            if entry_probability is None:
                observed = await _latest_price(store, target, target.forecasted_at)
                if observed is not None:
                    entry_probability, entry_observed_at, entry_source = observed

            closing_probability: Decimal | None = None
            closing_observed_at: datetime | None = None
            closing_source: str | None = None
            closing_cutoff = target.settlement_at
            if closing_cutoff is None or closing_cutoff <= target.forecasted_at:
                closing_cutoff = target.resolved_at
            if closing_cutoff is not None and closing_cutoff > target.forecasted_at:
                observed = await _latest_price(store, target, closing_cutoff)
                if observed is not None:
                    closing_probability, closing_observed_at, closing_source = observed

            entry_market_brier = (
                _brier(entry_probability, outcome_yes)
                if entry_probability is not None
                else None
            )
            closing_market_brier = (
                _brier(closing_probability, outcome_yes)
                if closing_probability is not None
                else None
            )
            report.records.append(
                ClosingLineSample(
                    forecast_id=target.forecast_id,
                    platform=target.platform,
                    platform_market_id=target.platform_market_id,
                    actual_outcome=str(record.actual_outcome),
                    outcome_yes=outcome_yes,
                    recommendation=record.recommendation,
                    model_probability_yes=model_probability,
                    entry_market_probability_yes=entry_probability,
                    entry_observed_at=entry_observed_at if entry_probability is not None else None,
                    entry_source=entry_source,
                    closing_market_probability_yes=closing_probability,
                    closing_observed_at=closing_observed_at,
                    closing_source=closing_source,
                    model_brier=_brier(model_probability, outcome_yes),
                    entry_model_brier=(
                        _brier(model_probability, outcome_yes)
                        if entry_probability is not None
                        else None
                    ),
                    entry_market_brier=entry_market_brier,
                    closing_model_brier=(
                        _brier(model_probability, outcome_yes)
                        if closing_probability is not None
                        else None
                    ),
                    closing_market_brier=closing_market_brier,
                    clv=(
                        _clv(record, closing_probability)
                        if closing_probability is not None
                        else None
                    ),
                )
            )

        if len(pairs) < page_limit:
            break
        last = pairs[-1].get("forecast")
        if not isinstance(last, dict):
            report.truncated = True
            break
        next_at = last.get("forecasted_at")
        next_id = last.get("forecast_id") or last.get("id")
        if not next_at or not next_id:
            report.truncated = True
            break
        next_cursor = (str(next_at), str(next_id))
        if next_cursor == (cursor_at, cursor_id):
            report.truncated = True
            break
        cursor_at, cursor_id = next_cursor
    else:
        report.truncated = True

    report.samples = len(report.records)
    if report.samples:
        report.model_brier = _mean([sample.model_brier for sample in report.records])
        entry_samples = [
            sample for sample in report.records if sample.entry_market_brier is not None
        ]
        if entry_samples:
            report.entry_market_brier = _mean(
                [sample.entry_market_brier for sample in entry_samples if sample.entry_market_brier is not None]
            )
            report.entry_model_brier = _mean(
                [sample.model_brier for sample in entry_samples]
            )
            if report.entry_market_brier is not None and report.entry_model_brier is not None:
                report.bss_vs_entry_market = _skill_score(
                    report.entry_model_brier, report.entry_market_brier
                )
        closing_samples = [
            sample for sample in report.records if sample.closing_market_brier is not None
        ]
        if closing_samples:
            report.closing_market_brier = _mean(
                [
                    sample.closing_market_brier
                    for sample in closing_samples
                    if sample.closing_market_brier is not None
                ]
            )
            report.closing_model_brier = _mean(
                [sample.model_brier for sample in closing_samples]
            )
            if report.closing_market_brier is not None and report.closing_model_brier is not None:
                report.bss_vs_closing_market = _skill_score(
                    report.closing_model_brier, report.closing_market_brier
                )
        report.entry_coverage = Decimal(len(entry_samples)) / Decimal(report.samples)
        report.closing_coverage = Decimal(len(closing_samples)) / Decimal(report.samples)

    clv_values = [
        sample.clv
        for sample in report.records
        if sample.clv is not None
        and sample.recommendation.upper() in _TRADE_RECOMMENDATIONS
    ]
    report.trade_count = len(clv_values)
    if clv_values:
        report.mean_clv = _mean(clv_values)
        report.positive_clv_rate = Decimal(
            sum(1 for value in clv_values if value > 0)
        ) / Decimal(len(clv_values))

    return report


__all__ = [
    "ClosingLineReport",
    "ClosingLineSample",
    "DEFAULT_MAX_PAGES",
    "MAX_PAGE_SIZE",
    "build_closing_line_report",
]
