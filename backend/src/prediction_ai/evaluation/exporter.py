"""Export persisted forecast/feedback pairs into resolved evaluation records.

The exporter is deliberately pure: it receives rows already loaded from the
repository and turns them into the same ``EvaluationRecord`` schema used by the
offline scorecard.  It does not fetch outcomes, infer missing labels, or join
data that was not present in the forecast snapshot.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from prediction_ai.evaluation.scorecard import EvaluationRecord


class ExportError(ValueError):
    """One forecast/feedback pair cannot be exported for a specific reason."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class ExportSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exported: int = 0
    skipped: int = 0
    skipped_reasons: dict[str, int] = Field(default_factory=dict)
    next_cursor: dict[str, str] | None = None


def _as_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
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


def _probability(value: Any) -> Decimal | None:
    parsed = _as_decimal(value)
    if parsed is None or parsed < 0 or parsed > 1:
        return None
    return parsed


def _executable_price(value: Any) -> Decimal | None:
    parsed = _as_decimal(value)
    if parsed is None or parsed <= 0 or parsed >= 1:
        return None
    return parsed


def _positive_cost(value: Any) -> Decimal | None:
    parsed = _as_decimal(value)
    if parsed is None or parsed <= 0:
        return None
    return parsed


def _eligible(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no"}
    return bool(value)


def _latest(left: datetime | None, right: datetime | None) -> datetime | None:
    if left is None:
        return right
    if right is None:
        return left
    return max(left, right)


def _snapshot(forecast: dict[str, Any], key: str) -> dict[str, Any]:
    value = forecast.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ExportError(f"malformed_{key}")
    return value


def forecast_feedback_to_record(pair: dict[str, Any]) -> EvaluationRecord:
    """Convert one repository pair into a scored evaluation record.

    ``resolved_at`` is the later of the contract settlement timestamp and the
    feedback recording timestamp.  Using the later timestamp is conservative:
    a walk-forward fit will not train on a label before the system could
    actually have observed it.
    """
    forecast = pair.get("forecast")
    feedback = pair.get("feedback")
    if not isinstance(forecast, dict):
        raise ExportError("missing_forecast")
    if not isinstance(feedback, dict):
        raise ExportError("missing_feedback")

    forecast_id = str(forecast.get("forecast_id") or forecast.get("id") or "").strip()
    if not forecast_id:
        raise ExportError("missing_forecast_id")

    probability_yes = _probability(forecast.get("probability_yes"))
    if probability_yes is None:
        raise ExportError("invalid_probability_yes")

    actual_outcome = feedback.get("actual_outcome")
    if actual_outcome is None or str(actual_outcome).strip() == "":
        raise ExportError("missing_actual_outcome")

    market = _snapshot(forecast, "market")
    decision_metrics = _snapshot(forecast, "decision_metrics")
    input_snapshot = _snapshot(forecast, "input_snapshot")
    model_components = _snapshot(forecast, "model_components")
    timing = _snapshot(forecast, "timing_provenance")
    contract_spec = _snapshot(forecast, "contract_spec")
    target_definition = _snapshot(forecast, "target_definition")

    market_probability = _probability(input_snapshot.get("market_implied"))
    if market_probability is None:
        market_probability = _probability(model_components.get("market_implied_probability"))
    if input_snapshot.get("market_anchor_available") is False:
        market_probability = None

    settlement_at = _as_datetime(
        timing.get("exact_resolves_at")
        or target_definition.get("settlement_timestamp")
        or market.get("resolves_at")
    )
    feedback_at = _as_datetime(feedback.get("created_at"))
    resolved_at = _latest(settlement_at, feedback_at)

    uncertainty_interval: list[Decimal] = []
    raw_interval = forecast.get("uncertainty_interval") or []
    if isinstance(raw_interval, list) and len(raw_interval) == 2:
        parsed_interval = [_as_decimal(item) for item in raw_interval]
        if all(item is not None for item in parsed_interval):
            uncertainty_interval = [item for item in parsed_interval if item is not None]

    contract_spec_hash = (
        contract_spec.get("spec_hash")
        or feedback.get("contract_spec_hash")
        or input_snapshot.get("contract_spec_hash")
    )

    return EvaluationRecord(
        forecast_id=forecast_id,
        probability_yes=probability_yes,
        actual_outcome=actual_outcome,
        market_probability_yes=market_probability,
        recommendation=str(forecast.get("recommendation") or "NO_TRADE"),
        effective_cost_yes=_positive_cost(decision_metrics.get("effective_cost_yes")),
        effective_cost_no=_positive_cost(decision_metrics.get("effective_cost_no")),
        ask_yes=_executable_price(decision_metrics.get("executable_ask_yes")),
        ask_no=_executable_price(decision_metrics.get("executable_ask_no")),
        forecasted_at=_as_datetime(forecast.get("forecasted_at")),
        resolved_at=resolved_at,
        category=feedback.get("category") or market.get("category"),
        asset=market.get("underlying_symbol"),
        market_id=str(forecast.get("market_id") or "") or None,
        model_version=forecast.get("model_version"),
        calibration_version=forecast.get("calibration_version"),
        contract_spec_hash=contract_spec_hash,
        uncertainty_interval=uncertainty_interval,
        evaluation_eligible=_eligible(feedback.get("evaluation_eligible", True)),
    )


def export_pairs(
    pairs: list[dict[str, Any]],
    *,
    page_limit: int = 100,
) -> tuple[list[EvaluationRecord], ExportSummary]:
    """Convert repository pairs, retaining skip reasons for auditability."""
    records: list[EvaluationRecord] = []
    reasons: dict[str, int] = {}

    for pair in pairs:
        try:
            records.append(forecast_feedback_to_record(pair))
        except (ExportError, ValueError) as error:
            reason = error.reason if isinstance(error, ExportError) else "validation_error"
            reasons[reason] = reasons.get(reason, 0) + 1

    next_cursor = None
    if len(pairs) >= page_limit and pairs:
        last_forecast = pairs[-1].get("forecast") or {}
        forecasted_at = _as_datetime(last_forecast.get("forecasted_at"))
        forecast_id = last_forecast.get("forecast_id") or last_forecast.get("id")
        if forecasted_at is not None and forecast_id:
            next_cursor = {
                "after_forecasted_at": forecasted_at.isoformat(),
                "after_forecast_id": str(forecast_id),
            }

    return records, ExportSummary(
        exported=len(records),
        skipped=sum(reasons.values()),
        skipped_reasons=reasons,
        next_cursor=next_cursor,
    )


def to_jsonl(records: list[EvaluationRecord]) -> str:
    """Serialize records in the exact JSONL format consumed by the scorecard CLI."""
    lines = [record.model_dump_json(exclude_none=True) for record in records]
    return "\n".join(lines) + ("\n" if lines else "")


def write_jsonl(records: list[EvaluationRecord], path: str | Path) -> None:
    """Write records in the exact JSONL format consumed by the scorecard CLI."""
    Path(path).write_text(to_jsonl(records), encoding="utf-8")
