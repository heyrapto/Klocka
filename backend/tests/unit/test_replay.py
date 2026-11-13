from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from prediction_ai.db.sqlite import SQLiteStore
from prediction_ai.evaluation.replay import (
    ReplayDefaults,
    ReplayPolicy,
    build_replay_report,
    pair_to_replay_sample,
    replay_decision,
)

_T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def _store(tmp_path: Path) -> SQLiteStore:
    store = SQLiteStore(str(tmp_path / "replay.db"))
    store.initialize()
    return store


async def _save_pair(
    store: SQLiteStore,
    *,
    forecast_id: str,
    analysis_id: str,
    forecasted_at: datetime,
    settlement_at: datetime,
    actual_outcome: str,
    probability_yes: str,
    recommendation: str,
    ask_yes: str = "0.50",
    ask_no: str = "0.50",
    cost_yes: str | None = None,
    cost_no: str | None = None,
    spread: str = "0.00",
    required_edge: str = "0.06",
    quote_age: str | None = None,
    max_quote_age: str | None = None,
    depth_known: bool = True,
    resolution_flags: list[str] | None = None,
    blocking_flags: list[str] | None = None,
    components: dict[str, Any] | None = None,
    decision_policy: dict[str, Any] | None = None,
    include_decision_metrics: bool = True,
    category: str = "crypto",
) -> None:
    market = {
        "platform": "polymarket",
        "platform_market_id": f"market-{forecast_id}",
        "title": "Bitcoin Up or Down - 15 minutes?",
        "category": category,
        "market_type": "binary",
        "outcomes": [
            {"outcome_id": "yes", "token_id": f"{forecast_id}-yes", "label": "Up"},
            {"outcome_id": "no", "token_id": f"{forecast_id}-no", "label": "Down"},
        ],
        "resolves_at": settlement_at.isoformat(),
        "status": "resolved",
    }
    decision_metrics: dict[str, Any] = {}
    if include_decision_metrics:
        decision_metrics = {
            "executable_ask_yes": ask_yes,
            "executable_ask_no": ask_no,
            "effective_cost_yes": cost_yes,
            "effective_cost_no": cost_no,
            "spread": spread,
            "slippage_bps": 15,
            "quarter_kelly_fraction": "0.0",
            "taker_fee_rate_yes": "0.01",
            "taker_fee_rate_no": "0.01",
            "depth_ask_usd_yes": "1000.0",
            "depth_ask_usd_no": "1000.0",
            "target_order_usd": "100.0",
            "minimum_depth_usd": "100.0",
            "fill_complete_yes": True,
            "fill_complete_no": True,
            "depth_known": depth_known,
            "quote_age_seconds": quote_age,
            "max_quote_age_seconds": max_quote_age,
            "fee_model": "variance",
            "minimum_required_edge": required_edge,
            "resolution_risk_flags": resolution_flags or [],
            "blocking_resolution_flags": blocking_flags or [],
        }

    await store.save_forecast(
        {
            "id": forecast_id,
            "forecast_id": forecast_id,
            "analysis_id": analysis_id,
            "user_id": "user-1",
            "market_id": f"market-{forecast_id}",
            "probability_yes": probability_yes,
            "probability_no": str(Decimal("1") - Decimal(probability_yes)),
            "recommendation": recommendation,
            "forecasted_at": forecasted_at.isoformat(),
            "market": market,
            "decision_metrics": decision_metrics,
            "contract_spec": {"affirmative_outcome_id": "yes"},
            "target_definition": {"settlement_timestamp": settlement_at.isoformat()},
            "timing_provenance": {"exact_resolves_at": settlement_at.isoformat()},
            "feature_snapshot": {
                "decision_gate_inputs": {"effective_uncertainty_margin": "0.02"},
                "decision_policy": decision_policy or {},
            },
            "model_components": components or {},
            "explanation": {"decision_reason": "test decision"},
        }
    )
    await store.record_feedback(
        {
            "id": f"fb-{analysis_id}",
            "analysis_id": analysis_id,
            "user_id": "user-1",
            "category": category,
            "market_title": market["title"],
            "recommendation": recommendation,
            "actual_outcome": actual_outcome,
            "was_correct": True,
            "evaluation_eligible": True,
            "probability_yes": probability_yes,
            "created_at": (settlement_at + timedelta(hours=1)).isoformat(),
        }
    )


def _defaults() -> ReplayDefaults:
    return ReplayDefaults(
        required_edge=Decimal("0.06"),
        max_spread=Decimal("0.05"),
        max_quote_age_seconds=Decimal("60"),
        min_depth_usd=Decimal("100"),
        target_order_usd=Decimal("100"),
    )


