from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from prediction_ai.evaluation.calibrate_cli import main as calibrate_main
from prediction_ai.evaluation.calibration import (
    CalibrationArtifact,
    InsufficientDataError,
    apply_isotonic,
    apply_platt,
    build_artifact,
    build_calibration_report,
    compute_artifact_hash,
    fit_isotonic,
    fit_platt,
    select_calibration_records,
    verify_artifact,
)
from prediction_ai.evaluation.exporter import write_jsonl
from prediction_ai.evaluation.scorecard import EvaluationRecord, score_records


def _record(
    *,
    forecast_id: str,
    probability: str,
    outcome: str,
    forecasted_at: datetime,
    resolved_at: datetime,
    evaluation_eligible: bool = True,
    category: str = "crypto",
) -> EvaluationRecord:
    return EvaluationRecord(
        forecast_id=forecast_id,
        probability_yes=Decimal(probability),
        actual_outcome=outcome,
        recommendation="NO_TRADE",
        forecasted_at=forecasted_at,
        resolved_at=resolved_at,
        evaluation_eligible=evaluation_eligible,
        category=category,
    )


def _calibrated_population(records_per_level: int = 100) -> list[EvaluationRecord]:
    """Latent outcome rates 0.1..0.9, forecasts deliberately overconfident.

    Levels are interleaved and positives are spread deterministically so every
    walk-forward block observes the full forecast range and class mix.
    """
    start = datetime(2026, 1, 1, tzinfo=UTC)
    records: list[EvaluationRecord] = []
    for index in range(9 * records_per_level):
        level = index % 9
        offset = index // 9
        latent = (level + 1) / 10
        positives = round(latent * records_per_level)
        positive_offsets = {(position * 37) % records_per_level for position in range(positives)}
        forecast = min(max(0.5 + 1.8 * (latent - 0.5), 0.01), 0.99)
        forecasted_at = start + timedelta(hours=index)
        records.append(
            _record(
                forecast_id=f"f-{index}",
                probability=str(round(forecast, 6)),
                outcome="YES" if offset in positive_offsets else "NO",
                forecasted_at=forecasted_at,
                resolved_at=forecasted_at + timedelta(minutes=30),
            )
        )
    return records


def test_platt_fit_improves_calibration_and_is_monotone() -> None:
    records = _calibrated_population()
    parameters = fit_platt(records)

    assert parameters.slope > 0
    grid = [Decimal(value) / Decimal("100") for value in range(1, 100)]
    mapped = [apply_platt(parameters, value) for value in grid]
    assert all(left <= right for left, right in zip(mapped, mapped[1:], strict=False))
    assert all(Decimal("0") < value < Decimal("1") for value in mapped)

    calibrated = [
        record.model_copy(
            update={"probability_yes": apply_platt(parameters, record.probability_yes)}
        )
        for record in records
    ]
    raw_brier = score_records(records).brier_score
    calibrated_brier = score_records(calibrated).brier_score
    assert raw_brier is not None and calibrated_brier is not None
    assert calibrated_brier < raw_brier


def test_isotonic_fit_is_monotone_and_improves_calibration() -> None:
    records = _calibrated_population()
    parameters = fit_isotonic(records)

    assert parameters.thresholds == sorted(parameters.thresholds)
    assert len(set(parameters.thresholds)) == len(parameters.thresholds)
    assert all(
        left <= right for left, right in zip(parameters.values, parameters.values[1:], strict=False)
    )
    grid = [Decimal(value) / Decimal("200") for value in range(0, 201)]
    mapped = [apply_isotonic(parameters, value) for value in grid]
    assert all(left <= right for left, right in zip(mapped, mapped[1:], strict=False))
    assert all(Decimal("0") < value < Decimal("1") for value in mapped)

    calibrated = [
        record.model_copy(
            update={"probability_yes": apply_isotonic(parameters, record.probability_yes)}
        )
        for record in records
    ]
    raw_brier = score_records(records).brier_score
    calibrated_brier = score_records(calibrated).brier_score
    assert raw_brier is not None and calibrated_brier is not None
    assert calibrated_brier < raw_brier


