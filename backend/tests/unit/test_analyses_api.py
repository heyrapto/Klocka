from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from prediction_ai.api.deps import get_db
from prediction_ai.main import app


@pytest.mark.asyncio
async def test_get_unknown_analysis_returns_404():
    mock_db = MagicMock()
    mock_db.get = AsyncMock(return_value=None)

    app.dependency_overrides[get_db] = lambda: mock_db
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            random_id = uuid4()
            response = await client.get(f"/api/v1/analyses/{random_id}")
            assert response.status_code == 404
            assert response.json()["detail"] == "Analysis not found"
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.mark.asyncio
async def test_create_analysis_with_hint_success():
    mock_db = MagicMock()
    mock_db.get = AsyncMock(return_value=None)
    mock_db.add = MagicMock()
    mock_db.flush = AsyncMock()
    mock_db.commit = AsyncMock()

    app.dependency_overrides[get_db] = lambda: mock_db
    try:
        with patch(
            "prediction_ai.orchestration.runner.AnalysisWorkflowRunner.run",
            new_callable=AsyncMock,
        ) as mock_run:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/api/v1/analyses",
                    data={"hint": "https://polymarket.com/market/btc-100k"},
                )
                assert response.status_code == 202
                body = response.json()
                assert "analysis_id" in body
                assert body["status"] == "RECEIVED"
                assert body["status_url"].startswith("/api/v1/analyses/")
                assert body["events_url"].endswith("/events")
                assert mock_run.called
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.mark.asyncio
async def test_feedback_learning_loop():
    from prediction_ai.db.sqlite import get_sqlite_db

    sqlite = get_sqlite_db()
    analysis_id = uuid4()
    await sqlite.create_analysis({
        "analysis_id": str(analysis_id),
        "status": "COMPLETED",
        "forecast": {
            "recommendation": "BUY_YES",
            "probability_yes": 0.72,
            "market": {
                "title": "Will Bitcoin break $100k in next 15 mins?",
                "category": "crypto",
            },
            "explanation": {
                "reasoning": "Strong 15m RSI momentum and bid volume imbalance.",
            },
        },
    })

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Submit successful outcome feedback
        res = await client.post(
            f"/api/v1/analyses/{analysis_id}/feedback",
            json={
                "actual_outcome": "YES",
                "notes": "Target hit 8 minutes into the candle.",
                "settled_price": 1.0,
            },
        )
        assert res.status_code == 200
        data = res.json()
        assert data["actual_outcome"] == "YES"
        assert data["was_correct"] is True
        assert "aligned with market resolution" in data["postmortem_analysis"]
        assert len(data["key_learnings"]) > 0

        # Verify learnings were recorded in SQLite feedback collection
        learnings = await sqlite.get_recent_learnings(category="crypto", limit=5)
        assert len(learnings) >= 1
        assert learnings[0]["was_correct"] is True
