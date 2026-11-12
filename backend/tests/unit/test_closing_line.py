from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from prediction_ai.db.sqlite import SQLiteStore
from prediction_ai.evaluation.closing_line import build_closing_line_report
from prediction_ai.schemas.price_history import OutcomePriceHistory, PricePoint

_T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def _store(tmp_path: Path) -> SQLiteStore:
    store = SQLiteStore(str(tmp_path / "closing.db"))
    store.initialize()
    return store


async def _save_pair(
    store: SQLiteStore,
    *,
    forecast_id: str,
    analysis_id: str,
    market_id: str,
    forecasted_at: datetime,
    settlement_at: datetime,
    actual_outcome: str,
    probability_yes: float,
    recommendation: str,
    market_implied: float | None = None,
    cost_yes: str | None = None,
    cost_no: str | None = None,
) -> None:
    market = {
        "platform": "polymarket",
        "platform_market_id": market_id,
        "title": "Bitcoin Up or Down - 15 minutes?",
        "category": "crypto",
        "market_type": "binary",
        "outcomes": [
            {"outcome_id": "yes", "token_id": f"{market_id}-yes", "label": "Up"},
            {"outcome_id": "no", "token_id": f"{market_id}-no", "label": "Down"},
        ],
        "resolves_at": settlement_at.isoformat(),
        "status": "resolved",
    }
    await store.save_forecast(
        {
            "id": forecast_id,
            "forecast_id": forecast_id,
            "analysis_id": analysis_id,
            "user_id": "user-1",
            "market_id": market_id,
            "probability_yes": probability_yes,
            "probability_no": 1.0 - probability_yes,
            "recommendation": recommendation,
            "forecasted_at": forecasted_at.isoformat(),
            "market": market,
            "contract_spec": {"affirmative_outcome_id": "yes"},
            "target_definition": {
                "settlement_timestamp": settlement_at.isoformat(),
            },
            "timing_provenance": {"exact_resolves_at": settlement_at.isoformat()},
            "input_snapshot": (
                {"market_implied": market_implied}
                if market_implied is not None
                else {}
            ),
            "feature_snapshot": {
                "market_microstructure": {
                    "observed_at": (
                        forecasted_at - timedelta(seconds=45)
                    ).isoformat()
                }
            },
            "decision_metrics": {
                "effective_cost_yes": cost_yes,
                "effective_cost_no": cost_no,
            },
        }
    )
    await store.record_feedback(
        {
            "id": f"fb-{analysis_id}",
            "analysis_id": analysis_id,
            "user_id": "user-1",
            "category": "crypto",
            "market_title": market["title"],
            "recommendation": recommendation,
            "actual_outcome": actual_outcome,
            "was_correct": True,
            "evaluation_eligible": True,
            "probability_yes": probability_yes,
            "created_at": (settlement_at + timedelta(hours=1)).isoformat(),
        }
    )


async def _save_prices(
    store: SQLiteStore,
    *,
    market_id: str,
    outcome_id: str,
    prices: list[tuple[datetime, str]],
) -> None:
    await store.record_price_history(
        OutcomePriceHistory(
            platform="polymarket",
            platform_market_id=market_id,
            outcome_id=outcome_id,
            token_id=f"{market_id}-{outcome_id}",
            label="Up" if outcome_id == "yes" else "Down",
            granularity_seconds=60,
            points=[
                PricePoint(
                    observed_at=observed_at,
                    price=Decimal(price),
                    source="polymarket_clob_prices_history",
                )
                for observed_at, price in prices
            ],
        )
    )