@pytest.mark.asyncio
async def test_pair_to_replay_sample_uses_stored_execution_snapshot(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    await _save_pair(
        store,
        forecast_id="forecast-1",
        analysis_id="analysis-1",
        forecasted_at=_T0,
        settlement_at=_T0 + timedelta(minutes=15),
        actual_outcome="UP",
        probability_yes="0.60",
        recommendation="BUY_YES",
        ask_yes="0.50",
        ask_no="0.54",
        cost_yes="0.505",
        cost_no="0.545",
        spread="0.04",
        required_edge="0.06",
        quote_age="3.5",
        max_quote_age="60",
    )

    pairs = await store.list_resolved_forecast_feedback(limit=10)
    sample = pair_to_replay_sample(pairs[0])

    assert sample.forecast_id == "forecast-1"
    assert sample.probability_yes == Decimal("0.60")
    assert sample.stored_required_edge == Decimal("0.06")
    assert sample.stored_uncertainty_margin == Decimal("0.02")
    assert sample.stored_min_edge == Decimal("0.04")
    assert sample.ask_yes == Decimal("0.50")
    assert sample.ask_no == Decimal("0.54")
    assert sample.spread == Decimal("0.04")
    assert sample.quote_age_seconds == Decimal("3.5")
    assert sample.stored_max_quote_age_seconds == Decimal("60")
    assert sample.outcome_yes == 1
    assert sample.stored_recommendation == "BUY_YES"


@pytest.mark.asyncio
async def test_baseline_replay_reconstructs_stored_recommendations(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    await _save_pair(
        store,
        forecast_id="forecast-a",
        analysis_id="analysis-a",
        forecasted_at=_T0,
        settlement_at=_T0 + timedelta(minutes=15),
        actual_outcome="UP",
        probability_yes="0.60",
        recommendation="BUY_YES",
        ask_yes="0.50",
        ask_no="0.50",
        cost_yes="0.505",
        cost_no="0.505",
        required_edge="0.06",
    )
    await _save_pair(
        store,
        forecast_id="forecast-b",
        analysis_id="analysis-b",
        forecasted_at=_T0 + timedelta(hours=1),
        settlement_at=_T0 + timedelta(hours=1, minutes=15),
        actual_outcome="DOWN",
        probability_yes="0.40",
        recommendation="BUY_NO",
        ask_yes="0.50",
        ask_no="0.50",
        cost_yes="0.505",
        cost_no="0.505",
        required_edge="0.06",
    )
    await _save_pair(
        store,
        forecast_id="forecast-c",
        analysis_id="analysis-c",
        forecasted_at=_T0 + timedelta(hours=2),
        settlement_at=_T0 + timedelta(hours=2, minutes=15),
        actual_outcome="UP",
        probability_yes="0.50",
        recommendation="NO_TRADE",
        ask_yes="0.50",
        ask_no="0.50",
        cost_yes="0.505",
        cost_no="0.505",
        required_edge="0.06",
    )

    report = await build_replay_report(store, category="crypto", defaults=_defaults())

    assert report.samples == 3
    assert report.reconstruction_matches == 3
    assert report.reconstruction_mismatches == 0
    assert report.mismatches == []
    assert report.baseline is not None
    assert report.baseline.trade_recommendations == 2
    assert report.baseline.trade_coverage == Decimal("0.6666666666666666666666666667")
    assert report.baseline.win_rate == Decimal("1")
    assert report.baseline.losing_trades == 0
    assert report.baseline.total_realized_pnl_per_share > 0
    assert report.baseline.recommendation_counts == {
        "BUY_NO": 1,
        "BUY_YES": 1,
        "NO_TRADE": 1,
    }


@pytest.mark.asyncio
async def test_threshold_sweep_changes_trade_count_and_win_rate(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    await _save_pair(
        store,
        forecast_id="forecast-threshold",
        analysis_id="analysis-threshold",
        forecasted_at=_T0,
        settlement_at=_T0 + timedelta(minutes=15),
        actual_outcome="UP",
        probability_yes="0.55",
        recommendation="NO_TRADE",
        ask_yes="0.50",
        ask_no="0.50",
        cost_yes="0.505",
        cost_no="0.505",
        required_edge="0.06",
    )

    report = await build_replay_report(
        store,
        category="crypto",
        thresholds=(Decimal("0.04"), Decimal("0.06")),
        defaults=_defaults(),
    )

    by_threshold = {result.required_edge: result for result in report.threshold_sweep}
    loose = by_threshold[Decimal("0.04")]
    strict = by_threshold[Decimal("0.06")]

    assert loose.trade_recommendations == 1
    assert loose.win_rate == Decimal("1")
    assert loose.total_realized_pnl_per_share > 0
    assert strict.trade_recommendations == 0
    assert strict.no_trade_count == 1
    assert strict.gate_counts == {"INSUFFICIENT_EDGE": 1}


@pytest.mark.asyncio
async def test_replay_policy_can_force_spread_and_quote_age_abstention(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    await _save_pair(
        store,
        forecast_id="forecast-gates",
        analysis_id="analysis-gates",
        forecasted_at=_T0,
        settlement_at=_T0 + timedelta(minutes=15),
        actual_outcome="UP",
        probability_yes="0.60",
        recommendation="BUY_YES",
        ask_yes="0.50",
        ask_no="0.54",
        cost_yes="0.505",
        cost_no="0.545",
        spread="0.04",
        required_edge="0.06",
        quote_age="30",
        max_quote_age="60",
    )

    pairs = await store.list_resolved_forecast_feedback(limit=10)
    sample = pair_to_replay_sample(pairs[0])

    spread_blocked = replay_decision(
        sample,
        ReplayPolicy(name="tight-spread", max_spread=Decimal("0.02")),
        _defaults(),
    )
    assert spread_blocked.recommendation == "NO_TRADE"
    assert "EXCESSIVE_SPREAD" in spread_blocked.gating_flags

    age_blocked = replay_decision(
        sample,
        ReplayPolicy(name="tight-age", max_quote_age_seconds=Decimal("15")),
        _defaults(),
    )
    assert age_blocked.recommendation == "NO_TRADE"
    assert "STALE_QUOTE" in age_blocked.gating_flags

    allowed = replay_decision(sample, ReplayPolicy(name="baseline"), _defaults())
    assert allowed.recommendation == "BUY_YES"


@pytest.mark.asyncio
async def test_blend_sweep_scores_component_variants(tmp_path: Path) -> None:
    store = _store(tmp_path)
    outcomes = ("UP", "DOWN", "UP", "DOWN")
    for index, outcome in enumerate(outcomes):
        up = outcome == "UP"
        await _save_pair(
            store,
            forecast_id=f"forecast-blend-{index}",
            analysis_id=f"analysis-blend-{index}",
            forecasted_at=_T0 + timedelta(hours=index),
            settlement_at=_T0 + timedelta(hours=index, minutes=15),
            actual_outcome=outcome,
            probability_yes="0.50",
            recommendation="NO_TRADE",
            components={
                "math_probability": 0.9 if up else 0.1,
                "llm_probability": 0.1 if up else 0.9,
                "market_implied_probability": 0.5,
            },
        )

    report = await build_replay_report(store, category="crypto", defaults=_defaults())

    assert report.component_coverage == Decimal("1")
    assert report.full_component_coverage == Decimal("1")
    by_name = {variant.name: variant for variant in report.blend_sweep}

    assert by_name["stored_probability"].brier == Decimal("0.25")
    assert by_name["math_only"].samples == 4
    assert by_name["math_only"].brier == Decimal("0.01")
    assert by_name["math_only"].bss_vs_market == Decimal("0.96")
    assert by_name["llm_only"].brier == Decimal("0.81")
    assert by_name["llm_only"].bss_vs_market == Decimal("-2.24")
    assert by_name["market_only"].brier == Decimal("0.25")
    assert by_name["market_only"].bss_vs_market == Decimal("0")


@pytest.mark.asyncio
async def test_blend_sweep_reports_missing_components_without_failing(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    await _save_pair(
        store,
        forecast_id="forecast-legacy",
        analysis_id="analysis-legacy",
        forecasted_at=_T0,
        settlement_at=_T0 + timedelta(minutes=15),
        actual_outcome="UP",
        probability_yes="0.60",
        recommendation="BUY_YES",
        cost_yes="0.505",
        required_edge="0.06",
    )

    report = await build_replay_report(store, category="crypto", defaults=_defaults())

    assert report.component_coverage == Decimal("0")
    assert report.full_component_coverage == Decimal("0")
    by_name = {variant.name: variant for variant in report.blend_sweep}
    assert by_name["stored_probability"].samples == 1
    assert by_name["math_only"].samples == 0
    assert by_name["math_only"].brier is None
    assert any("No stored model components" in note for note in report.notes)


@pytest.mark.asyncio
async def test_replay_skips_pairs_without_decision_metrics(tmp_path: Path) -> None:
    store = _store(tmp_path)
    await _save_pair(
        store,
        forecast_id="forecast-no-metrics",
        analysis_id="analysis-no-metrics",
        forecasted_at=_T0,
        settlement_at=_T0 + timedelta(minutes=15),
        actual_outcome="UP",
        probability_yes="0.60",
        recommendation="BUY_YES",
        include_decision_metrics=False,
    )

    report = await build_replay_report(store, category="crypto", defaults=_defaults())

    assert report.samples == 0
    assert report.skipped == 1
    assert report.skipped_reasons == {"missing_decision_metrics": 1}


@pytest.mark.asyncio
async def test_replay_report_is_bounded_and_reports_truncation(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    for index in range(3):
        await _save_pair(
            store,
            forecast_id=f"forecast-page-{index}",
            analysis_id=f"analysis-page-{index}",
            forecasted_at=_T0 + timedelta(hours=index),
            settlement_at=_T0 + timedelta(hours=index, minutes=15),
            actual_outcome="UP",
            probability_yes="0.60",
            recommendation="BUY_YES",
            cost_yes="0.505",
            required_edge="0.06",
        )

    report = await build_replay_report(
        store,
        category="crypto",
        page_limit=2,
        max_pages=1,
        defaults=_defaults(),
    )

    assert report.pages == 1
    assert report.samples == 2
    assert report.truncated is True
    assert report.baseline is not None
    assert report.baseline.samples == 2
