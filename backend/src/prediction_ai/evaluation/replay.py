"""Deterministic point-in-time replay of stored forecast decisions.

The replay answers two questions without fetching a price, calling an LLM, or
reading anything observed after the forecast timestamp:

1. **Decision policy replay** — given the exact execution snapshot and the
   stored probability, what would the decision engine have recommended under a
   different edge threshold, spread cap, quote-age cap, depth floor, or
   resolution-risk policy?
2. **Component attribution** — which stored probability component (math, LLM,
   market) had skill, and how would simple fixed blends have scored?

Everything is reconstructed from the immutable forecast document.  The
settlement outcome is used only for scoring.  This is an *in-sample diagnostic*
on already-resolved forecasts, not a fitted model, and it never promotes a
policy or a blend weight.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from prediction_ai.db.sqlite import SQLiteStore
from prediction_ai.decision.engine import evaluate_decision
from prediction_ai.evaluation.exporter import ExportError, forecast_feedback_to_record
from prediction_ai.evaluation.scorecard import EvaluationRecord, score_records
from prediction_ai.schemas.decision import DecisionEvaluation

MAX_PAGE_SIZE = 100
DEFAULT_MAX_PAGES = 200
MAX_THRESHOLD_VARIANTS = 20
DEFAULT_THRESHOLDS: tuple[Decimal, ...] = (
    Decimal("0.02"),
    Decimal("0.03"),
    Decimal("0.04"),
    Decimal("0.05"),
    Decimal("0.06"),
    Decimal("0.08"),
    Decimal("0.10"),
)

_EPSILON = Decimal("1e-6")
_TRADE_RECOMMENDATIONS = frozenset({"BUY_YES", "BUY_NO"})


class ReplayError(ValueError):
    """One persisted pair cannot be replayed for a specific reason."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _as_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _first_decimal(*values: Any) -> Decimal | None:
    for value in values:
        parsed = _as_decimal(value)
        if parsed is not None:
            return parsed
    return None


def _first_int(*values: Any) -> int | None:
    for value in values:
        if value is None or value == "":
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no", "off"}
    return bool(value)


