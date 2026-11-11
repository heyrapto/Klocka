from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import anyio
import pytest

from prediction_ai.db.sqlite import SQLiteStore
from prediction_ai.evaluation.export_cli import main as export_main
from prediction_ai.evaluation.exporter import (
    export_pairs,
    forecast_feedback_to_record,
    write_jsonl,
)
from prediction_ai.evaluation.scorecard import load_jsonl
from prediction_ai.evaluation.sqlite_export import export_records_from_sqlite


def _forecast(
    *,
    forecast_id: str = "forecast-1",
    analysis_id: str = "analysis-1",
    forecasted_at: str | None = "2026-11-10T18:00:00+00:00",
    probability_yes: object = 0.62,
    market_implied: object = 0.55,
    market_anchor_available: bool = True,
    settlement_at: str = "2026-11-10T18:15:00+00:00",
    recommendation: str = "BUY_YES",
) -> dict:
    return {
        "id": forecast_id,
        "analysis_id": analysis_id,
        "user_id": "user-1",
        "market_id": f"market-{analysis_id}",
        "forecasted_at": forecasted_at,
        "probability_yes": probability_yes,
        "probability_no": 1 - float(probability_yes)
        if isinstance(probability_yes, (int, float))
        else 0.5,
        "uncertainty_interval": [0.5, 0.7],
        "recommendation": recommendation,
        "decision_metrics": {
            "effective_cost_yes": "0.56",
            "effective_cost_no": "0.47",
            "executable_ask_yes": "0.55",
            "executable_ask_no": "0.46",
        },
        "market": {"title": "BTC up?", "category": "crypto", "underlying_symbol": "BTC"},
        "model_version": "market_baseline_v1",
        "calibration_version": "unfitted_linear_normalized_v2",
        "contract_spec": {"spec_hash": "spec-1"},
        "input_snapshot": {
            "market_implied": market_implied,
            "market_anchor_available": market_anchor_available,
        },
        "target_definition": {"settlement_timestamp": "2026-11-10T18:15:00+00:00"},
        "timing_provenance": {"exact_resolves_at": settlement_at},
        "created_at": "2026-11-10T17:59:00+00:00",
    }


def _feedback(
    *,
    feedback_id: str = "feedback-1",
    analysis_id: str = "analysis-1",
    actual_outcome: str = "YES",
    created_at: str | None = "2026-11-10T18:20:00+00:00",
    category: str = "crypto",
    evaluation_eligible: bool = True,
) -> dict:
    return {
        "feedback_id": feedback_id,
        "analysis_id": analysis_id,
        "user_id": "user-1",
        "category": category,
        "market_title": "BTC up?",
        "recommendation": "BUY_YES",
        "actual_outcome": actual_outcome,
        "was_correct": True,
        "evaluation_eligible": evaluation_eligible,
        "probability_yes": 0.62,
        "contract_spec_hash": "spec-1",
        "created_at": created_at,
    }


async def _seed(
    store: SQLiteStore,
    *,
    count: int = 1,
    category: str = "crypto",
) -> None:
    for index in range(1, count + 1):
        minute = index % 60
        hour = 18 + index // 60
        forecasted_at = f"2026-11-10T{hour:02d}:{minute:02d}:00+00:00"
        resolved_at = (datetime.fromisoformat(forecasted_at) + timedelta(minutes=5)).isoformat()
        await store.save_forecast(
            _forecast(
                forecast_id=f"forecast-{index}",
                analysis_id=f"analysis-{index}",
                forecasted_at=forecasted_at,
                settlement_at=resolved_at,
            )
        )
        await store.record_feedback(
            _feedback(
                feedback_id=f"feedback-{index}",
                analysis_id=f"analysis-{index}",
                created_at=resolved_at,
                category=category,
            )
        )


@pytest.mark.asyncio
async def test_list_resolved_pairs_orders_and_paginates(tmp_path: Path) -> None:
    store = SQLiteStore(db_path=str(tmp_path / "pairs.db"))
    store.initialize()
    await _seed(store, count=3)

    first = await store.list_resolved_forecast_feedback(limit=2)
    assert [pair["forecast"]["forecast_id"] for pair in first] == [
        "forecast-1",
        "forecast-2",
    ]

    last = first[-1]["forecast"]
    second = await store.list_resolved_forecast_feedback(
        limit=2,
        after_forecasted_at=last["forecasted_at"],
        after_forecast_id=last["forecast_id"],
    )
    assert [pair["forecast"]["forecast_id"] for pair in second] == ["forecast-3"]

    with pytest.raises(ValueError):
        await store.list_resolved_forecast_feedback(
            limit=2, after_forecasted_at=last["forecasted_at"]
        )


