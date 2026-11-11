from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from prediction_ai.api.deps import get_db
from prediction_ai.learning.postmortem_agent import PostmortemResult
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
        with patch(
            "prediction_ai.learning.postmortem_agent.run_postmortem_analysis",
            new=AsyncMock(
                return_value=PostmortemResult(
                    was_correct=True,
                    brier_loss=0.0784,
                    discrepancy_classification="ACCURATE_CONVICTION",
                    postmortem_analysis=(
                        "Model recommendation BUY_YES aligned with market resolution YES."
                    ),
                    key_learnings=["Maintain baseline volatility scaling."],
                )
            ),
        ):
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


@pytest.mark.asyncio
async def test_sse_stream_includes_stage_in_payload():
    """Regression: SSE data payloads must carry ``stage`` so client progress bars track pipeline steps."""
    import json as json_lib

    from prediction_ai.db.sqlite import get_sqlite_db

    sqlite = get_sqlite_db()
    analysis_id = uuid4()
    await sqlite.create_analysis({
        "analysis_id": str(analysis_id),
        "status": "COMPLETED",  # terminal status ends the SSE loop after draining queued events
    })
    await sqlite.append_analysis_event(
        str(analysis_id),
        {
            "sequence_number": 1,
            "event_type": "analysis.stage",
            "stage": "DATA_COLLECTION",
            "payload": {"status": "in_progress", "message": "Ingesting real-time order book data"},
        },
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get(f"/api/v1/analyses/{analysis_id}/events")
        assert res.status_code == 200
        stage_frames = []
        for block in res.text.split("\n\n"):
            lines = [ln for ln in block.split("\n") if ln.startswith("data: ")]
            if any(ln.strip() == "event: analysis.stage" for ln in block.split("\n")) and lines:
                stage_frames.extend(json_lib.loads(ln[len("data: "):]) for ln in lines)
        assert stage_frames, "expected at least one analysis.stage SSE frame"
        for payload in stage_frames:
            assert payload["stage"] == "DATA_COLLECTION"
            assert payload["message"] == "Ingesting real-time order book data"


@pytest.mark.asyncio
async def test_feedback_abstention_is_not_scored_as_correct():
    from prediction_ai.db.sqlite import get_sqlite_db

    sqlite = get_sqlite_db()
    analysis_id = uuid4()
    await sqlite.create_analysis({
        "analysis_id": str(analysis_id),
        "status": "COMPLETED",
        "forecast": {
            "recommendation": "NO_TRADE",
            "probability_yes": 0.52,
            "market": {
                "title": "Will XRP close above $2.10 in 15 minutes?",
                "category": "crypto",
                "underlying_symbol": "XRP",
            },
            "explanation": {"reasoning": "Edge did not clear costs."},
        },
    })
    before = await sqlite.get_asset_calibration("XRP", "crypto")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/api/v1/analyses/{analysis_id}/feedback",
            json={"actual_outcome": "NO"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["was_correct"] is False
    assert body["evaluation_eligible"] is False
    assert body["discrepancy_classification"] == "ABSTENTION_NO_TRADE"

    after = await sqlite.get_asset_calibration("XRP", "crypto")
    assert after["sample_count"] == before["sample_count"]


@pytest.mark.asyncio
async def test_feedback_duplicate_submission_is_idempotent_and_conflict_is_rejected():
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
                "title": "Will DOGE close above $0.40 in 15 minutes?",
                "category": "crypto",
                "underlying_symbol": "DOGE",
            },
        },
    })

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.post(
            f"/api/v1/analyses/{analysis_id}/feedback",
            json={"actual_outcome": "YES"},
        )
        second = await client.post(
            f"/api/v1/analyses/{analysis_id}/feedback",
            json={"actual_outcome": "YES"},
        )
        conflict = await client.post(
            f"/api/v1/analyses/{analysis_id}/feedback",
            json={"actual_outcome": "NO"},
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["feedback_id"] == first.json()["feedback_id"]
    assert conflict.status_code == 409


@pytest.mark.asyncio
async def test_feedback_rejects_unrecognised_outcome():
    from prediction_ai.db.sqlite import get_sqlite_db

    sqlite = get_sqlite_db()
    analysis_id = uuid4()
    await sqlite.create_analysis({
        "analysis_id": str(analysis_id),
        "status": "COMPLETED",
        "forecast": {"recommendation": "NO_TRADE", "probability_yes": 0.50, "market": {}},
    })

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/api/v1/analyses/{analysis_id}/feedback",
            json={"actual_outcome": "MAYBE"},
        )

    assert response.status_code == 422
