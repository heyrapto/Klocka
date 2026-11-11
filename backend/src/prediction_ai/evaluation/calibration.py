"""Walk-forward probability calibration with explicit anti-leakage controls.

Design rules:

* Calibration is fitted only on resolved, evaluation-eligible feedback.
* Every fold trains exclusively on labels that were observable strictly before
  the first forecast in its test block.  ``resolved_at`` is the conservative
  ``max(settlement_timestamp, feedback_created_at)`` from the exporter, so a
  label is never used before the system could have observed it.
* Fitting uses float64 internally; every serialized probability and parameter
  is a ``Decimal``.
* An artifact is evidence for human review, never an automatically deployed
  calibrator.  ``deployment_status`` is always ``review_required``.
* Insufficient data is reported honestly and never patched with synthetic
  samples, a 0.50 prior, or forced class balance.
"""

from __future__ import annotations

import hashlib
import json
import math
from bisect import bisect_right
from collections import Counter
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

from prediction_ai.evaluation.scorecard import EvaluationRecord, Scorecard, score_records

CalibrationMethod = Literal["platt", "isotonic"]
CalibrationStatus = Literal["evaluated", "insufficient_data"]

ARTIFACT_VERSION: Literal["calibration_artifact_v1"] = "calibration_artifact_v1"
REPORT_VERSION: Literal["calibration_report_v1"] = "calibration_report_v1"
PROBABILITY_EPSILON = Decimal("0.000001")
PROBABILITY_QUANTUM = Decimal("0.000000000001")
DEFAULT_MIN_TRAIN_SAMPLES = 30
DEFAULT_MIN_CLASS_SAMPLES = 5
DEFAULT_FOLDS = 5
DEFAULT_ECE_TARGET = Decimal("0.05")
SMALL_SAMPLE_ISOTONIC_THRESHOLD = 1000

_LIMITATIONS = [
    "Calibration artifacts are review evidence only; they are never applied automatically.",
    "Walk-forward metrics are retrospective and do not guarantee future calibration.",
    "Only resolved, evaluation-eligible feedback is used; unresolved forecasts and abstentions marked ineligible are excluded.",
    "Because NO_TRADE feedback is ineligible, calibration currently learns only from forecasts that led to a trade; this selection can bias the sample toward traded regimes.",
    "Serialized probabilities are clamped to [1e-6, 1-1e-6] to avoid certainty claims and infinite log loss.",
    "No hierarchical pooling across categories, assets, or horizons is performed.",
    "Label availability is purged using resolved_at, but slow or disputed resolutions can still shorten the effective training window.",
]