@pytest.mark.asyncio
async def test_report_uses_entry_snapshot_and_pre_settlement_closing_price(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    forecast_a = _T0
    settlement_a = _T0 + timedelta(minutes=15)
    forecast_b = _T0 + timedelta(hours=1)
    settlement_b = forecast_b + timedelta(minutes=15)

    await _save_pair(
        store,
        forecast_id="forecast-a",
        analysis_id="analysis-a",
        market_id="market-a",
        forecasted_at=forecast_a,
        settlement_at=settlement_a,
        actual_outcome="UP",
        probability_yes=0.6,
        recommendation="BUY_YES",
        market_implied=0.55,
        cost_yes="0.50",
    )
    await _save_pair(
        store,
        forecast_id="forecast-b",
        analysis_id="analysis-b",
        market_id="market-b",
        forecasted_at=forecast_b,
        settlement_at=settlement_b,
        actual_outcome="DOWN",
        probability_yes=0.3,
        recommendation="BUY_NO",
        market_implied=0.35,
        cost_no="0.60",
    )
    await _save_prices(
        store,
        market_id="market-a",
        outcome_id="yes",
        prices=[
            (forecast_a - timedelta(minutes=1), "0.50"),
            (settlement_a - timedelta(minutes=1), "0.70"),
            (settlement_a + timedelta(hours=1), "1.00"),
        ],
    )
    await _save_prices(
        store,
        market_id="market-b",
        outcome_id="yes",
        prices=[
            (forecast_b - timedelta(minutes=1), "0.30"),
            (settlement_b - timedelta(minutes=1), "0.25"),
            (settlement_b + timedelta(hours=1), "0.00"),
        ],
    )

    report = await build_closing_line_report(store, category="crypto")

    assert report.samples == 2
    assert report.entry_coverage == Decimal("1")
    assert report.closing_coverage == Decimal("1")
    assert report.model_brier == Decimal("0.125")
    assert report.entry_market_brier == Decimal("0.1625")
    assert report.bss_vs_entry_market == pytest.approx(
        Decimal("1") - Decimal("0.125") / Decimal("0.1625")
    )
    assert report.closing_market_brier == Decimal("0.07625")
    assert report.bss_vs_closing_market is not None
    assert report.bss_vs_closing_market < 0

    by_forecast = {sample.forecast_id: sample for sample in report.records}
    assert by_forecast["forecast-a"].entry_market_probability_yes == Decimal("0.55")
    assert by_forecast["forecast-a"].entry_source == "forecast_snapshot"
    assert by_forecast["forecast-a"].entry_observed_at == forecast_a - timedelta(
        seconds=45
    )
    # The post-resolution 1.00 print is deliberately excluded.
    assert by_forecast["forecast-a"].closing_market_probability_yes == Decimal("0.70")
    assert by_forecast["forecast-a"].closing_observed_at == settlement_a - timedelta(
        minutes=1
    )
    assert by_forecast["forecast-b"].closing_market_probability_yes == Decimal("0.25")

    assert report.trade_count == 2
    assert report.mean_clv == Decimal("0.175")
    assert report.positive_clv_rate == Decimal("1")


@pytest.mark.asyncio
async def test_report_falls_back_to_last_stored_price_before_forecast(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    forecasted_at = _T0
    settlement_at = _T0 + timedelta(minutes=15)
    observed_at = forecasted_at - timedelta(minutes=5)

    await _save_pair(
        store,
        forecast_id="forecast-c",
        analysis_id="analysis-c",
        market_id="market-c",
        forecasted_at=forecasted_at,
        settlement_at=settlement_at,
        actual_outcome="NO",
        probability_yes=0.45,
        recommendation="NO_TRADE",
    )
    await _save_prices(
        store,
        market_id="market-c",
        outcome_id="yes",
        prices=[
            (observed_at, "0.40"),
            (forecasted_at + timedelta(minutes=1), "0.60"),
            (settlement_at + timedelta(minutes=5), "0.01"),
        ],
    )

    report = await build_closing_line_report(store, category="crypto")

    assert report.samples == 1
    sample = report.records[0]
    assert sample.entry_market_probability_yes == Decimal("0.40")
    assert sample.entry_observed_at == observed_at
    assert sample.entry_source == "polymarket_clob_prices_history"
    # The latest pre-settlement price is the closing line; the post-settlement
    # 0.01 print must not leak into it.
    assert sample.closing_market_probability_yes == Decimal("0.60")
    assert sample.closing_observed_at == forecasted_at + timedelta(minutes=1)
    assert report.closing_coverage == Decimal("1")
    assert report.trade_count == 0
    assert report.mean_clv is None