def test_insufficient_samples_and_class_diversity_are_honest() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    small = [
        _record(
            forecast_id=f"small-{index}",
            probability="0.7",
            outcome="YES" if index % 2 else "NO",
            forecasted_at=start + timedelta(hours=index),
            resolved_at=start + timedelta(hours=index, minutes=30),
        )
        for index in range(10)
    ]
    with pytest.raises(InsufficientDataError) as small_error:
        build_artifact(small, "platt")
    assert small_error.value.reason == "insufficient_samples"

    one_class = [
        _record(
            forecast_id=f"one-{index}",
            probability="0.7",
            outcome="YES",
            forecasted_at=start + timedelta(hours=index),
            resolved_at=start + timedelta(hours=index, minutes=30),
        )
        for index in range(40)
    ]
    with pytest.raises(InsufficientDataError) as class_error:
        build_artifact(one_class, "platt", min_samples=30)
    assert class_error.value.reason == "insufficient_class_diversity"

    almost_one_class = [
        _record(
            forecast_id=f"almost-{index}",
            probability="0.7",
            outcome="NO" if index == 0 else "YES",
            forecasted_at=start + timedelta(hours=index),
            resolved_at=start + timedelta(hours=index, minutes=30),
        )
        for index in range(40)
    ]
    with pytest.raises(InsufficientDataError) as rare_class_error:
        build_artifact(almost_one_class, "platt", min_samples=30, min_class_samples=5)
    assert rare_class_error.value.reason == "insufficient_class_diversity"


def test_platt_constant_scores_fit_a_constant_base_rate() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    records = [
        _record(
            forecast_id=f"f-{index}",
            probability="0.8",
            outcome="YES" if index % 2 else "NO",
            forecasted_at=start + timedelta(hours=index),
            resolved_at=start + timedelta(hours=index, minutes=5),
        )
        for index in range(40)
    ]
    parameters = fit_platt(records)

    assert parameters.slope == Decimal("0")
    assert apply_platt(parameters, Decimal("0.2")) == apply_platt(parameters, Decimal("0.8"))


def test_selection_excludes_ineligible_and_unlabeled_records() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    records = [
        _record(
            forecast_id="ok",
            probability="0.6",
            outcome="YES",
            forecasted_at=start,
            resolved_at=start + timedelta(minutes=1),
        ),
        _record(
            forecast_id="abstained",
            probability="0.6",
            outcome="YES",
            forecasted_at=start,
            resolved_at=start + timedelta(minutes=1),
            evaluation_eligible=False,
        ),
        EvaluationRecord(
            forecast_id="unlabeled",
            probability_yes=Decimal("0.6"),
            actual_outcome="YES",
            forecasted_at=None,
            resolved_at=None,
        ),
    ]

    selection = select_calibration_records(records)
    assert [record.forecast_id for record in selection.records] == ["ok"]
    assert selection.input_count == 3
    assert selection.excluded_ineligible == 1
    assert selection.excluded_missing_timestamps == 1


def test_walk_forward_purges_labels_resolved_after_test_start() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    records = []
    for index in range(8):
        forecasted_at = start + timedelta(days=index)
        # The first four labels stay unresolved until well after the test block
        # starts; the last four resolve quickly and are not in any training set.
        resolved_at = forecasted_at + (timedelta(days=90) if index < 4 else timedelta(hours=1))
        records.append(
            _record(
                forecast_id=f"f-{index}",
                probability="0.6" if index % 2 else "0.4",
                outcome="YES" if index % 2 else "NO",
                forecasted_at=forecasted_at,
                resolved_at=resolved_at,
            )
        )

    report = build_calibration_report(
        records,
        methods=("platt",),
        folds=1,
        min_train_samples=2,
        min_class_samples=1,
    )
    method = report.methods[0]
    assert method.status == "scored" or method.status == "insufficient_data"
    assert len(method.folds) == 1
    fold = method.folds[0]
    assert fold.train_candidate_count == 4
    assert fold.purged_label_count == 4
    assert fold.train_count == 0
    assert fold.status == "insufficient_data"
    assert fold.reason == "insufficient_samples_after_purge"


def test_walk_forward_uses_only_labels_available_before_test() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    records = []
    for index in range(12):
        forecasted_at = start + timedelta(days=index)
        # One early label is delayed past the test start and must be purged.
        delayed = index == 2
        resolved_at = forecasted_at + (timedelta(days=20) if delayed else timedelta(hours=1))
        records.append(
            _record(
                forecast_id=f"f-{index}",
                probability="0.65" if index % 2 else "0.35",
                outcome="YES" if index % 2 else "NO",
                forecasted_at=forecasted_at,
                resolved_at=resolved_at,
            )
        )

    report = build_calibration_report(
        records,
        methods=("platt",),
        folds=1,
        min_train_samples=4,
        min_class_samples=1,
    )
    fold = report.methods[0].folds[0]
    assert fold.train_candidate_count == 6
    assert fold.purged_label_count == 1
    assert fold.train_count == 5
    assert fold.status == "scored"


