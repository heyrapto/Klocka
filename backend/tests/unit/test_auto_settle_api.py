from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from prediction_ai.api.deps import get_sqlite
from prediction_ai.db.sqlite import SQLiteStore
from prediction_ai.learning.postmortem_agent import PostmortemResult
from prediction_ai.main import app


def _analysis_doc(analysis_id: UUID, *, resolves_at: datetime) -> dict[str, Any]:
    return {
        "analysis_id": str(analysis_id),
        "user_id": "00000000-0000-0000-0000-000000000001",
        "status": "COMPLETED",
        "extraction_result": {
            "target_price": 100_000.0,
            "outcomes": [{"label": "UP"}, {"label": "DOWN"}],
        },
        "forecast": {
            "recommendation": "BUY_UP",
            "probability_yes": 0.62,
            "market": {
                "title": "BTC above 100k in 15m",
                "category": "crypto",
                "underlying_symbol": "BTC",
            },
            "timing_provenance": {"exact_resolves_at": resolves_at.isoformat()},
        },
        "specialist_context": {"crypto_research": {"strike": 100_000.0, "direction": "above"}},
    }


@pytest.mark.asyncio
async def test_auto_settle_rejects_unresolved_contract(tmp_path: Path) -> None:
    store = SQLiteStore(db_path=str(tmp_path / "settle.db"))
    store.initialize()
    analysis_id = uuid4()
    await store.create_analysis(
        _analysis_doc(analysis_id, resolves_at=datetime.now(UTC) + timedelta(minutes=10))
    )

    app.dependency_overrides[get_sqlite] = lambda: store
    try:
        with patch(
            "prediction_ai.specialists.crypto.settlement.fetch_crypto_settlement_price",
            new_callable=AsyncMock,
        ) as fetch_price:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(f"/api/v1/analyses/{analysis_id}/auto-settle")

        assert response.status_code == 409
        assert "before the contract resolves" in response.json()["detail"]
        fetch_price.assert_not_awaited()
    finally:
        app.dependency_overrides.pop(get_sqlite, None)


@pytest.mark.asyncio
async def test_auto_settle_records_feedback_after_resolution(tmp_path: Path) -> None:
    store = SQLiteStore(db_path=str(tmp_path / "settle.db"))
    store.initialize()
    analysis_id = uuid4()
    await store.create_analysis(
        _analysis_doc(analysis_id, resolves_at=datetime.now(UTC) - timedelta(minutes=5))
    )

    postmortem = PostmortemResult(
        was_correct=True,
        brier_loss=0.1444,
        discrepancy_classification="ACCURATE_CONVICTION",
        postmortem_analysis="Settlement aligned with the model probability.",
        key_learnings=["Maintain baseline volatility scaling."],
    )

    app.dependency_overrides[get_sqlite] = lambda: store
    try:
        with (
            patch(
                "prediction_ai.specialists.crypto.settlement.fetch_crypto_settlement_price",
                new=AsyncMock(return_value=101_250.0),
            ),
            patch(
                "prediction_ai.learning.postmortem_agent.run_postmortem_analysis",
                new=AsyncMock(return_value=postmortem),
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(f"/api/v1/analyses/{analysis_id}/auto-settle")

        assert response.status_code == 200
        body = response.json()
        assert body["actual_outcome"] == "UP"
        assert body["was_correct"] is True

        feedback = await store.get_recent_learnings(category="crypto", limit=5)
        assert len(feedback) == 1
        assert feedback[0]["settled_price"] == 101_250.0
        assert feedback[0]["actual_outcome"] == "UP"
    finally:
        app.dependency_overrides.pop(get_sqlite, None)


@pytest.mark.asyncio
async def test_auto_settle_is_idempotent_after_feedback_exists(tmp_path: Path) -> None:
    store = SQLiteStore(db_path=str(tmp_path / "idempotent.db"))
    store.initialize()
    analysis_id = uuid4()
    await store.create_analysis(
        _analysis_doc(analysis_id, resolves_at=datetime.now(UTC) - timedelta(minutes=5))
    )
    await store.record_feedback(
        {
            "feedback_id": "00000000-0000-0000-0000-0000000000aa",
            "analysis_id": str(analysis_id),
            "recommendation": "BUY_UP",
            "actual_outcome": "UP",
            "was_correct": True,
            "evaluation_eligible": True,
            "probability_yes": 0.62,
            "postmortem_analysis": "Already settled.",
            "key_learnings": ["Keep the existing record."],
            "created_at": datetime.now(UTC).isoformat(),
        }
    )

    app.dependency_overrides[get_sqlite] = lambda: store
    try:
        with patch(
            "prediction_ai.specialists.crypto.settlement.fetch_crypto_settlement_price",
            new_callable=AsyncMock,
        ) as fetch_price:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(f"/api/v1/analyses/{analysis_id}/auto-settle")

        assert response.status_code == 200
        assert response.json()["feedback_id"] == "00000000-0000-0000-0000-0000000000aa"
        fetch_price.assert_not_awaited()
    finally:
        app.dependency_overrides.pop(get_sqlite, None)
