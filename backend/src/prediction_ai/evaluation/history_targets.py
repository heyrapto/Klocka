"""Extract point-in-time price targets from persisted forecast/feedback pairs.

This module is pure: it never fetches a price and never infers an outcome.  It
only re-reads what the forecast snapshot already recorded, so a backfill or
closing-line report cannot accidentally use information that the system did not
have at the forecast cutoff.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, Field

from prediction_ai.evaluation.exporter import ExportError, forecast_feedback_to_record
from prediction_ai.evaluation.scorecard import EvaluationRecord
from prediction_ai.schemas.market import Platform

_AFFIRMATIVE_LABELS = frozenset({"YES", "UP", "HIGHER", "BUY UP", "1", "TRUE"})
_PLATFORMS = frozenset({"polymarket", "bayse"})


class HistoryTargetError(ValueError):
    """One persisted pair cannot be mapped to a price-history target."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class HistoryOutcome(BaseModel):
    """One outcome of the verified market, including its CLOB token id."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    outcome_id: str = Field(min_length=1, max_length=200)
    token_id: str = Field(default="", max_length=200)
    label: str = Field(default="", max_length=100)


class ForecastPriceTarget(BaseModel):
    """Everything needed to locate prices for one resolved forecast.

    ``probability_yes`` always refers to ``affirmative_outcome_id``.  The
    affirmative id comes from the contract spec recorded with the forecast, so
    an UP/DOWN market cannot silently invert its market baseline.
    """

    model_config = ConfigDict(extra="forbid")

    forecast_id: str = Field(min_length=1)
    platform: Platform
    platform_market_id: str = Field(min_length=1, max_length=200)
    category: str | None = None
    forecasted_at: datetime
    entry_observed_at: datetime | None = None
    settlement_at: datetime | None = None
    resolved_at: datetime | None = None
    record: EvaluationRecord
    outcomes: list[HistoryOutcome] = Field(min_length=1, max_length=2)
    affirmative_outcome_id: str = Field(min_length=1, max_length=200)
    affirmative_token_id: str = Field(default="", max_length=200)

    @property
    def affirmative(self) -> HistoryOutcome:
        return next(
            outcome
            for outcome in self.outcomes
            if outcome.outcome_id == self.affirmative_outcome_id
        )


def _snapshot(forecast: dict[str, Any], key: str) -> dict[str, Any]:
    value = forecast.get(key)
    return value if isinstance(value, dict) else {}


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


def _outcomes(market: dict[str, Any]) -> list[HistoryOutcome]:
    raw_outcomes = market.get("outcomes")
    if not isinstance(raw_outcomes, list) or not raw_outcomes:
        raise HistoryTargetError("missing_market_outcomes")

    outcomes: list[HistoryOutcome] = []
    for raw in raw_outcomes:
        if not isinstance(raw, dict):
            continue
        outcome_id = str(raw.get("outcome_id") or "").strip()
        if not outcome_id:
            continue
        outcomes.append(
            HistoryOutcome(
                outcome_id=outcome_id,
                token_id=str(raw.get("token_id") or "").strip(),
                label=str(raw.get("label") or "").strip(),
            )
        )
    if not outcomes:
        raise HistoryTargetError("missing_market_outcomes")
    return outcomes


def _affirmative_outcome(
    outcomes: list[HistoryOutcome],
    contract_spec: dict[str, Any],
    target_definition: dict[str, Any],
) -> HistoryOutcome:
    recorded_id = str(
        contract_spec.get("affirmative_outcome_id")
        or target_definition.get("affirmative_outcome_id")
        or ""
    ).strip()
    if recorded_id:
        match = next(
            (outcome for outcome in outcomes if outcome.outcome_id == recorded_id),
            None,
        )
        if match is not None:
            return match

    labelled = next(
        (
            outcome
            for outcome in outcomes
            if outcome.label.strip().upper() in _AFFIRMATIVE_LABELS
        ),
        None,
    )
    if labelled is not None:
        return labelled
    raise HistoryTargetError("affirmative_outcome_unknown")


def pair_price_target(pair: dict[str, Any]) -> ForecastPriceTarget:
    """Map one repository pair to a validated price-history target."""
    forecast = pair.get("forecast")
    if not isinstance(forecast, dict):
        raise HistoryTargetError("missing_forecast")

    try:
        record = forecast_feedback_to_record(pair)
    except ExportError as exc:
        raise HistoryTargetError(exc.reason) from exc
    except ValueError as exc:
        raise HistoryTargetError("invalid_evaluation_record") from exc

    market = forecast.get("market")
    if not isinstance(market, dict):
        raise HistoryTargetError("missing_market_snapshot")

    platform_raw = str(market.get("platform") or "").strip().lower()
    if platform_raw not in _PLATFORMS:
        raise HistoryTargetError("unsupported_platform")
    platform = cast(Platform, platform_raw)

    platform_market_id = str(market.get("platform_market_id") or "").strip()
    if not platform_market_id:
        raise HistoryTargetError("missing_platform_market_id")

    if record.forecasted_at is None:
        raise HistoryTargetError("missing_forecasted_at")

    outcomes = _outcomes(market)
    affirmative = _affirmative_outcome(
        outcomes,
        _snapshot(forecast, "contract_spec"),
        _snapshot(forecast, "target_definition"),
    )
    timing = _snapshot(forecast, "timing_provenance")
    target_definition = _snapshot(forecast, "target_definition")
    microstructure = _snapshot(forecast, "feature_snapshot").get("market_microstructure")
    entry_observed_at = (
        _as_datetime(microstructure.get("observed_at"))
        if isinstance(microstructure, dict)
        else None
    )
    settlement_at = (
        _as_datetime(timing.get("exact_resolves_at"))
        or _as_datetime(target_definition.get("settlement_timestamp"))
        or _as_datetime(market.get("resolves_at"))
        or _as_datetime(market.get("closes_at"))
    )
    resolved_at = (
        record.resolved_at
        or settlement_at
    )

    return ForecastPriceTarget(
        forecast_id=record.forecast_id,
        platform=platform,
        platform_market_id=platform_market_id,
        category=record.category or str(market.get("category") or "") or None,
        forecasted_at=record.forecasted_at,
        entry_observed_at=entry_observed_at or record.forecasted_at,
        settlement_at=settlement_at,
        resolved_at=resolved_at,
        record=record,
        outcomes=outcomes,
        affirmative_outcome_id=affirmative.outcome_id,
        affirmative_token_id=affirmative.token_id,
    )


__all__ = [
    "ForecastPriceTarget",
    "HistoryOutcome",
    "HistoryTargetError",
    "pair_price_target",
]