def _optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    return _as_bool(value, False)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list_str(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _mean(values: Sequence[Decimal]) -> Decimal | None:
    if not values:
        return None
    return sum(values, Decimal("0")) / Decimal(len(values))


class ReplaySample(BaseModel):
    """One resolved forecast reconstructed from its immutable snapshot."""

    model_config = ConfigDict(extra="forbid")

    base_record: EvaluationRecord
    forecast_id: str
    category: str | None = None
    market_id: str | None = None
    forecasted_at: datetime
    resolved_at: datetime
    outcome_yes: int = Field(ge=0, le=1)
    probability_yes: Decimal = Field(ge=Decimal("0.0"), le=Decimal("1.0"))
    stored_recommendation: str

    stored_required_edge: Decimal | None = Field(default=None, ge=Decimal("0.0"))
    stored_uncertainty_margin: Decimal | None = Field(default=None, ge=Decimal("0.0"))
    stored_min_edge: Decimal | None = Field(default=None, ge=Decimal("0.0"))
    stored_max_spread: Decimal | None = Field(default=None, ge=Decimal("0.0"))
    stored_max_quote_age_seconds: Decimal | None = Field(default=None, gt=Decimal("0.0"))
    stored_min_depth_usd: Decimal | None = Field(default=None, gt=Decimal("0.0"))
    stored_target_order_usd: Decimal | None = Field(default=None, gt=Decimal("0.0"))
    stored_block_on_resolution_risk: bool | None = None
    stored_kelly_fraction: Decimal | None = Field(default=None, gt=Decimal("0.0"))

    ask_yes: Decimal | None = None
    ask_no: Decimal | None = None
    effective_cost_yes: Decimal | None = None
    effective_cost_no: Decimal | None = None
    spread: Decimal = Decimal("0.0")
    slippage_bps: int = 15
    taker_fee_rate_yes: Decimal = Decimal("0.01")
    taker_fee_rate_no: Decimal = Decimal("0.01")
    fee_model: str = "variance"
    quote_age_seconds: Decimal | None = None
    depth_ask_usd_yes: Decimal | None = None
    depth_ask_usd_no: Decimal | None = None
    depth_known: bool = False
    fill_complete_yes: bool = True
    fill_complete_no: bool = True
    resolution_risk_flags: list[str] = Field(default_factory=list)
    blocking_resolution_flags: list[str] = Field(default_factory=list)

    stored_gating_flags: list[str] = Field(default_factory=list)
    stored_decision_reason: str | None = None

    sigma_distance: Decimal | None = None
    signal_confidence: str | None = None
    math_probability: Decimal | None = None
    llm_probability: Decimal | None = None
    market_probability: Decimal | None = None
    model_version: str | None = None
    calibration_version: str | None = None
    decision_policy_version: str | None = None

    @property
    def eligible(self) -> bool:
        return self.base_record.evaluation_eligible


class ReplayDefaults(BaseModel):
    """Fallbacks used only when the stored snapshot did not record a value."""

    model_config = ConfigDict(extra="forbid")

    required_edge: Decimal = Field(default=Decimal("0.06"), ge=Decimal("0.0"), lt=Decimal("1.0"))
    max_spread: Decimal = Field(default=Decimal("0.05"), ge=Decimal("0.0"), lt=Decimal("1.0"))
    max_quote_age_seconds: Decimal = Field(default=Decimal("60.0"), gt=Decimal("0.0"))
    min_depth_usd: Decimal = Field(default=Decimal("100.0"), gt=Decimal("0.0"))
    target_order_usd: Decimal = Field(default=Decimal("100.0"), gt=Decimal("0.0"))
    block_on_resolution_risk: bool = False
    kelly_fraction: Decimal = Field(default=Decimal("0.25"), gt=Decimal("0.0"), le=Decimal("1.0"))


class ReplayPolicy(BaseModel):
    """Optional overrides for one replay variant.

    ``None`` means "use the value stored with the forecast, then the defaults".
    The baseline uses all ``None`` so it reconstructs the original policy as
    closely as the snapshot permits.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = "baseline"
    required_edge: Decimal | None = Field(default=None, ge=Decimal("0.0"), lt=Decimal("1.0"))
    max_spread: Decimal | None = Field(default=None, ge=Decimal("0.0"), lt=Decimal("1.0"))
    max_quote_age_seconds: Decimal | None = Field(default=None, gt=Decimal("0.0"))
    min_depth_usd: Decimal | None = Field(default=None, gt=Decimal("0.0"))
    target_order_usd: Decimal | None = Field(default=None, gt=Decimal("0.0"))
    block_on_resolution_risk: bool | None = None


class ReplayMismatch(BaseModel):
    """One baseline replay that did not reproduce the stored recommendation."""

    model_config = ConfigDict(extra="forbid")

    forecast_id: str
    stored_recommendation: str
    replayed_recommendation: str
    stored_required_edge: Decimal | None = None
    stored_gating_flags: list[str] = Field(default_factory=list)
    gating_flags: list[str] = Field(default_factory=list)
    decision_reason: str | None = None


class ReplayVariantResult(BaseModel):
    """Aggregate decision quality for one policy variant."""

    model_config = ConfigDict(extra="forbid")

    name: str
    threshold_source: str
    required_edge: Decimal | None = None
    max_spread: Decimal | None = None
    max_quote_age_seconds: Decimal | None = None
    min_depth_usd: Decimal | None = None
    block_on_resolution_risk: bool | None = None
    samples: int = 0
    scored_samples: int = 0
    trade_recommendations: int = 0
    trade_coverage: Decimal | None = None
    no_trade_count: int = 0
    no_trade_rate: Decimal | None = None
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: Decimal | None = None
    direction_accuracy: Decimal | None = None
    average_edge: Decimal | None = None
    average_realized_pnl_per_share: Decimal | None = None
    total_realized_pnl_per_share: Decimal = Decimal("0.0")
    gate_counts: dict[str, int] = Field(default_factory=dict)
    recommendation_counts: dict[str, int] = Field(default_factory=dict)


class BlendVariantResult(BaseModel):
    """Probability-only score for one fixed component blend."""

    model_config = ConfigDict(extra="forbid")

    name: str
    weights: dict[str, Decimal] = Field(default_factory=dict)
    samples: int = 0
    paired_samples: int = 0
    coverage: Decimal | None = None
    brier: Decimal | None = None
    log_loss: Decimal | None = None
    outcome_accuracy: Decimal | None = None
    market_brier: Decimal | None = None
    bss_vs_market: Decimal | None = None


class BaselineSample(BaseModel):
    """Compact per-forecast view of the baseline replay."""

    model_config = ConfigDict(extra="forbid")

    forecast_id: str
    stored_recommendation: str
    replayed_recommendation: str
    outcome_yes: int
    probability_yes: Decimal
    required_edge: Decimal | None = None
    stored_gating_flags: list[str] = Field(default_factory=list)
    gating_flags: list[str] = Field(default_factory=list)
    realized_pnl_per_share: Decimal | None = None


class ReplayReport(BaseModel):
    """Bounded replay report over resolved forecast/feedback pairs."""

    model_config = ConfigDict(extra="forbid")

    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    category: str | None = None
    pairs_scanned: int = 0
    samples: int = 0
    skipped: int = 0
    skipped_reasons: dict[str, int] = Field(default_factory=dict)
    pages: int = 0
    truncated: bool = False
    threshold_fallbacks: int = 0
    reconstruction_matches: int = 0
    reconstruction_mismatches: int = 0
    baseline: ReplayVariantResult | None = None
    threshold_sweep: list[ReplayVariantResult] = Field(default_factory=list)
    blend_sweep: list[BlendVariantResult] = Field(default_factory=list)
    mismatches: list[ReplayMismatch] = Field(default_factory=list)
    baseline_samples: list[BaselineSample] = Field(default_factory=list)
    component_coverage: Decimal | None = None
    full_component_coverage: Decimal | None = None
    notes: list[str] = Field(default_factory=list)


def _snapshot(forecast: dict[str, Any], key: str) -> dict[str, Any]:
    return _dict(forecast.get(key))


def _infer_block_on_resolution_risk(
    recorded: bool | None,
    recommendation: str,
    reason: str | None,
) -> bool | None:
    if recorded is not None:
        return recorded
    if (
        recommendation.upper() == "NO_TRADE"
        and reason is not None
        and reason.startswith("Contract settlement semantics are unresolved")
    ):
        return True
    return None


def pair_to_replay_sample(pair: dict[str, Any]) -> ReplaySample:
    """Map one repository pair to a validated replay sample."""
    forecast = pair.get("forecast")
    if not isinstance(forecast, dict):
        raise ReplayError("missing_forecast")
    feedback = pair.get("feedback")
    if not isinstance(feedback, dict):
        raise ReplayError("missing_feedback")

    try:
        record = forecast_feedback_to_record(pair)
    except ExportError as exc:
        raise ReplayError(exc.reason) from exc
    except ValueError as exc:
        raise ReplayError("invalid_evaluation_record") from exc

    if record.forecasted_at is None:
        raise ReplayError("missing_forecasted_at")
    if record.resolved_at is None:
        raise ReplayError("missing_resolved_at")

    decision_metrics = _snapshot(forecast, "decision_metrics")
    if not decision_metrics:
        raise ReplayError("missing_decision_metrics")

    input_snapshot = _snapshot(forecast, "input_snapshot")
    execution = _snapshot(input_snapshot, "execution")
    feature_snapshot = _snapshot(forecast, "feature_snapshot")
    gate_inputs = _snapshot(feature_snapshot, "decision_gate_inputs")
    crypto_research = _snapshot(feature_snapshot, "crypto_research")
    decision_policy = _snapshot(feature_snapshot, "decision_policy")
    model_components = _snapshot(forecast, "model_components")
    explanation = _snapshot(forecast, "explanation")

    ask_yes = _first_decimal(
        decision_metrics.get("executable_ask_yes"), execution.get("ask_yes")
    )
    ask_no = _first_decimal(
        decision_metrics.get("executable_ask_no"), execution.get("ask_no")
    )

    spread = _first_decimal(decision_metrics.get("spread"), execution.get("spread"))
    if spread is None:
        spread = (
            ask_yes + ask_no - Decimal("1.0")
            if ask_yes is not None and ask_no is not None
            else Decimal("0.0")
        )

    stored_required_edge = _as_decimal(decision_metrics.get("minimum_required_edge"))
    stored_uncertainty_margin = _first_decimal(
        decision_policy.get("uncertainty_margin"),
        gate_inputs.get("effective_uncertainty_margin"),
    )
    stored_min_edge = _as_decimal(decision_policy.get("min_edge"))
    if (
        stored_min_edge is None
        and stored_required_edge is not None
        and stored_uncertainty_margin is not None
    ):
        stored_min_edge = stored_required_edge - stored_uncertainty_margin

    stored_recommendation = record.recommendation
    stored_decision_reason = str(explanation.get("decision_reason") or "") or None

    market_probability = _first_decimal(
        model_components.get("market_implied_probability"),
        input_snapshot.get("market_implied"),
    )
    if input_snapshot.get("market_anchor_available") is False:
        market_probability = None

    return ReplaySample(
        base_record=record,
        forecast_id=record.forecast_id,
        category=record.category,
        market_id=record.market_id,
        forecasted_at=record.forecasted_at,
        resolved_at=record.resolved_at,
        outcome_yes=record.outcome_yes,
        probability_yes=record.probability_yes,
        stored_recommendation=stored_recommendation,
        stored_required_edge=stored_required_edge,
        stored_uncertainty_margin=stored_uncertainty_margin,
        stored_min_edge=stored_min_edge,
        stored_max_spread=_as_decimal(decision_policy.get("max_spread")),
        stored_max_quote_age_seconds=_first_decimal(
            decision_policy.get("max_quote_age_seconds"),
            decision_metrics.get("max_quote_age_seconds"),
        ),
        stored_min_depth_usd=_first_decimal(
            decision_policy.get("min_depth_usd"),
            decision_metrics.get("minimum_depth_usd"),
        ),
        stored_target_order_usd=_first_decimal(
            decision_policy.get("target_order_usd"),
            decision_metrics.get("target_order_usd"),
        ),
        stored_block_on_resolution_risk=_infer_block_on_resolution_risk(
            _optional_bool(decision_policy.get("block_on_resolution_risk")),
            stored_recommendation,
            stored_decision_reason,
        ),
        stored_kelly_fraction=_as_decimal(decision_policy.get("kelly_fraction")),
        ask_yes=ask_yes,
        ask_no=ask_no,
        effective_cost_yes=_as_decimal(decision_metrics.get("effective_cost_yes")),
        effective_cost_no=_as_decimal(decision_metrics.get("effective_cost_no")),
        spread=spread,
        slippage_bps=_first_int(
            decision_metrics.get("slippage_bps"),
            execution.get("slippage_bps"),
            15,
        )
        or 15,
        taker_fee_rate_yes=_first_decimal(
            decision_metrics.get("taker_fee_rate_yes"),
            execution.get("taker_fee_rate"),
            Decimal("0.01"),
        )
        or Decimal("0.01"),
        taker_fee_rate_no=_first_decimal(
            decision_metrics.get("taker_fee_rate_no"),
            execution.get("taker_fee_rate"),
            Decimal("0.01"),
        )
        or Decimal("0.01"),
        fee_model=str(
            decision_metrics.get("fee_model")
            or execution.get("fee_model")
            or "variance"
        ),
        quote_age_seconds=_first_decimal(
            decision_metrics.get("quote_age_seconds"),
            execution.get("quote_age_seconds"),
        ),
        depth_ask_usd_yes=_first_decimal(
            decision_metrics.get("depth_ask_usd_yes"),
            execution.get("depth_ask_usd_yes"),
        ),
        depth_ask_usd_no=_first_decimal(
            decision_metrics.get("depth_ask_usd_no"),
            execution.get("depth_ask_usd_no"),
        ),
        depth_known=_as_bool(
            decision_metrics.get("depth_known", execution.get("depth_known")),
            False,
        ),
        fill_complete_yes=_as_bool(
            decision_metrics.get("fill_complete_yes", execution.get("fill_complete_yes")),
            True,
        ),
        fill_complete_no=_as_bool(
            decision_metrics.get("fill_complete_no", execution.get("fill_complete_no")),
            True,
        ),
        resolution_risk_flags=_list_str(
            decision_metrics.get("resolution_risk_flags")
            or input_snapshot.get("resolution_risk_flags")
        ),
        blocking_resolution_flags=_list_str(
            decision_metrics.get("blocking_resolution_flags")
        ),
        stored_gating_flags=_list_str(explanation.get("gating_flags")),
        stored_decision_reason=stored_decision_reason,
        sigma_distance=_first_decimal(
            gate_inputs.get("sigma_distance"),
            crypto_research.get("sigma_distance"),
        ),
        signal_confidence=str(
            gate_inputs.get("signal_confidence")
            or crypto_research.get("signal_confidence")
            or ""
        )
        or None,
        math_probability=_first_decimal(
            model_components.get("math_probability"),
            input_snapshot.get("math_probability"),
        ),
        llm_probability=_first_decimal(
            model_components.get("llm_probability"),
            input_snapshot.get("llm_probability"),
        ),
        market_probability=market_probability,
        model_version=record.model_version,
        calibration_version=record.calibration_version,
        decision_policy_version=str(
            forecast.get("decision_policy_version") or ""
        )
        or None,
    )


def _resolve(
    override: Any,
    stored: Any,
    default: Any,
) -> Any:
    if override is not None:
        return override
    if stored is not None:
        return stored
    return default


def replay_decision(
    sample: ReplaySample,
    policy: ReplayPolicy,
    defaults: ReplayDefaults,
) -> DecisionEvaluation:
    """Re-run the decision engine on the stored execution snapshot."""
    required_edge = _resolve(
        policy.required_edge, sample.stored_required_edge, defaults.required_edge
    )
    max_spread = _resolve(policy.max_spread, sample.stored_max_spread, defaults.max_spread)
    max_quote_age = _resolve(
        policy.max_quote_age_seconds,
        sample.stored_max_quote_age_seconds,
        defaults.max_quote_age_seconds,
    )
    min_depth = _resolve(
        policy.min_depth_usd, sample.stored_min_depth_usd, defaults.min_depth_usd
    )
    target_order = _resolve(
        policy.target_order_usd, sample.stored_target_order_usd, defaults.target_order_usd
    )
    block_on_resolution_risk = _resolve(
        policy.block_on_resolution_risk,
        sample.stored_block_on_resolution_risk,
        defaults.block_on_resolution_risk,
    )

    return evaluate_decision(
        probability_yes=sample.probability_yes,
        ask_yes=sample.ask_yes,
        ask_no=sample.ask_no,
        min_edge=required_edge,
        max_spread=max_spread,
        spread=sample.spread,
        is_data_fresh=True,
        uncertainty_margin=Decimal("0.0"),
        taker_fee_rate_yes=sample.taker_fee_rate_yes,
        taker_fee_rate_no=sample.taker_fee_rate_no,
        fee_model=sample.fee_model,
        slippage_bps=sample.slippage_bps,
        kelly_fraction=sample.stored_kelly_fraction or defaults.kelly_fraction,
        quote_age_seconds=sample.quote_age_seconds,
        max_quote_age_seconds=max_quote_age,
        depth_ask_usd_yes=sample.depth_ask_usd_yes,
        depth_ask_usd_no=sample.depth_ask_usd_no,
        min_depth_usd=min_depth,
        target_order_usd=target_order,
        fill_complete_yes=sample.fill_complete_yes,
        fill_complete_no=sample.fill_complete_no,
        depth_known=sample.depth_known,
        resolution_risk_flags=sample.resolution_risk_flags,
        blocking_resolution_flags=sample.blocking_resolution_flags,
        block_on_resolution_risk=block_on_resolution_risk,
    )


def _record_with_decision(
    sample: ReplaySample,
    decision: DecisionEvaluation,
) -> EvaluationRecord:
    return sample.base_record.model_copy(
        update={
            "recommendation": decision.recommendation,
            "effective_cost_yes": decision.metrics.effective_cost_yes,
            "effective_cost_no": decision.metrics.effective_cost_no,
            "ask_yes": decision.metrics.executable_ask_yes,
            "ask_no": decision.metrics.executable_ask_no,
        }
    )


def _realized_pnl(sample: ReplaySample, decision: DecisionEvaluation) -> Decimal | None:
    recommendation = decision.recommendation.upper()
    if recommendation == "BUY_YES":
        cost = decision.metrics.effective_cost_yes
        payoff = Decimal(sample.outcome_yes)
    elif recommendation == "BUY_NO":
        cost = decision.metrics.effective_cost_no
        payoff = Decimal(1 - sample.outcome_yes)
    else:
        return None
    if cost is None:
        return None
    return payoff - cost


def _run_variant(
    samples: Sequence[ReplaySample],
    policy: ReplayPolicy,
    defaults: ReplayDefaults,
) -> tuple[ReplayVariantResult, list[DecisionEvaluation]]:
    decisions = [replay_decision(sample, policy, defaults) for sample in samples]
    records = [
        _record_with_decision(sample, decision)
        for sample, decision in zip(samples, decisions, strict=True)
    ]
    scored = score_records(records).decision
    gate_counts: Counter[str] = Counter(
        flag for decision in decisions for flag in decision.gating_flags
    )
    recommendation_counts: Counter[str] = Counter(
        decision.recommendation for decision in decisions
    )
    trade_recommendations = sum(
        count
        for recommendation, count in recommendation_counts.items()
        if recommendation.upper() in _TRADE_RECOMMENDATIONS
    )
    scored_samples = sum(1 for sample in samples if sample.eligible)

    max_spread = _resolve(policy.max_spread, None, defaults.max_spread)
    max_quote_age = _resolve(
        policy.max_quote_age_seconds, None, defaults.max_quote_age_seconds
    )
    min_depth = _resolve(policy.min_depth_usd, None, defaults.min_depth_usd)
    block = _resolve(
        policy.block_on_resolution_risk,
        None,
        defaults.block_on_resolution_risk,
    )

    return (
        ReplayVariantResult(
            name=policy.name,
            threshold_source=(
                "stored_per_sample" if policy.required_edge is None else "fixed"
            ),
            required_edge=policy.required_edge,
            max_spread=policy.max_spread if policy.max_spread is not None else max_spread,
            max_quote_age_seconds=(
                policy.max_quote_age_seconds
                if policy.max_quote_age_seconds is not None
                else max_quote_age
            ),
            min_depth_usd=policy.min_depth_usd if policy.min_depth_usd is not None else min_depth,
            block_on_resolution_risk=block,
            samples=len(samples),
            scored_samples=scored_samples,
            trade_recommendations=trade_recommendations,
            trade_coverage=(
                Decimal(trade_recommendations) / Decimal(scored_samples)
                if scored_samples
                else None
            ),
            no_trade_count=scored.no_trade_count,
            no_trade_rate=scored.no_trade_rate,
            winning_trades=scored.winning_trades,
            losing_trades=scored.losing_trades,
            win_rate=scored.win_rate,
            direction_accuracy=scored.direction_accuracy,
            average_edge=scored.average_edge,
            average_realized_pnl_per_share=scored.average_realized_pnl_per_share,
            total_realized_pnl_per_share=scored.total_realized_pnl_per_share,
            gate_counts=dict(sorted(gate_counts.items())),
            recommendation_counts=dict(sorted(recommendation_counts.items())),
        ),
        decisions,
    )


def _weighted_blend(
    weights: dict[str, Decimal],
    components: dict[str, Decimal | None],
) -> Decimal | None:
    total = sum(weights.values(), Decimal("0"))
    if abs(total - Decimal("1.0")) > Decimal("1e-9"):
        raise ValueError(f"Blend weights must sum to 1.0, got {total}")
    value = Decimal("0")
    for name, weight in weights.items():
        component = components.get(name)
        if component is None:
            return None
        value += weight * component
    return min(max(value, _EPSILON), Decimal("1.0") - _EPSILON)


_BLEND_VARIANTS: tuple[tuple[str, dict[str, Decimal]], ...] = (
    ("math_only", {"math": Decimal("1.0")}),
    ("llm_only", {"llm": Decimal("1.0")}),
    ("market_only", {"market": Decimal("1.0")}),
    ("math_market_65_35", {"math": Decimal("0.65"), "market": Decimal("0.35")}),
    ("llm_market_35_65", {"llm": Decimal("0.35"), "market": Decimal("0.65")}),
    (
        "crypto_math_llm_market_40_10_50",
        {
            "math": Decimal("0.40"),
            "llm": Decimal("0.10"),
            "market": Decimal("0.50"),
        },
    ),
    ("equal_math_market_50_50", {"math": Decimal("0.50"), "market": Decimal("0.50")}),
)


def _probability_metrics(
    probabilities: Sequence[Decimal],
    outcomes: Sequence[int],
    market_probabilities: Sequence[Decimal | None],
) -> tuple[
    Decimal | None,
    Decimal | None,
    Decimal | None,
    Decimal | None,
    Decimal | None,
    int,
]:
    brier = _mean(
        [
            (probability - Decimal(outcome)) ** 2
            for probability, outcome in zip(probabilities, outcomes, strict=True)
        ]
    )
    log_loss = _mean(
        [
            -(
                Decimal(outcome) * probability.max(_EPSILON).ln()
                + Decimal(1 - outcome)
                * (Decimal("1.0") - probability).max(_EPSILON).ln()
            )
            for probability, outcome in zip(probabilities, outcomes, strict=True)
        ]
    )
    accuracy = _mean(
        [
            Decimal(int((probability >= Decimal("0.5")) == (outcome == 1)))
            for probability, outcome in zip(probabilities, outcomes, strict=True)
        ]
    )

    paired = [
        (probability, outcome, market)
        for probability, outcome, market in zip(
            probabilities, outcomes, market_probabilities, strict=True
        )
        if market is not None
    ]
    paired_model_brier: Decimal | None = None
    market_brier: Decimal | None = None
    bss: Decimal | None = None
    if paired:
        paired_model_brier = _mean(
            [(probability - Decimal(outcome)) ** 2 for probability, outcome, _ in paired]
        )
        market_brier = _mean(
            [(market - Decimal(outcome)) ** 2 for _, outcome, market in paired if market is not None]
        )
        if (
            paired_model_brier is not None
            and market_brier is not None
            and market_brier > 0
        ):
            bss = Decimal("1.0") - (paired_model_brier / market_brier)
    return brier, log_loss, accuracy, market_brier, bss, len(paired)


def _blend_sweep(samples: Sequence[ReplaySample]) -> list[BlendVariantResult]:
    eligible = [sample for sample in samples if sample.eligible]
    total = len(eligible)
    results: list[BlendVariantResult] = []

    stored_probabilities = [sample.probability_yes for sample in eligible]
    stored_outcomes = [sample.outcome_yes for sample in eligible]
    stored_markets = [sample.market_probability for sample in eligible]
    brier, log_loss, accuracy, market_brier, bss, paired = _probability_metrics(
        stored_probabilities, stored_outcomes, stored_markets
    )
    results.append(
        BlendVariantResult(
            name="stored_probability",
            weights={},
            samples=total,
            paired_samples=paired,
            coverage=Decimal("1") if total else None,
            brier=brier,
            log_loss=log_loss,
            outcome_accuracy=accuracy,
            market_brier=market_brier,
            bss_vs_market=bss,
        )
    )

    for name, weights in _BLEND_VARIANTS:
        probabilities: list[Decimal] = []
        outcomes: list[int] = []
        markets: list[Decimal | None] = []
        for sample in eligible:
            components = {
                "math": sample.math_probability,
                "llm": sample.llm_probability,
                "market": sample.market_probability,
            }
            blended = _weighted_blend(weights, components)
            if blended is None:
                continue
            probabilities.append(blended)
            outcomes.append(sample.outcome_yes)
            markets.append(sample.market_probability)

        brier, log_loss, accuracy, market_brier, bss, paired = _probability_metrics(
            probabilities, outcomes, markets
        )
        results.append(
            BlendVariantResult(
                name=name,
                weights=weights,
                samples=len(probabilities),
                paired_samples=paired,
                coverage=(
                    Decimal(len(probabilities)) / Decimal(total) if total else None
                ),
                brier=brier,
                log_loss=log_loss,
                outcome_accuracy=accuracy,
                market_brier=market_brier,
                bss_vs_market=bss,
            )
        )
    return results


def _cursor(pair: dict[str, Any]) -> tuple[str, str] | None:
    forecast = pair.get("forecast")
    if not isinstance(forecast, dict):
        return None
    forecasted_at = forecast.get("forecasted_at")
    forecast_id = forecast.get("forecast_id") or forecast.get("id")
    if not forecasted_at or not forecast_id:
        return None
    return str(forecasted_at), str(forecast_id)


def _skip(report: ReplayReport, reason: str) -> None:
    report.skipped += 1
    report.skipped_reasons[reason] = report.skipped_reasons.get(reason, 0) + 1


def _dedupe_thresholds(thresholds: Sequence[Decimal]) -> list[Decimal]:
    seen: set[Decimal] = set()
    unique: list[Decimal] = []
    for threshold in thresholds:
        if not Decimal("0.0") < threshold < Decimal("1.0"):
            raise ValueError("thresholds must lie strictly between 0 and 1")
        if threshold in seen:
            continue
        seen.add(threshold)
        unique.append(threshold)
    if not unique:
        raise ValueError("at least one threshold is required")
    if len(unique) > MAX_THRESHOLD_VARIANTS:
        raise ValueError(
            f"at most {MAX_THRESHOLD_VARIANTS} threshold variants are allowed"
        )
    return unique


async def build_replay_report(
    store: SQLiteStore,
    *,
    category: str | None = None,
    thresholds: Sequence[Decimal] = DEFAULT_THRESHOLDS,
    page_limit: int = MAX_PAGE_SIZE,
    max_pages: int = DEFAULT_MAX_PAGES,
    defaults: ReplayDefaults | None = None,
    sweep_policy: ReplayPolicy | None = None,
    include_samples: bool = False,
) -> ReplayReport:
    """Page through resolved pairs and replay each decision deterministically."""
    if not 1 <= page_limit <= MAX_PAGE_SIZE:
        raise ValueError(f"page_limit must be between 1 and {MAX_PAGE_SIZE}")
    if max_pages < 1:
        raise ValueError("max_pages must be at least 1")
    bounded_thresholds = _dedupe_thresholds(thresholds)
    resolved_defaults = defaults or ReplayDefaults()
    base_sweep_policy = sweep_policy or ReplayPolicy(name="sweep")

    report = ReplayReport(category=category)
    samples: list[ReplaySample] = []
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
                samples.append(pair_to_replay_sample(pair))
            except ReplayError as exc:
                _skip(report, exc.reason)

        if len(pairs) < page_limit:
            break
        next_cursor = _cursor(pairs[-1])
        if next_cursor is None or next_cursor == (cursor_at, cursor_id):
            report.truncated = True
            break
        cursor_at, cursor_id = next_cursor
    else:
        report.truncated = True

    report.samples = len(samples)
    report.threshold_fallbacks = sum(
        1 for sample in samples if sample.stored_required_edge is None
    )

    baseline_policy = ReplayPolicy(name="baseline")
    baseline, baseline_decisions = _run_variant(samples, baseline_policy, resolved_defaults)
    report.baseline = baseline

    for sample, decision in zip(samples, baseline_decisions, strict=True):
        if decision.recommendation == sample.stored_recommendation:
            report.reconstruction_matches += 1
            continue
        report.reconstruction_mismatches += 1
        if len(report.mismatches) < 25:
            report.mismatches.append(
                ReplayMismatch(
                    forecast_id=sample.forecast_id,
                    stored_recommendation=sample.stored_recommendation,
                    replayed_recommendation=decision.recommendation,
                    stored_required_edge=sample.stored_required_edge,
                    stored_gating_flags=sample.stored_gating_flags,
                    gating_flags=decision.gating_flags,
                    decision_reason=sample.stored_decision_reason,
                )
            )

    for threshold in bounded_thresholds:
        policy = ReplayPolicy(
            name=f"required_edge={threshold}",
            required_edge=threshold,
            max_spread=base_sweep_policy.max_spread,
            max_quote_age_seconds=base_sweep_policy.max_quote_age_seconds,
            min_depth_usd=base_sweep_policy.min_depth_usd,
            target_order_usd=base_sweep_policy.target_order_usd,
            block_on_resolution_risk=base_sweep_policy.block_on_resolution_risk,
        )
        result, _ = _run_variant(samples, policy, resolved_defaults)
        report.threshold_sweep.append(result)

    report.blend_sweep = _blend_sweep(samples)
    eligible = [sample for sample in samples if sample.eligible]
    if eligible:
        with_any = sum(
            1
            for sample in eligible
            if sample.math_probability is not None
            or sample.llm_probability is not None
            or sample.market_probability is not None
        )
        with_all = sum(
            1
            for sample in eligible
            if sample.math_probability is not None
            and sample.llm_probability is not None
            and sample.market_probability is not None
        )
        report.component_coverage = Decimal(with_any) / Decimal(len(eligible))
        report.full_component_coverage = Decimal(with_all) / Decimal(len(eligible))

    if include_samples:
        report.baseline_samples = [
            BaselineSample(
                forecast_id=sample.forecast_id,
                stored_recommendation=sample.stored_recommendation,
                replayed_recommendation=decision.recommendation,
                outcome_yes=sample.outcome_yes,
                probability_yes=sample.probability_yes,
                required_edge=sample.stored_required_edge,
                stored_gating_flags=sample.stored_gating_flags,
                gating_flags=decision.gating_flags,
                realized_pnl_per_share=_realized_pnl(sample, decision),
            )
            for sample, decision in zip(samples, baseline_decisions, strict=True)
        ]

    if report.component_coverage is None or report.component_coverage == 0:
        report.notes.append(
            "No stored model components were available; blend attribution "
            "requires forecasts written after component snapshots were persisted."
        )
    report.notes.append(
        "Blend comparison is in-sample and diagnostic; it does not fit or "
        "promote weights."
    )
    if report.samples < 30:
        report.notes.append(
            "Small sample: treat policy and blend differences as directional, "
            "not conclusive."
        )
    if report.reconstruction_mismatches:
        report.notes.append(
            "Baseline replay differs from some stored recommendations; inspect "
            "mismatches before trusting threshold sweeps."
        )
    return report


__all__ = [
    "BaselineSample",
    "BlendVariantResult",
    "DEFAULT_MAX_PAGES",
    "DEFAULT_THRESHOLDS",
    "MAX_PAGE_SIZE",
    "MAX_THRESHOLD_VARIANTS",
    "ReplayDefaults",
    "ReplayError",
    "ReplayMismatch",
    "ReplayPolicy",
    "ReplayReport",
    "ReplaySample",
    "ReplayVariantResult",
    "build_replay_report",
    "pair_to_replay_sample",
    "replay_decision",
]