class InsufficientDataError(ValueError):
    """A calibration fit or report cannot be produced honestly."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _decimal(value: float) -> Decimal:
    rounded = round(value, 12)
    if abs(rounded) < 1e-12:
        return Decimal("0")
    return Decimal(str(rounded))


def _clamp_probability(value: Decimal, epsilon: Decimal = PROBABILITY_EPSILON) -> Decimal:
    bounded = min(max(value, epsilon), Decimal("1") - epsilon)
    return bounded.quantize(PROBABILITY_QUANTUM, rounding=ROUND_HALF_EVEN)


def _as_probability(value: Decimal, epsilon: Decimal = PROBABILITY_EPSILON) -> Decimal:
    if not value.is_finite():
        raise ValueError("probability must be finite")
    return min(max(value, epsilon), Decimal("1") - epsilon)


class PlattParameters(BaseModel):
    """Sigmoid ``1 / (1 + exp(-(slope * logit(p) + intercept)))``."""

    model_config = ConfigDict(extra="forbid")

    slope: Decimal = Field(ge=Decimal("0"), le=Decimal("50"))
    intercept: Decimal = Field(ge=Decimal("-50"), le=Decimal("50"))
    l2: Decimal = Field(default=Decimal("0.000001"), ge=Decimal("0"))
    epsilon: Decimal = Field(
        default=PROBABILITY_EPSILON,
        gt=Decimal("0"),
        lt=Decimal("0.5"),
    )


class IsotonicParameters(BaseModel):
    """Piecewise-linear monotone map over sorted raw-probability thresholds."""

    model_config = ConfigDict(extra="forbid")

    thresholds: list[Decimal] = Field(min_length=1)
    values: list[Decimal] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_monotonicity(self) -> IsotonicParameters:
        if len(self.thresholds) != len(self.values):
            raise ValueError("thresholds and values must have the same length")
        if any(
            right <= left for left, right in zip(self.thresholds, self.thresholds[1:], strict=False)
        ):
            raise ValueError("thresholds must be strictly increasing")
        if any(right < left for left, right in zip(self.values, self.values[1:], strict=False)):
            raise ValueError("isotonic values must be non-decreasing")
        if any(value < 0 or value > 1 for value in self.values):
            raise ValueError("isotonic values must lie in [0, 1]")
        return self


class CalibrationArtifact(BaseModel):
    """Serialized calibrator plus the evidence needed to audit its fit."""

    model_config = ConfigDict(extra="forbid")

    artifact_version: Literal["calibration_artifact_v1"] = ARTIFACT_VERSION
    method: CalibrationMethod
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    training_forecast_start: datetime
    training_forecast_end: datetime
    label_available_through: datetime
    sample_count: int = Field(gt=0)
    positive_count: int = Field(ge=0)
    negative_count: int = Field(ge=0)
    excluded_ineligible_count: int = Field(default=0, ge=0)
    excluded_missing_timestamps_count: int = Field(default=0, ge=0)
    training_data_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    parameters: PlattParameters | IsotonicParameters
    calibration_version: str = ""
    artifact_hash: str = ""
    deployment_status: Literal["review_required"] = "review_required"
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_parameter_type(self) -> CalibrationArtifact:
        expected = PlattParameters if self.method == "platt" else IsotonicParameters
        if not isinstance(self.parameters, expected):
            raise ValueError(f"method {self.method!r} requires {expected.__name__} parameters")
        if self.positive_count + self.negative_count != self.sample_count:
            raise ValueError("class counts must sum to sample_count")
        if self.training_forecast_start > self.training_forecast_end:
            raise ValueError("training_forecast_start must not exceed training_forecast_end")
        if self.label_available_through < self.training_forecast_start:
            raise ValueError("a label cannot be available before the first forecast")
        if self.artifact_hash and len(self.artifact_hash) != 64:
            raise ValueError("artifact_hash must be a 64-character SHA-256 digest")
        return self

    def apply(self, probability: Decimal) -> Decimal:
        """Map one raw probability through this artifact."""
        if not probability.is_finite():
            raise ValueError("probability must be finite")
        if isinstance(self.parameters, PlattParameters):
            return apply_platt(self.parameters, probability)
        return apply_isotonic(self.parameters, probability)


def apply_platt(parameters: PlattParameters, probability: Decimal) -> Decimal:
    epsilon = parameters.epsilon
    p = _as_probability(probability, epsilon)
    logit = p.ln() - (Decimal("1") - p).ln()
    z = parameters.slope * logit + parameters.intercept
    z = min(max(z, Decimal("-60")), Decimal("60"))
    calibrated = Decimal("1") / (Decimal("1") + (-z).exp())
    return _clamp_probability(calibrated, epsilon)


def apply_isotonic(parameters: IsotonicParameters, probability: Decimal) -> Decimal:
    p = _as_probability(probability)
    thresholds = parameters.thresholds
    values = parameters.values
    if len(thresholds) == 1 or p <= thresholds[0]:
        return _clamp_probability(values[0])
    if p >= thresholds[-1]:
        return _clamp_probability(values[-1])
    index = bisect_right(thresholds, p) - 1
    left_x, right_x = thresholds[index], thresholds[index + 1]
    left_y, right_y = values[index], values[index + 1]
    if right_x == left_x:
        return _clamp_probability(right_y)
    ratio = (p - left_x) / (right_x - left_x)
    return _clamp_probability(left_y + (right_y - left_y) * ratio)


def _labeled_points(records: Sequence[EvaluationRecord]) -> list[tuple[float, int]]:
    return [(float(record.probability_yes), record.outcome_yes) for record in records]


def _ensure_both_classes(records: Sequence[EvaluationRecord]) -> None:
    outcomes = {record.outcome_yes for record in records}
    if len(outcomes) < 2:
        raise InsufficientDataError("insufficient_class_diversity")


def _logit(probability: float) -> float:
    bounded = min(max(probability, 1e-6), 1.0 - 1e-6)
    return math.log(bounded / (1.0 - bounded))


def _sigmoid(value: float) -> float:
    bounded = min(max(value, -60.0), 60.0)
    return 1.0 / (1.0 + math.exp(-bounded))


def _softplus(value: float) -> float:
    if value > 0:
        return value + math.log1p(math.exp(-value))
    return math.log1p(math.exp(value))


def _platt_loss(
    zs: Sequence[float], ys: Sequence[int], slope: float, intercept: float, l2: float
) -> float:
    total = 0.0
    for z, y in zip(zs, ys, strict=True):
        logit = slope * z + intercept
        total += _softplus(logit) - y * logit
    return total / len(zs) + l2 * (slope * slope + intercept * intercept)


def fit_platt(
    records: Sequence[EvaluationRecord],
    *,
    l2: float = 1e-6,
) -> PlattParameters:
    """Fit a slope-constrained Platt map by projected Newton iterations.

    The slope is bounded below at zero so calibrated probabilities remain a
    non-decreasing function of the raw forecast.  A negative slope would invert
    the model's ordering, which is a modelling failure rather than calibration.
    """
    if not records:
        raise InsufficientDataError("insufficient_samples")
    _ensure_both_classes(records)
    if l2 < 0:
        raise ValueError("l2 must be non-negative")

    points = _labeled_points(records)
    zs = [_logit(probability) for probability, _ in points]
    ys = [outcome for _, outcome in points]
    count = len(points)

    slope = 1.0
    intercept = 0.0
    if max(zs) - min(zs) < 1e-12:
        # No variation in the raw forecast carries no calibration signal; fit a
        # constant base rate instead of extrapolating an arbitrary slope.
        slope = 0.0
    for _ in range(200):
        probabilities = [_sigmoid(slope * z + intercept) for z in zs]
        gradient_slope = (
            sum((p - y) * z for p, y, z in zip(probabilities, ys, zs, strict=True)) / count
            + 2.0 * l2 * slope
        )
        gradient_intercept = (
            sum(p - y for p, y in zip(probabilities, ys, strict=True)) / count
            + 2.0 * l2 * intercept
        )
        if max(abs(gradient_slope), abs(gradient_intercept)) < 1e-9:
            break

        curvature = [p * (1.0 - p) for p in probabilities]
        h_ss = sum(c * z * z for c, z in zip(curvature, zs, strict=True)) / count + 2.0 * l2
        h_si = sum(c * z for c, z in zip(curvature, zs, strict=True)) / count
        h_ii = sum(curvature) / count + 2.0 * l2
        determinant = h_ss * h_ii - h_si * h_si
        if abs(determinant) < 1e-12:
            break
        delta_slope = (h_ii * gradient_slope - h_si * gradient_intercept) / determinant
        delta_intercept = (-h_si * gradient_slope + h_ss * gradient_intercept) / determinant

        current_loss = _platt_loss(zs, ys, slope, intercept, l2)
        step = 1.0
        accepted = False
        for _ in range(60):
            candidate_slope = min(max(slope - step * delta_slope, 0.0), 50.0)
            candidate_intercept = min(max(intercept - step * delta_intercept, -50.0), 50.0)
            candidate_loss = _platt_loss(zs, ys, candidate_slope, candidate_intercept, l2)
            if candidate_loss < current_loss - 1e-14:
                accepted = True
                break
            step *= 0.5
        if not accepted:
            break
        if abs(candidate_slope - slope) < 1e-12 and abs(candidate_intercept - intercept) < 1e-12:
            break
        slope, intercept = candidate_slope, candidate_intercept

    return PlattParameters(
        slope=_decimal(slope),
        intercept=_decimal(intercept),
        l2=_decimal(l2),
    )


def fit_isotonic(records: Sequence[EvaluationRecord]) -> IsotonicParameters:
    """Fit a monotone piecewise-linear map using pool-adjacent-violators."""
    if not records:
        raise InsufficientDataError("insufficient_samples")
    _ensure_both_classes(records)

    counts: dict[float, list[int]] = {}
    for probability, outcome in _labeled_points(records):
        bounded = min(
            max(probability, float(PROBABILITY_EPSILON)), 1.0 - float(PROBABILITY_EPSILON)
        )
        bucket = counts.setdefault(round(bounded, 12), [0, 0])
        bucket[0] += 1
        bucket[1] += outcome

    thresholds = sorted(counts)
    weights = [float(counts[x][0]) for x in thresholds]
    rates = [counts[x][1] / counts[x][0] for x in thresholds]

    # Each stack entry is [weighted_sum, weight, start_index, end_index].
    stack: list[list[float]] = []
    for index, (rate, weight) in enumerate(zip(rates, weights, strict=True)):
        stack.append([rate * weight, weight, float(index), float(index)])
        while len(stack) >= 2:
            previous = stack[-2]
            current = stack[-1]
            if previous[0] / previous[1] <= current[0] / current[1]:
                break
            stack[-2:] = [
                [
                    previous[0] + current[0],
                    previous[1] + current[1],
                    previous[2],
                    current[3],
                ]
            ]

    values: list[float] = []
    for weighted_sum, weight, start, end in stack:
        block_value = weighted_sum / weight
        values.extend([block_value] * (int(end) - int(start) + 1))

    clamped = [
        min(max(value, float(PROBABILITY_EPSILON)), 1.0 - float(PROBABILITY_EPSILON))
        for value in values
    ]
    # Clamping cannot break monotonicity, but enforce it defensively.
    for index in range(1, len(clamped)):
        clamped[index] = max(clamped[index], clamped[index - 1])

    return IsotonicParameters(
        thresholds=[_decimal(x) for x in thresholds],
        values=[_decimal(value) for value in clamped],
    )


def fit_calibrator(
    records: Sequence[EvaluationRecord],
    method: CalibrationMethod,
    *,
    l2: float = 1e-6,
) -> PlattParameters | IsotonicParameters:
    if method == "platt":
        return fit_platt(records, l2=l2)
    if method == "isotonic":
        return fit_isotonic(records)
    raise ValueError(f"unsupported calibration method: {method!r}")


def _apply_parameters(
    parameters: PlattParameters | IsotonicParameters,
    probability: Decimal,
) -> Decimal:
    if isinstance(parameters, PlattParameters):
        return apply_platt(parameters, probability)
    return apply_isotonic(parameters, probability)


class RecordSelection(BaseModel):
    """Eligible, timestamped records plus an audit of what was removed."""

    model_config = ConfigDict(extra="forbid")

    records: list[EvaluationRecord]
    input_count: int
    excluded_ineligible: int = 0
    excluded_missing_timestamps: int = 0


def select_calibration_records(
    records: Iterable[EvaluationRecord],
) -> RecordSelection:
    materialized = list(records)
    eligible: list[EvaluationRecord] = []
    excluded_ineligible = 0
    missing_timestamps = 0
    for record in materialized:
        if not record.evaluation_eligible:
            excluded_ineligible += 1
            continue
        if record.forecasted_at is None or record.resolved_at is None:
            missing_timestamps += 1
            continue
        eligible.append(record)
    eligible.sort(key=lambda record: (record.forecasted_at, record.forecast_id))
    return RecordSelection(
        records=eligible,
        input_count=len(materialized),
        excluded_ineligible=excluded_ineligible,
        excluded_missing_timestamps=missing_timestamps,
    )


def training_data_issue(
    records: Sequence[EvaluationRecord],
    *,
    min_samples: int = DEFAULT_MIN_TRAIN_SAMPLES,
    min_class_samples: int = DEFAULT_MIN_CLASS_SAMPLES,
) -> str | None:
    if min_samples < 1 or min_class_samples < 1:
        raise ValueError("sample thresholds must be positive")
    if len(records) < min_samples:
        return "insufficient_samples"
    positives = sum(record.outcome_yes for record in records)
    negatives = len(records) - positives
    if positives < min_class_samples or negatives < min_class_samples:
        return "insufficient_class_diversity"
    return None


def training_data_hash(records: Sequence[EvaluationRecord]) -> str:
    entries = [
        {
            "forecast_id": record.forecast_id,
            "probability_yes": str(record.probability_yes),
            "outcome_yes": record.outcome_yes,
            "forecasted_at": record.forecasted_at.isoformat(),  # type: ignore[union-attr]
            "resolved_at": record.resolved_at.isoformat(),  # type: ignore[union-attr]
        }
        for record in sorted(
            records,
            key=lambda record: (record.forecasted_at, record.forecast_id),
        )
    ]
    serialized = json.dumps(entries, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _artifact_payload(artifact: CalibrationArtifact) -> str:
    payload = artifact.model_dump(
        mode="json",
        exclude={"artifact_hash", "calibration_version", "created_at"},
    )
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def compute_artifact_hash(artifact: CalibrationArtifact) -> str:
    return hashlib.sha256(_artifact_payload(artifact).encode("utf-8")).hexdigest()


def seal_artifact(artifact: CalibrationArtifact) -> CalibrationArtifact:
    """Attach a deterministic hash and human-readable calibration version."""
    digest = compute_artifact_hash(artifact)
    version = f"{artifact.method}_v1_{digest[:12]}"
    sealed = artifact.model_copy(update={"artifact_hash": digest, "calibration_version": version})
    return CalibrationArtifact.model_validate(sealed.model_dump())


def verify_artifact(artifact: CalibrationArtifact) -> bool:
    if not artifact.artifact_hash:
        return False
    expected = compute_artifact_hash(artifact)
    return artifact.artifact_hash == expected and artifact.calibration_version == (
        f"{artifact.method}_v1_{expected[:12]}"
    )


def build_artifact(
    records: Iterable[EvaluationRecord],
    method: CalibrationMethod,
    *,
    min_samples: int = DEFAULT_MIN_TRAIN_SAMPLES,
    min_class_samples: int = DEFAULT_MIN_CLASS_SAMPLES,
    l2: float = 1e-6,
    created_at: datetime | None = None,
) -> CalibrationArtifact:
    """Fit one review artifact on every eligible, timestamped record."""
    selection = select_calibration_records(records)
    usable = selection.records
    reason = training_data_issue(
        usable, min_samples=min_samples, min_class_samples=min_class_samples
    )
    if reason is not None:
        raise InsufficientDataError(reason)

    parameters = fit_calibrator(usable, method, l2=l2)
    positives = sum(record.outcome_yes for record in usable)
    limitations = list(_LIMITATIONS)
    if method == "isotonic" and len(usable) < SMALL_SAMPLE_ISOTONIC_THRESHOLD:
        limitations.append(
            "Isotonic regression is fitted below the recommended 1000 labels; "
            "the step function may overfit and should be compared with Platt."
        )

    artifact = CalibrationArtifact(
        method=method,
        created_at=created_at or datetime.now(UTC),
        training_forecast_start=cast(datetime, usable[0].forecasted_at),
        training_forecast_end=cast(datetime, usable[-1].forecasted_at),
        label_available_through=max(
            record.resolved_at for record in usable if record.resolved_at is not None
        ),
        sample_count=len(usable),
        positive_count=positives,
        negative_count=len(usable) - positives,
        excluded_ineligible_count=selection.excluded_ineligible,
        excluded_missing_timestamps_count=selection.excluded_missing_timestamps,
        training_data_hash=training_data_hash(usable),
        parameters=parameters,
        limitations=limitations,
    )
    return seal_artifact(artifact)


class FoldMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sample_count: int
    brier_score: Decimal
    log_loss: Decimal
    ece: Decimal
    outcome_accuracy: Decimal


def _fold_metrics(records: Sequence[EvaluationRecord], n_bins: int) -> FoldMetrics:
    if not records:
        raise ValueError("cannot score an empty fold")
    scorecard = score_records(records, n_bins=n_bins)
    if (
        scorecard.brier_score is None
        or scorecard.log_loss is None
        or scorecard.ece is None
        or scorecard.outcome_accuracy is None
    ):
        raise ValueError("scorecard returned incomplete metrics for a non-empty fold")
    return FoldMetrics(
        sample_count=scorecard.resolved_count,
        brier_score=scorecard.brier_score,
        log_loss=scorecard.log_loss,
        ece=scorecard.ece,
        outcome_accuracy=scorecard.outcome_accuracy,
    )


class WalkForwardFold(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fold_index: int
    method: CalibrationMethod
    test_forecast_start: datetime
    test_forecast_end: datetime
    train_candidate_count: int
    train_count: int
    train_positive_count: int
    train_negative_count: int
    purged_label_count: int
    test_count: int
    status: Literal["scored", "insufficient_data"]
    reason: str | None = None
    baseline: FoldMetrics | None = None
    calibrated: FoldMetrics | None = None
    mean_probability_shift: Decimal | None = None


class MethodWalkForward(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method: CalibrationMethod
    status: CalibrationStatus
    folds: list[WalkForwardFold]
    scored_fold_count: int
    failure_reasons: dict[str, int] = Field(default_factory=dict)
    oos_test_count: int = 0
    baseline: Scorecard | None = None
    calibrated: Scorecard | None = None
    brier_improvement: Decimal | None = None
    log_loss_improvement: Decimal | None = None
    ece_target: Decimal
    meets_ece_target: bool | None = None


def _evaluate_method(
    records: Sequence[EvaluationRecord],
    method: CalibrationMethod,
    *,
    folds: int,
    min_train_samples: int,
    min_class_samples: int,
    n_bins: int,
    ece_target: Decimal,
    l2: float,
) -> MethodWalkForward:
    count = len(records)
    if count <= min_train_samples or folds < 1:
        early_reason = (
            "insufficient_samples" if count <= min_train_samples else "invalid_fold_count"
        )
        return MethodWalkForward(
            method=method,
            status="insufficient_data",
            folds=[],
            scored_fold_count=0,
            failure_reasons={early_reason: 1},
            ece_target=ece_target,
        )

    initial_train = min(max(min_train_samples, count // (folds + 1)), count - 1)
    block_size = max(1, math.ceil((count - initial_train) / folds))

    fold_reports: list[WalkForwardFold] = []
    oos_records: list[EvaluationRecord] = []
    calibrated_records: list[EvaluationRecord] = []
    failure_reasons: Counter[str] = Counter()

    for fold_index in range(folds):
        start = initial_train + fold_index * block_size
        if start >= count:
            break
        end = min(count, start + block_size)
        test = records[start:end]
        if not test:
            break
        test_start = test[0].forecasted_at
        if test_start is None:
            raise ValueError("selected records must carry forecasted_at")
        candidates = records[:start]
        train = [
            record
            for record in candidates
            if record.resolved_at is not None and record.resolved_at < test_start
        ]
        purged = len(candidates) - len(train)

        fold_reason: str | None = training_data_issue(
            train, min_samples=min_train_samples, min_class_samples=min_class_samples
        )
        if fold_reason is not None and purged:
            fold_reason = f"{fold_reason}_after_purge"

        fold = WalkForwardFold(
            fold_index=fold_index,
            method=method,
            test_forecast_start=test_start,
            test_forecast_end=test[-1].forecasted_at or test_start,
            train_candidate_count=len(candidates),
            train_count=len(train),
            train_positive_count=sum(record.outcome_yes for record in train),
            train_negative_count=len(train) - sum(record.outcome_yes for record in train),
            purged_label_count=purged,
            test_count=len(test),
            status="insufficient_data" if fold_reason is not None else "scored",
            reason=fold_reason,
        )
        if fold_reason is not None:
            failure_reasons[fold_reason] += 1
            fold_reports.append(fold)
            continue

        parameters = fit_calibrator(train, method, l2=l2)
        calibrated_probabilities = [
            _apply_parameters(parameters, record.probability_yes) for record in test
        ]
        calibrated_test = [
            record.model_copy(update={"probability_yes": probability})
            for record, probability in zip(test, calibrated_probabilities, strict=True)
        ]
        raw_mean = sum((record.probability_yes for record in test), Decimal("0")) / Decimal(
            len(test)
        )
        calibrated_mean = sum(calibrated_probabilities, Decimal("0")) / Decimal(
            len(calibrated_probabilities)
        )

        fold.baseline = _fold_metrics(test, n_bins)
        fold.calibrated = _fold_metrics(calibrated_test, n_bins)
        fold.mean_probability_shift = calibrated_mean - raw_mean
        fold_reports.append(fold)
        oos_records.extend(test)
        calibrated_records.extend(calibrated_test)

    baseline = score_records(oos_records, n_bins=n_bins) if oos_records else None
    calibrated = score_records(calibrated_records, n_bins=n_bins) if calibrated_records else None
    brier_improvement = None
    log_loss_improvement = None
    meets_ece_target = None
    if baseline is not None and calibrated is not None:
        if baseline.brier_score is not None and calibrated.brier_score is not None:
            brier_improvement = baseline.brier_score - calibrated.brier_score
        if baseline.log_loss is not None and calibrated.log_loss is not None:
            log_loss_improvement = baseline.log_loss - calibrated.log_loss
        if calibrated.ece is not None:
            meets_ece_target = calibrated.ece <= ece_target

    return MethodWalkForward(
        method=method,
        status="evaluated" if oos_records else "insufficient_data",
        folds=fold_reports,
        scored_fold_count=sum(1 for fold in fold_reports if fold.status == "scored"),
        failure_reasons=dict(sorted(failure_reasons.items())),
        oos_test_count=len(oos_records),
        baseline=baseline,
        calibrated=calibrated,
        brier_improvement=brier_improvement,
        log_loss_improvement=log_loss_improvement,
        ece_target=ece_target,
        meets_ece_target=meets_ece_target,
    )


class DatasetSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_count: int
    eligible_count: int
    excluded_ineligible: int
    excluded_missing_timestamps: int
    positive_count: int
    negative_count: int
    forecast_start: datetime | None
    forecast_end: datetime | None
    label_available_start: datetime | None
    label_available_end: datetime | None
    category_counts: dict[str, int] = Field(default_factory=dict)


class CalibrationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report_version: Literal["calibration_report_v1"] = REPORT_VERSION
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    dataset: DatasetSummary
    folds: int
    min_train_samples: int
    min_class_samples: int
    ece_target: Decimal
    methods: list[MethodWalkForward]
    verdict: CalibrationStatus
    recommended_method: CalibrationMethod | None = None
    recommendation_reason: str
    sample_size_guidance: str
    deployment_status: Literal["review_required"] = "review_required"
    artifacts: dict[str, str] = Field(default_factory=dict)
    artifact_skips: dict[str, str] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)


def _dataset_summary(selection: RecordSelection) -> DatasetSummary:
    usable = selection.records
    categories = Counter((record.category or "unknown").lower() for record in usable)
    positives = sum(record.outcome_yes for record in usable)
    return DatasetSummary(
        input_count=selection.input_count,
        eligible_count=len(usable),
        excluded_ineligible=selection.excluded_ineligible,
        excluded_missing_timestamps=selection.excluded_missing_timestamps,
        positive_count=positives,
        negative_count=len(usable) - positives,
        forecast_start=usable[0].forecasted_at if usable else None,
        forecast_end=usable[-1].forecasted_at if usable else None,
        label_available_start=(
            min(record.resolved_at for record in usable if record.resolved_at is not None)
            if usable
            else None
        ),
        label_available_end=(
            max(record.resolved_at for record in usable if record.resolved_at is not None)
            if usable
            else None
        ),
        category_counts=dict(sorted(categories.items())),
    )


def build_calibration_report(
    records: Iterable[EvaluationRecord],
    *,
    methods: Sequence[CalibrationMethod] = ("platt", "isotonic"),
    folds: int = DEFAULT_FOLDS,
    min_train_samples: int = DEFAULT_MIN_TRAIN_SAMPLES,
    min_class_samples: int = DEFAULT_MIN_CLASS_SAMPLES,
    n_bins: int = 10,
    ece_target: Decimal = DEFAULT_ECE_TARGET,
    l2: float = 1e-6,
    generated_at: datetime | None = None,
) -> CalibrationReport:
    """Run purged walk-forward evaluation for each requested method."""
    if folds < 1:
        raise ValueError("folds must be at least 1")
    if n_bins < 1:
        raise ValueError("n_bins must be at least 1")
    if min_train_samples < 1:
        raise ValueError("min_train_samples must be at least 1")
    if min_class_samples < 1:
        raise ValueError("min_class_samples must be at least 1")
    if not ece_target.is_finite() or not (Decimal("0") < ece_target < Decimal("1")):
        raise ValueError("ece_target must lie strictly between 0 and 1")
    if len(set(methods)) != len(methods):
        raise ValueError("methods must not contain duplicates")
    if any(method not in {"platt", "isotonic"} for method in methods):
        raise ValueError("methods must be 'platt' and/or 'isotonic'")

    selection = select_calibration_records(records)
    dataset = _dataset_summary(selection)
    method_reports = [
        _evaluate_method(
            selection.records,
            method,
            folds=folds,
            min_train_samples=min_train_samples,
            min_class_samples=min_class_samples,
            n_bins=n_bins,
            ece_target=ece_target,
            l2=l2,
        )
        for method in methods
    ]

    comparable: list[tuple[MethodWalkForward, Decimal, Decimal]] = []
    for report in method_reports:
        if report.status != "evaluated" or report.baseline is None or report.calibrated is None:
            continue
        baseline_brier = report.baseline.brier_score
        calibrated_brier = report.calibrated.brier_score
        if baseline_brier is None or calibrated_brier is None:
            continue
        comparable.append((report, baseline_brier, calibrated_brier))

    recommended: CalibrationMethod | None = None
    if not comparable:
        reason = "no_method_completed_out_of_sample_evaluation"
    else:
        best, baseline_brier, calibrated_brier = min(
            comparable,
            key=lambda item: (item[2], item[0].method != "platt"),
        )
        improvement = baseline_brier - calibrated_brier
        if improvement <= 0:
            reason = "no_out_of_sample_brier_improvement"
        elif best.meets_ece_target is not True:
            reason = "calibrated_ece_above_target"
        else:
            recommended = best.method
            reason = "out_of_sample_brier_improvement_and_ece_target_met"

    if dataset.eligible_count < min_train_samples:
        guidance = "insufficient_data"
    elif dataset.eligible_count < SMALL_SAMPLE_ISOTONIC_THRESHOLD:
        guidance = "platt_preferred_small_sample"
    else:
        guidance = "isotonic_supported"

    limitations = list(_LIMITATIONS)
    if (
        recommended is not None
        and recommended == "isotonic"
        and guidance == "platt_preferred_small_sample"
    ):
        limitations.append(
            "Isotonic was selected below 1000 labels; review the fold-level stability before promotion."
        )

    return CalibrationReport(
        generated_at=generated_at or datetime.now(UTC),
        dataset=dataset,
        folds=folds,
        min_train_samples=min_train_samples,
        min_class_samples=min_class_samples,
        ece_target=ece_target,
        methods=method_reports,
        verdict=(
            "evaluated"
            if any(report.status == "evaluated" for report in method_reports)
            else "insufficient_data"
        ),
        recommended_method=recommended,
        recommendation_reason=reason,
        sample_size_guidance=guidance,
        limitations=limitations,
    )


__all__ = [
    "ARTIFACT_VERSION",
    "DEFAULT_ECE_TARGET",
    "DEFAULT_FOLDS",
    "DEFAULT_MIN_CLASS_SAMPLES",
    "DEFAULT_MIN_TRAIN_SAMPLES",
    "REPORT_VERSION",
    "CalibrationArtifact",
    "CalibrationMethod",
    "CalibrationReport",
    "DatasetSummary",
    "FoldMetrics",
    "InsufficientDataError",
    "IsotonicParameters",
    "MethodWalkForward",
    "PlattParameters",
    "RecordSelection",
    "WalkForwardFold",
    "apply_isotonic",
    "apply_platt",
    "build_artifact",
    "build_calibration_report",
    "compute_artifact_hash",
    "fit_calibrator",
    "fit_isotonic",
    "fit_platt",
    "seal_artifact",
    "select_calibration_records",
    "training_data_hash",
    "verify_artifact",
]