@pytest.mark.asyncio
async def test_list_resolved_pairs_filters_category_and_excludes_unsettled(
    tmp_path: Path,
) -> None:
    store = SQLiteStore(db_path=str(tmp_path / "filters.db"))
    store.initialize()
    await _seed(store, count=1, category="crypto")
    await store.save_forecast(_forecast(forecast_id="other-forecast", analysis_id="other"))
    await store.record_feedback(
        _feedback(
            feedback_id="other-feedback",
            analysis_id="other",
            category="sports",
        )
    )
    # A forecast with no feedback must never appear.
    await store.save_forecast(_forecast(forecast_id="unsettled", analysis_id="unsettled"))
    # A forecast with no forecasted_at cannot be ordered point-in-time.
    await store.save_forecast(
        _forecast(
            forecast_id="no-time",
            analysis_id="no-time",
            forecasted_at=None,
        )
    )
    await store.record_feedback(_feedback(feedback_id="no-time-fb", analysis_id="no-time"))

    pairs = await store.list_resolved_forecast_feedback(limit=100, category="CRYPTO")
    assert {pair["forecast"]["forecast_id"] for pair in pairs} == {"forecast-1"}

    all_pairs = await store.list_resolved_forecast_feedback(limit=100)
    assert "unsettled" not in {pair["forecast"]["forecast_id"] for pair in all_pairs}
    assert "no-time" not in {pair["forecast"]["forecast_id"] for pair in all_pairs}