def test_artifact_hash_is_deterministic_and_verifiable() -> None:
    records = _calibrated_population(records_per_level=20)
    first = build_artifact(
        records,
        "platt",
        min_samples=30,
        min_class_samples=5,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    second = build_artifact(
        records,
        "platt",
        min_samples=30,
        min_class_samples=5,
        created_at=datetime(2026, 2, 1, tzinfo=UTC),
    )

    assert first.artifact_hash == second.artifact_hash
    assert first.calibration_version == second.calibration_version
    assert compute_artifact_hash(first) == first.artifact_hash
    assert verify_artifact(first) is True

    tampered = first.model_copy(update={"training_data_hash": "0" * 64})
    assert verify_artifact(tampered) is False
    assert first.deployment_status == "review_required"


def test_artifact_round_trips_through_json() -> None:
    artifact = build_artifact(
        _calibrated_population(records_per_level=20), "isotonic", min_samples=30
    )
    payload = json.loads(artifact.model_dump_json())
    restored = CalibrationArtifact.model_validate(payload)

    assert verify_artifact(restored) is True
    assert restored.apply(Decimal("0.73")) == artifact.apply(Decimal("0.73"))


def test_report_insufficient_data_is_a_successful_honest_verdict() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    records = [
        _record(
            forecast_id=f"f-{index}",
            probability="0.6",
            outcome="YES" if index % 2 else "NO",
            forecasted_at=start + timedelta(hours=index),
            resolved_at=start + timedelta(hours=index, minutes=5),
        )
        for index in range(10)
    ]

    report = build_calibration_report(records, folds=3)
    assert report.verdict == "insufficient_data"
    assert report.recommended_method is None
    assert report.recommendation_reason == "no_method_completed_out_of_sample_evaluation"
    assert report.deployment_status == "review_required"
    assert all(method.status == "insufficient_data" for method in report.methods)


def test_report_compares_methods_out_of_sample() -> None:
    records = _calibrated_population()
    report = build_calibration_report(
        records,
        folds=3,
        min_train_samples=100,
        min_class_samples=10,
        n_bins=10,
    )

    assert report.verdict == "evaluated"
    assert report.dataset.eligible_count == 900
    assert report.sample_size_guidance == "platt_preferred_small_sample"
    assert report.recommended_method == "platt"
    assert report.deployment_status == "review_required"
    for method in report.methods:
        assert method.status == "evaluated"
        assert method.oos_test_count > 0
        assert method.brier_improvement is not None
        assert method.brier_improvement > 0
        assert method.log_loss_improvement is not None
        assert method.meets_ece_target is True
    assert report.recommendation_reason == ("out_of_sample_brier_improvement_and_ece_target_met")


def test_calibrate_cli_writes_review_artifacts_without_applying_them(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "resolved.jsonl"
    output_path = tmp_path / "report.json"
    artifact_dir = tmp_path / "artifacts"
    write_jsonl(_calibrated_population(records_per_level=40), input_path)

    assert (
        calibrate_main(
            [
                "--input",
                str(input_path),
                "--output",
                str(output_path),
                "--artifact-dir",
                str(artifact_dir),
                "--folds",
                "2",
                "--min-train-samples",
                "100",
                "--min-class-samples",
                "10",
            ]
        )
        == 0
    )

    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["deployment_status"] == "review_required"
    assert report["verdict"] == "evaluated"
    assert set(report["artifacts"]) == {"platt", "isotonic"}
    for method, path in report["artifacts"].items():
        artifact = json.loads(Path(path).read_text(encoding="utf-8"))
        assert artifact["deployment_status"] == "review_required"
        assert artifact["method"] == method
        assert len(artifact["artifact_hash"]) == 64


def test_calibrate_cli_skips_artifacts_when_data_is_insufficient(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "small.jsonl"
    output_path = tmp_path / "report.json"
    start = datetime(2026, 1, 1, tzinfo=UTC)
    records = [
        _record(
            forecast_id=f"f-{index}",
            probability="0.6",
            outcome="YES" if index % 2 else "NO",
            forecasted_at=start + timedelta(hours=index),
            resolved_at=start + timedelta(hours=index, minutes=5),
        )
        for index in range(10)
    ]
    write_jsonl(records, input_path)

    assert (
        calibrate_main(
            [
                "--input",
                str(input_path),
                "--output",
                str(output_path),
                "--artifact-dir",
                str(tmp_path / "artifacts"),
            ]
        )
        == 0
    )

    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["verdict"] == "insufficient_data"
    assert report["artifacts"] == {}
    assert report["artifact_skips"] == {
        "platt": "insufficient_samples",
        "isotonic": "insufficient_samples",
    }
