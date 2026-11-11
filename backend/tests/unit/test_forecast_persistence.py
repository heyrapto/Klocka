import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from prediction_ai.db.sqlite import SQLiteStore


def _forecast_doc() -> dict:
    return {
        "id": "forecast-1",
        "analysis_id": "analysis-1",
        "user_id": "user-1",
        "market_id": "market-1",
        "forecasted_at": "2026-11-10T18:00:00+00:00",
        "forecast_cutoff_at": "2026-11-10T18:00:00+00:00",
        "seconds_to_target": 900.0,
        "target_definition": {"question": "BTC up or down?", "outcomes": ["Up", "Down"]},
        "probability_yes": 0.62,
        "probability_no": 0.38,
        "uncertainty_interval": [0.52, 0.72],
        "recommendation": "BUY_YES",
        "decision_metrics": {"estimated_net_edge_yes": 0.05},
        "explanation": {"reasoning": "test"},
        "evidence": [],
        "specialist_context": {},
        "market": {"title": "BTC up or down?"},
        "model_version": "model-v1",
        "calibration_version": "unfitted_linear_normalized_v2",
        "prompt_version": "prompt-v1",
        "decision_policy_version": "decision_policy_v1",
        "contract_spec": {
            "platform": "polymarket",
            "platform_market_id": "market-1",
            "spec_hash": "abc123",
        },
        "input_snapshot": {"llm_probability": 0.55, "execution": {"quote_age_seconds": "1.2"}},
        "feature_snapshot": {"crypto_research": {"spot_price": "74000"}},
        "model_components": {"llm_probability": 0.55, "math_probability": 0.6},
        "data_quality": {"flags": []},
        "timing_provenance": {"exact_resolves_at": "2026-11-10T18:15:00+00:00"},
        "created_at": datetime.now(UTC).isoformat(),
    }


@pytest.mark.asyncio
async def test_forecast_snapshot_round_trip(tmp_path: Path) -> None:
    store = SQLiteStore(db_path=str(tmp_path / "forecasts.db"))
    store.initialize()

    await store.save_forecast(_forecast_doc())
    stored = await store.get_forecast("forecast-1")

    assert stored is not None
    assert stored["contract_spec"]["spec_hash"] == "abc123"
    assert stored["input_snapshot"]["execution"]["quote_age_seconds"] == "1.2"
    assert stored["feature_snapshot"]["crypto_research"]["spot_price"] == "74000"
    assert stored["model_components"]["math_probability"] == 0.6
    assert stored["seconds_to_target"] == 900.0
    assert stored["calibration_version"] == "unfitted_linear_normalized_v2"
    assert stored["timing_provenance"]["exact_resolves_at"].startswith("2026-11-10T18:15")


@pytest.mark.asyncio
async def test_initialize_migrates_legacy_forecast_table(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.db"
    connection = sqlite3.connect(db_path)
    connection.executescript(
        """
        CREATE TABLE forecasts (
            forecast_id TEXT PRIMARY KEY,
            analysis_id TEXT NOT NULL,
            user_id TEXT,
            market_id TEXT,
            probability_yes REAL NOT NULL,
            probability_no REAL NOT NULL,
            uncertainty_interval TEXT,
            recommendation TEXT NOT NULL,
            decision_metrics TEXT,
            explanation TEXT,
            evidence TEXT,
            specialist_context TEXT,
            image_description TEXT,
            market_data TEXT,
            created_at TEXT NOT NULL
        );
        """
    )
    connection.close()

    store = SQLiteStore(db_path=str(db_path))
    store.initialize()
    await store.save_forecast(_forecast_doc())
    stored = await store.get_forecast("forecast-1")

    assert stored is not None
    assert stored["contract_spec"]["spec_hash"] == "abc123"


@pytest.mark.asyncio
async def test_feedback_abstention_and_duplicate_guard(tmp_path: Path) -> None:
    store = SQLiteStore(db_path=str(tmp_path / "feedback.db"))
    store.initialize()

    feedback = {
        "feedback_id": "feedback-1",
        "analysis_id": "analysis-1",
        "recommendation": "NO_TRADE",
        "actual_outcome": "YES",
        "was_correct": False,
        "evaluation_eligible": False,
        "probability_yes": 0.55,
        "key_learnings": ["Preserve abstention."],
        "created_at": datetime.now(UTC).isoformat(),
    }
    await store.record_feedback(feedback)

    stored = await store.get_feedback_by_analysis("analysis-1")
    assert stored is not None
    assert stored["was_correct"] is False
    assert stored["evaluation_eligible"] is False

    with pytest.raises(sqlite3.IntegrityError):
        await store.record_feedback({**feedback, "feedback_id": "feedback-2"})