@pytest.mark.asyncio
async def test_malformed_stored_snapshot_is_skipped_with_reason(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "malformed.db"
    store = SQLiteStore(db_path=str(db_path))
    store.initialize()
    await _seed(store, count=1)

    connection = sqlite3.connect(db_path)
    connection.execute(
        "UPDATE forecasts SET input_snapshot = 'not-json' WHERE forecast_id = ?",
        ("forecast-1",),
    )
    connection.commit()
    connection.close()

    pairs = await store.list_resolved_forecast_feedback(limit=10)
    records, summary = export_pairs(pairs)

    assert records == []
    assert summary.exported == 0
    assert summary.skipped == 1
    assert summary.skipped_reasons == {"malformed_input_snapshot": 1}


@pytest.mark.asyncio
async def test_repository_limit_is_capped_at_one_hundred(tmp_path: Path) -> None:
    store = SQLiteStore(db_path=str(tmp_path / "cap.db"))
    store.initialize()
    await _seed(store, count=105)

    pairs = await store.list_resolved_forecast_feedback(limit=10_000)
    assert len(pairs) == 100


def test_exporter_uses_conservative_resolved_at() -> None:
    # Feedback arrived after settlement: the label became observable at feedback time.
    late_feedback = forecast_feedback_to_record(
        {
            "forecast": _forecast(),
            "feedback": _feedback(created_at="2026-11-10T18:20:00+00:00"),
        }
    )
    assert late_feedback.resolved_at == datetime(2026, 11, 10, 18, 20, tzinfo=UTC)

    # Settlement arrived after feedback: the settlement timestamp dominates.
    late_settlement = forecast_feedback_to_record(
        {
            "forecast": _forecast(settlement_at="2026-11-10T18:25:00+00:00"),
            "feedback": _feedback(created_at="2026-11-10T18:20:00+00:00"),
        }
    )
    assert late_settlement.resolved_at == datetime(2026, 11, 10, 18, 25, tzinfo=UTC)


def test_exporter_copies_execution_and_market_fields() -> None:
    record = forecast_feedback_to_record({"forecast": _forecast(), "feedback": _feedback()})

    assert record.probability_yes == Decimal("0.62")
    assert record.market_probability_yes == Decimal("0.55")
    assert record.effective_cost_yes == Decimal("0.56")
    assert record.ask_yes == Decimal("0.55")
    assert record.category == "crypto"
    assert record.asset == "BTC"
    assert record.contract_spec_hash == "spec-1"
    assert record.forecasted_at == datetime(2026, 11, 10, 18, 0, tzinfo=UTC)


def test_exporter_drops_missing_market_anchor_instead_of_inventing_one() -> None:
    record = forecast_feedback_to_record(
        {
            "forecast": _forecast(market_implied=None, market_anchor_available=False),
            "feedback": _feedback(),
        }
    )
    assert record.market_probability_yes is None


def test_exporter_falls_back_to_serialized_model_components() -> None:
    forecast = _forecast(market_implied=None)
    forecast["model_components"] = {"market_implied_probability": 0.58}
    record = forecast_feedback_to_record({"forecast": forecast, "feedback": _feedback()})
    assert record.market_probability_yes == Decimal("0.58")


def test_exporter_parses_string_evaluation_eligibility() -> None:
    feedback = _feedback()
    feedback["evaluation_eligible"] = "0"
    record = forecast_feedback_to_record({"forecast": _forecast(), "feedback": feedback})
    assert record.evaluation_eligible is False


def test_exporter_reports_skip_reasons_and_validates_outcomes() -> None:
    valid = {"forecast": _forecast(), "feedback": _feedback()}
    missing_id = {
        "forecast": _forecast(forecast_id=""),
        "feedback": _feedback(),
    }
    invalid_probability = {
        "forecast": _forecast(probability_yes=1.5),
        "feedback": _feedback(),
    }
    missing_outcome = {
        "forecast": _forecast(),
        "feedback": _feedback(actual_outcome=""),
    }
    invalid_outcome = {
        "forecast": _forecast(),
        "feedback": _feedback(actual_outcome="MAYBE"),
    }
    malformed_forecast = _forecast()
    malformed_forecast["input_snapshot"] = "not-json"
    malformed_snapshot = {"forecast": malformed_forecast, "feedback": _feedback()}

    records, summary = export_pairs(
        [
            valid,
            missing_id,
            invalid_probability,
            missing_outcome,
            invalid_outcome,
            malformed_snapshot,
        ],
        page_limit=100,
    )

    assert len(records) == 1
    assert summary.exported == 1
    assert summary.skipped == 5
    assert summary.skipped_reasons == {
        "missing_forecast_id": 1,
        "invalid_probability_yes": 1,
        "missing_actual_outcome": 1,
        "validation_error": 1,
        "malformed_input_snapshot": 1,
    }


def test_exporter_allows_missing_timestamps_but_walk_forward_will_exclude_them() -> None:
    forecast = _forecast(forecasted_at=None, settlement_at="")
    forecast["target_definition"] = {}
    record = forecast_feedback_to_record(
        {
            "forecast": forecast,
            "feedback": {**_feedback(), "created_at": None},
        }
    )
    assert record.forecasted_at is None
    assert record.resolved_at is None


def test_exported_jsonl_round_trips_through_scorecard_loader(tmp_path: Path) -> None:
    records, _ = export_pairs([{"forecast": _forecast(), "feedback": _feedback()}])
    path = tmp_path / "resolved.jsonl"
    write_jsonl(records, path)

    loaded = load_jsonl(path)
    assert len(loaded) == 1
    assert loaded[0].forecast_id == "forecast-1"
    assert loaded[0].probability_yes == Decimal("0.62")


@pytest.mark.asyncio
async def test_sqlite_export_pages_and_reports_truncation(tmp_path: Path) -> None:
    store = SQLiteStore(db_path=str(tmp_path / "page.db"))
    store.initialize()
    await _seed(store, count=3)

    result = await export_records_from_sqlite(store, page_limit=2)
    assert result.exported == 3
    assert result.pages == 2
    assert result.truncated is False
    assert [record.forecast_id for record in result.records] == [
        "forecast-1",
        "forecast-2",
        "forecast-3",
    ]

    truncated = await export_records_from_sqlite(store, page_limit=2, max_pages=1)
    assert truncated.exported == 2
    assert truncated.truncated is True


@pytest.mark.asyncio
async def test_export_cli_writes_jsonl_and_summary(tmp_path: Path) -> None:
    db_path = tmp_path / "cli.db"
    store = SQLiteStore(db_path=str(db_path))
    store.initialize()
    await _seed(store, count=2)

    output = tmp_path / "resolved.jsonl"
    summary = tmp_path / "summary.json"
    assert (
        await anyio.to_thread.run_sync(
            lambda: export_main(
                [
                    "--sqlite-path",
                    str(db_path),
                    "--output",
                    str(output),
                    "--summary",
                    str(summary),
                ]
            )
        )
        == 0
    )

    assert len(output.read_text(encoding="utf-8").splitlines()) == 2
    payload = json.loads(summary.read_text(encoding="utf-8"))
    assert payload["exported"] == 2
    assert payload["truncated"] is False
