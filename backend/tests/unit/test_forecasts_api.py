from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from prediction_ai.api.deps import get_current_user, get_db, get_mongo, get_sqlite
from prediction_ai.auth.jwt import AuthenticatedUser
from prediction_ai.db.sqlite import SQLiteStore
from prediction_ai.main import app
from prediction_ai.schemas.market import NormalizedOutcome

FORECASTED_AT = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
RESOLVES_AT = FORECASTED_AT + timedelta(minutes=15)


def _contract_spec() -> dict:
    return {
        "platform": "polymarket",
        "platform_market_id": "condition-1",
        "title": "Will BTC be up or down in the next 15 minutes?",
        "category": "crypto",
        "market_type": "binary",
        "asset": "BTC",
        "direction": "up",
        "outcomes": [
            NormalizedOutcome(
                outcome_id="down-id", token_id="down-token", label="Down"
            ).model_dump(mode="json"),
            NormalizedOutcome(
                outcome_id="up-id", token_id="up-token", label="Up"
            ).model_dump(mode="json"),
        ],
        "affirmative_outcome_id": "up-id",
        "negative_outcome_id": "down-id",
        "affirmative_token_id": "up-token",
        "negative_token_id": "down-token",
        "strike_value": "74000",
        "strike_source": "start_price",
        "settlement_source": "chainlink_data_stream",
        "settlement_timestamp": RESOLVES_AT.isoformat(),
        "comparator": "gt",
        "spec_hash": "spec-hash",
        "tradeable": True,
    }


def _forecast_doc(forecast_id: str, *, contract_spec: dict) -> dict:
    return {
        "id": forecast_id,
        "analysis_id": str(uuid4()),
        "user_id": "00000000-0000-0000-0000-000000000001",
        "market_id": "condition-1",
        "forecasted_at": FORECASTED_AT.isoformat(),
        "forecast_cutoff_at": FORECASTED_AT.isoformat(),
        "seconds_to_target": 900.0,
        "target_definition": {
            "question": "Will BTC be up or down in the next 15 minutes?",
            "outcomes": ["Up", "Down"],
        },
        "probability_yes": 0.62,
        "probability_no": 0.38,
        "uncertainty_interval": [0.54, 0.70],
        "recommendation": "BUY_YES",
        "decision_metrics": {
            "minimum_required_edge": "0.06",
            "spread": "0.01",
            "slippage_bps": 15,
            "quarter_kelly_fraction": "0.12",
        },
        "explanation": {"reasoning": "Test snapshot."},
        "evidence": [],
        "specialist_context": {},
        "market": {
            "title": "Will BTC be up or down in the next 15 minutes?",
            "platform": "polymarket",
            "platform_market_id": "condition-1",
            "category": "crypto",
            "market_type": "binary",
            "underlying_symbol": "BTC",
        },
        "model_version": "model-v1",
        "calibration_version": "unfitted_linear_normalized_v2",
        "prompt_version": "prompt-v1",
        "decision_policy_version": "decision_policy_v1",
        "contract_spec": contract_spec,
        "input_snapshot": {"execution": {"quote_age_seconds": "1.2"}},
        "feature_snapshot": {
            "crypto_research": {"spot_price": "74000", "dispersion_bps": "14.45"}
        },
        "model_components": {
            "llm_probability": 0.55,
            "math_probability": 0.60,
            "blend_weights": {"math": 0.4, "llm": 0.1, "market": 0.5},
        },
        "data_quality": {"flags": []},
        "timing_provenance": {"exact_resolves_at": RESOLVES_AT.isoformat()},
        "created_at": datetime.now(UTC).isoformat(),
    }


async def _request_forecast(store: SQLiteStore, forecast_id: str):
    app.dependency_overrides[get_sqlite] = lambda: store
    app.dependency_overrides[get_mongo] = lambda: MagicMock()
    app.dependency_overrides[get_db] = lambda: None
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        id=uuid4(), tier="standard"
    )
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get(f"/api/v1/forecasts/{forecast_id}")
    finally:
        for dependency in (get_sqlite, get_mongo, get_db, get_current_user):
            app.dependency_overrides.pop(dependency, None)


@pytest.mark.asyncio
async def test_forecast_response_exposes_immutable_snapshots(tmp_path: Path) -> None:
    store = SQLiteStore(db_path=str(tmp_path / "forecasts.db"))
    store.initialize()
    forecast_id = str(uuid4())
    await store.save_forecast(
        _forecast_doc(forecast_id, contract_spec=_contract_spec())
    )

    response = await _request_forecast(store, forecast_id)

    assert response.status_code == 200
    body = response.json()
    assert body["contract_spec"]["spec_hash"] == "spec-hash"
    assert body["contract_spec"]["direction"] == "up"
    assert body["contract_spec"]["strike_value"] == "74000"
    assert body["feature_snapshot"]["crypto_research"]["dispersion_bps"] == "14.45"
    assert body["model_components"]["math_probability"] == 0.6
    assert body["input_snapshot"]["execution"]["quote_age_seconds"] == "1.2"


@pytest.mark.asyncio
async def test_malformed_contract_spec_does_not_break_forecast_read(
    tmp_path: Path,
) -> None:
    store = SQLiteStore(db_path=str(tmp_path / "malformed.db"))
    store.initialize()
    forecast_id = str(uuid4())
    await store.save_forecast(
        _forecast_doc(forecast_id, contract_spec={"platform": "polymarket"})
    )

    response = await _request_forecast(store, forecast_id)

    assert response.status_code == 200
    body = response.json()
    assert body["contract_spec"] is None
    assert Decimal(body["probability_yes"]) == Decimal("0.62")
