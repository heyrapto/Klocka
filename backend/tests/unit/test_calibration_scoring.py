from __future__ import annotations

from pathlib import Path

import pytest

from prediction_ai.core.config import Settings
from prediction_ai.db.sqlite import SQLiteStore
from prediction_ai.learning.postmortem_agent import run_postmortem_analysis


def _test_settings() -> Settings:
    # No GEMINI_API_KEY -> the deterministic heuristic path is exercised.
    return Settings(ENVIRONMENT="test")


@pytest.mark.asyncio
async def test_abstention_updates_brier_but_not_win_rate(tmp_path: Path) -> None:
    """An abstention is a probability sample, not an executed trade."""
    store = SQLiteStore(str(tmp_path / "calibration.db"))
    store.initialize()

    await store.update_asset_calibration(
        symbol="BTC",
        category="crypto",
        was_correct=True,
        brier_loss=0.04,
        was_trade=False,
    )
    calibration = await store.get_asset_calibration("BTC")

    assert calibration["sample_count"] == 0
    assert calibration["correct_count"] == 0
    assert calibration["brier_score"] == pytest.approx(0.04, abs=1e-6)

    await store.update_asset_calibration(
        symbol="BTC",
        category="crypto",
        was_correct=True,
        brier_loss=0.01,
        was_trade=True,
    )
    calibration = await store.get_asset_calibration("BTC")

    assert calibration["sample_count"] == 1
    assert calibration["correct_count"] == 1


@pytest.mark.asyncio
async def test_abstention_keeps_its_lean_for_diagnostics() -> None:
    result = await run_postmortem_analysis(
        market_title="Bitcoin Up or Down - 15 minutes?",
        category="crypto",
        symbol="BTC",
        recommendation="NO_TRADE",
        probability_yes=0.70,
        actual_outcome="UP",
        settings=_test_settings(),
    )

    # The lean is recorded so postmortems can distinguish a missed edge from a
    # disciplined save, but it is not an executed trade (see calibration test).
    assert result.was_correct is True
    assert result.evaluation_eligible is True
    assert result.discrepancy_classification == "ABSTENTION_ALIGNED_LEAN"
