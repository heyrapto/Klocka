"""Deterministic forecast scoring and calibration diagnostics.

The scorecard answers three separate questions and never conflates them:

1. Were the probability forecasts accurate?  (Brier, log loss, BSS vs market)
2. Were they calibrated?  (reliability bins, ECE, MCE)
3. Did the decision policy make money after real execution costs?

Abstentions are included in probability scoring because abstaining does not
erase the forecast, but they are excluded from trade PnL.  ``NO_TRADE`` is
never counted as a correct prediction.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

OutcomeLabel = Literal[
    "YES",
    "NO",
    "UP",
    "DOWN",
    "HIGHER",
    "LOWER",
    "BUY UP",
    "BUY DOWN",
    "1",
    "0",
]

_AFFIRMATIVE_OUTCOMES = frozenset({"YES", "UP", "HIGHER", "BUY UP", "1", "TRUE"})
_NEGATIVE_OUTCOMES = frozenset({"NO", "DOWN", "LOWER", "BUY DOWN", "0", "FALSE"})
_TRADE_RECOMMENDATIONS = frozenset({"BUY_YES", "BUY_NO"})
_EPSILON = Decimal("1e-12")


class EvaluationRecord(BaseModel):
    """One resolved forecast joined to its settlement outcome."""

    model_config = ConfigDict(extra="ignore")

    forecast_id: str = Field(min_length=1)
    probability_yes: Decimal = Field(ge=Decimal("0.0"), le=Decimal("1.0"))
    actual_outcome: OutcomeLabel | bool | int | str
    market_probability_yes: Decimal | None = Field(default=None, ge=Decimal("0.0"), le=Decimal("1.0"))
    recommendation: str = "NO_TRADE"
    effective_cost_yes: Decimal | None = Field(default=None, gt=Decimal("0.0"))
    effective_cost_no: Decimal | None = Field(default=None, gt=Decimal("0.0"))
    ask_yes: Decimal | None = Field(default=None, gt=Decimal("0.0"), lt=Decimal("1.0"))
    ask_no: Decimal | None = Field(default=None, gt=Decimal("0.0"), lt=Decimal("1.0"))
    forecasted_at: datetime | None = None
    resolved_at: datetime | None = None
    category: str | None = None
    asset: str | None = None
    market_id: str | None = None
    model_version: str | None = None
    calibration_version: str | None = None
    contract_spec_hash: str | None = None
    uncertainty_interval: list[Decimal] = Field(default_factory=list, max_length=2)
    evaluation_eligible: bool = True

    @field_validator("actual_outcome", mode="before")
    @classmethod
    def normalize_outcome(cls, value: object) -> str:
        if isinstance(value, bool):
            return "YES" if value else "NO"
        if isinstance(value, int) and value in (0, 1):
            return "YES" if value == 1 else "NO"
        if isinstance(value, str):
            normalized = value.strip().upper()
            if normalized in _AFFIRMATIVE_OUTCOMES or normalized in _NEGATIVE_OUTCOMES:
                return normalized
            if normalized in {"TRUE", "1"}:
                return "YES"
            if normalized in {"FALSE", "0"}:
                return "NO"
        raise ValueError(f"Unrecognized settlement outcome: {value!r}")

    @property
    def outcome_yes(self) -> int:
        return 1 if str(self.actual_outcome).strip().upper() in _AFFIRMATIVE_OUTCOMES else 0

    @model_validator(mode="after")
    def validate_point_in_time(self) -> EvaluationRecord:
        if (
            self.forecasted_at is not None
            and self.resolved_at is not None
            and self.resolved_at <= self.forecasted_at
        ):
            raise ValueError(
                "resolved_at must be later than forecasted_at; a settlement cannot "
                "predate its forecast."
            )
        return self


class CalibrationBin(BaseModel):
    """One equal-width reliability-diagram bin."""

    model_config = ConfigDict(extra="forbid")

    lower: Decimal
    upper: Decimal
    count: int
    mean_predicted: Decimal | None = None
    observed_frequency: Decimal | None = None
    absolute_gap: Decimal | None = None


class DecisionScorecard(BaseModel):
    """Realized decision quality after execution costs, per share."""

    model_config = ConfigDict(extra="forbid")

    trade_count: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: Decimal | None = None
    direction_accuracy: Decimal | None = None
    average_edge: Decimal | None = None
    average_realized_pnl_per_share: Decimal | None = None
    total_realized_pnl_per_share: Decimal = Decimal("0.0")
    no_trade_count: int = 0
    no_trade_rate: Decimal | None = None


class Scorecard(BaseModel):
    """Aggregate evaluation output for one forecast population."""

    model_config = ConfigDict(extra="forbid")

    sample_count: int
    resolved_count: int
    excluded_count: int
    abstention_count: int
    coverage: Decimal | None = None
    brier_score: Decimal | None = None
    log_loss: Decimal | None = None
    outcome_accuracy: Decimal | None = None
    market_sample_count: int = 0
    market_brier_score: Decimal | None = None
    brier_skill_score: Decimal | None = None
    ece: Decimal | None = None
    mce: Decimal | None = None
    sharpness: Decimal | None = None
    calibration_bins: list[CalibrationBin] = Field(default_factory=list)
    decision: DecisionScorecard = Field(default_factory=DecisionScorecard)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


def _mean(values: Sequence[Decimal]) -> Decimal | None:
    if not values:
        return None
    return sum(values, Decimal("0")) / Decimal(len(values))


def _market_brier(records: Sequence[EvaluationRecord]) -> tuple[int, Decimal | None]:
    pairs = [
        (record.market_probability_yes, record.outcome_yes)
        for record in records
        if record.market_probability_yes is not None
    ]
    if not pairs:
        return 0, None
    total = sum(
        (probability - Decimal(outcome)) ** 2 for probability, outcome in pairs
    )
    return len(pairs), total / Decimal(len(pairs))


def _calibration_bins(
    records: Sequence[EvaluationRecord], n_bins: int
) -> tuple[list[CalibrationBin], Decimal | None, Decimal | None, Decimal | None]:
    if n_bins < 1:
        raise ValueError("n_bins must be at least 1")
    if not records:
        return [], None, None, None

    width = Decimal("1.0") / Decimal(n_bins)
    buckets: list[list[EvaluationRecord]] = [[] for _ in range(n_bins)]
    for record in records:
        # The last bin is closed on the right so p=1.0 is counted.
        index = min(int(record.probability_yes / width), n_bins - 1)
        buckets[index].append(record)

    bins: list[CalibrationBin] = []
    total = Decimal(len(records))
    ece = Decimal("0.0")
    mce = Decimal("0.0")
    sharpness_terms: list[Decimal] = []

    for index, bucket in enumerate(buckets):
        lower = width * Decimal(index)
        upper = width * Decimal(index + 1)
        if not bucket:
            bins.append(CalibrationBin(lower=lower, upper=upper, count=0))
            continue
        mean_predicted = _mean([record.probability_yes for record in bucket])
        observed = Decimal(sum(record.outcome_yes for record in bucket)) / Decimal(
            len(bucket)
        )
        gap = abs(mean_predicted - observed) if mean_predicted is not None else None
        if gap is not None:
            weight = Decimal(len(bucket)) / total
            ece += weight * gap
            mce = max(mce, gap)
        for record in bucket:
            sharpness_terms.append(record.probability_yes * (Decimal("1.0") - record.probability_yes))
        bins.append(
            CalibrationBin(
                lower=lower,
                upper=upper,
                count=len(bucket),
                mean_predicted=mean_predicted,
                observed_frequency=observed,
                absolute_gap=gap,
            )
        )

    sharpness = _mean(sharpness_terms)
    return bins, ece, mce, sharpness


def _decision_scorecard(records: Sequence[EvaluationRecord]) -> DecisionScorecard:
    traded = [
        record
        for record in records
        if record.recommendation.upper() in _TRADE_RECOMMENDATIONS
    ]
    no_trade = [record for record in records if record.recommendation.upper() == "NO_TRADE"]

    pnls: list[Decimal] = []
    edges: list[Decimal] = []
    wins = 0
    losses = 0
    direction_wins = 0

    for record in traded:
        if record.recommendation.upper() == "BUY_YES":
            side_probability = record.probability_yes
            cost = record.effective_cost_yes or record.ask_yes
            payoff = Decimal(record.outcome_yes)
        else:
            side_probability = Decimal("1.0") - record.probability_yes
            cost = record.effective_cost_no or record.ask_no
            payoff = Decimal(1 - record.outcome_yes)

        if cost is None:
            continue
        pnl = payoff - cost
        pnls.append(pnl)
        edges.append(side_probability - cost)
        if pnl > 0:
            wins += 1
        elif pnl < 0:
            losses += 1
        if (side_probability >= Decimal("0.5")) == (payoff == Decimal("1")):
            direction_wins += 1

    trade_count = len(pnls)
    total_pnl = sum(pnls, Decimal("0")) if pnls else Decimal("0")
    return DecisionScorecard(
        trade_count=trade_count,
        winning_trades=wins,
        losing_trades=losses,
        win_rate=(Decimal(wins) / Decimal(trade_count)) if trade_count else None,
        direction_accuracy=(
            Decimal(direction_wins) / Decimal(trade_count) if trade_count else None
        ),
        average_edge=_mean(edges),
        average_realized_pnl_per_share=(total_pnl / Decimal(trade_count)) if trade_count else None,
        total_realized_pnl_per_share=total_pnl,
        no_trade_count=len(no_trade),
        no_trade_rate=(
            Decimal(len(no_trade)) / Decimal(len(records)) if records else None
        ),
    )


def score_records(
    records: Iterable[EvaluationRecord],
    *,
    n_bins: int = 10,
) -> Scorecard:
    """Score an iterable of resolved records deterministically."""
    materialized = list(records)
    all_records = [record for record in materialized if record.evaluation_eligible]
    excluded_count = len(materialized) - len(all_records)

    # Every eligible record must carry a settled outcome.  There is no implicit
    # settlement and no synthetic label; callers must join outcomes explicitly.
    brier_terms = [
        (record.probability_yes - Decimal(record.outcome_yes)) ** 2
        for record in all_records
    ]
    brier = _mean(brier_terms)
    log_terms = [
        -(
            Decimal(record.outcome_yes) * (record.probability_yes.max(_EPSILON)).ln()
            + Decimal(1 - record.outcome_yes)
            * (Decimal("1.0") - record.probability_yes).max(_EPSILON).ln()
        )
        for record in all_records
    ]
    log_loss = _mean(log_terms)
    accuracy_terms = [
        Decimal(
            int(
                (record.probability_yes >= Decimal("0.5"))
                == (record.outcome_yes == 1)
            )
        )
        for record in all_records
    ]

    market_count, market_brier = _market_brier(all_records)
    bss: Decimal | None = None
    if brier is not None and market_brier is not None and market_brier > 0:
        bss = Decimal("1.0") - (brier / market_brier)

    bins, ece, mce, sharpness = _calibration_bins(all_records, n_bins)
    abstentions = [
        record for record in all_records if record.recommendation.upper() == "NO_TRADE"
    ]
    return Scorecard(
        sample_count=len(materialized),
        resolved_count=len(all_records),
        excluded_count=excluded_count,
        abstention_count=len(abstentions),
        coverage=(Decimal(len(all_records)) / Decimal(len(materialized))) if materialized else None,
        brier_score=brier,
        log_loss=log_loss,
        outcome_accuracy=_mean(accuracy_terms),
        market_sample_count=market_count,
        market_brier_score=market_brier,
        brier_skill_score=bss,
        ece=ece,
        mce=mce,
        sharpness=sharpness,
        calibration_bins=bins,
        decision=_decision_scorecard(all_records),
    )


def load_jsonl(path: str | Path) -> list[EvaluationRecord]:
    """Load and validate evaluation records from a JSONL file."""
    records: list[EvaluationRecord] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError as error:
                raise ValueError(f"{path}:{line_number}: invalid JSON: {error}") from error
            if not isinstance(payload, dict):
                raise ValueError(f"{path}:{line_number}: expected a JSON object")
            try:
                records.append(EvaluationRecord.model_validate(payload))
            except Exception as error:
                raise ValueError(f"{path}:{line_number}: invalid record: {error}") from error
    return records


def score_jsonl(path: str | Path, *, n_bins: int = 10) -> dict[str, Any]:
    """Convenience wrapper used by the CLI."""
    records = load_jsonl(path)
    return score_records(records, n_bins=n_bins).model_dump(mode="json")
