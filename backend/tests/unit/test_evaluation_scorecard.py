import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from prediction_ai.evaluation.cli import main
from prediction_ai.evaluation.scorecard import (
    EvaluationRecord,
    score_records,
)


def _record(
    probability: str,
    outcome: str,
    *,
    market_probability: str | None = None,
    recommendation: str = "NO_TRADE",
    effective_cost_yes: str | None = None,
    effective_cost_no: str | None = None,
    forecasted_at: datetime | None = None,
    resolved_at: datetime | None = None,
    evaluation_eligible: bool = True,
) -> EvaluationRecord:
    return EvaluationRecord(
        forecast_id=f"f-{probability}-{outcome}",
        probability_yes=Decimal(probability),
        actual_outcome=outcome,
        market_probability_yes=(
            Decimal(market_probability) if market_probability is not None else None
        ),
        recommendation=recommendation,
        effective_cost_yes=(
            Decimal(effective_cost_yes) if effective_cost_yes is not None else None
        ),
        effective_cost_no=(
            Decimal(effective_cost_no) if effective_cost_no is not None else None
        ),
        forecasted_at=forecasted_at,
        resolved_at=resolved_at,
        evaluation_eligible=evaluation_eligible,
    )


def test_brier_and_log_loss_known_values() -> None:
    scorecard = score_records(
        [_record("0.80", "YES"), _record("0.30", "NO")], n_bins=10
    )

    # (0.8-1)^2 = 0.04; (0.3-0)^2 = 0.09; mean = 0.065.
    assert scorecard.brier_score == Decimal("0.065")
    assert scorecard.outcome_accuracy == Decimal("1.0")
    assert scorecard.sharpness is not None


def test_bss_compares_against_market_baseline() -> None:
    # Model is perfect; market is wrong on both records.
    records = [
        _record("0.90", "YES", market_probability="0.10"),
        _record("0.10", "NO", market_probability="0.90"),
    ]
    scorecard = score_records(records)

    assert scorecard.brier_score == Decimal("0.01")
    assert scorecard.market_brier_score == Decimal("0.81")
    assert scorecard.brier_skill_score == Decimal("1") - (
        Decimal("0.01") / Decimal("0.81")
    )


def test_bss_can_be_negative_when_model_is_worse_than_market() -> None:
    records = [_record("0.10", "YES", market_probability="0.90")]
    scorecard = score_records(records)

    assert scorecard.brier_skill_score is not None
    assert scorecard.brier_skill_score < Decimal("0")


def test_calibration_bins_and_ece() -> None:
    # Ten forecasts at 0.9, all YES; ten at 0.1, all NO.
    records = [_record("0.90", "YES") for _ in range(10)] + [
        _record("0.10", "NO") for _ in range(10)
    ]
    scorecard = score_records(records, n_bins=10)

    assert scorecard.ece == Decimal("0.1")
    assert scorecard.mce == Decimal("0.1")
    populated = [item for item in scorecard.calibration_bins if item.count > 0]
    assert len(populated) == 2
    assert populated[0].observed_frequency == Decimal("0")


def test_no_trade_is_not_counted_as_correct_or_as_a_trade() -> None:
    scorecard = score_records(
        [
            _record("0.60", "NO", recommendation="NO_TRADE"),
            _record(
                "0.80",
                "YES",
                recommendation="BUY_YES",
                effective_cost_yes="0.55",
            ),
        ]
    )

    assert scorecard.decision.trade_count == 1
    assert scorecard.decision.winning_trades == 1
    assert scorecard.decision.no_trade_count == 1
    assert scorecard.decision.no_trade_rate == Decimal("0.5")
    assert scorecard.decision.total_realized_pnl_per_share == Decimal("0.45")


def test_decision_pnl_after_costs_and_side_specific_edge() -> None:
    scorecard = score_records(
        [
            _record(
                "0.70",
                "NO",
                recommendation="BUY_YES",
                effective_cost_yes="0.60",
            ),
            _record(
                "0.30",
                "NO",
                recommendation="BUY_NO",
                effective_cost_no="0.55",
            ),
        ]
    )

    # BUY_YES loses 0.60; BUY_NO earns 1 - 0.55 = 0.45; total = -0.15.
    assert scorecard.decision.trade_count == 2
    assert scorecard.decision.winning_trades == 1
    assert scorecard.decision.losing_trades == 1
    assert scorecard.decision.total_realized_pnl_per_share == Decimal("-0.15")


def test_excluded_records_do_not_enter_probability_metrics() -> None:
    scorecard = score_records(
        [
            _record("0.90", "YES"),
            _record("0.10", "YES", evaluation_eligible=False),
        ]
    )

    assert scorecard.sample_count == 2
    assert scorecard.resolved_count == 1
    assert scorecard.excluded_count == 1
    assert scorecard.brier_score == Decimal("0.01")


def test_point_in_time_order_is_enforced() -> None:
    now = datetime(2026, 11, 10, 18, 0, tzinfo=UTC)
    with pytest.raises(ValueError, match="resolved_at must be later"):
        _record(
            "0.60",
            "YES",
            forecasted_at=now,
            resolved_at=now - timedelta(minutes=1),
        )


def test_unknown_outcome_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unrecognized settlement outcome"):
        _record("0.60", "MAYBE")


def test_jsonl_cli_emits_scorecard(tmp_path: Path, capsys) -> None:
    input_path = tmp_path / "resolved.jsonl"
    input_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "forecast_id": "f1",
                        "probability_yes": "0.80",
                        "actual_outcome": "YES",
                        "market_probability_yes": "0.50",
                        "recommendation": "BUY_YES",
                        "effective_cost_yes": "0.60",
                    }
                ),
                json.dumps(
                    {
                        "forecast_id": "f2",
                        "probability_yes": "0.20",
                        "actual_outcome": "NO",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    exit_code = main(["--input", str(input_path), "--bins", "10"])

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["resolved_count"] == 2
    assert Decimal(output["brier_score"]) == Decimal("0.04")
    assert output["decision"]["trade_count"] == 1
    assert Decimal(output["decision"]["total_realized_pnl_per_share"]) == Decimal("0.4")
