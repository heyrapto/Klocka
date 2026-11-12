from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest

from prediction_ai.core.config import Settings
from prediction_ai.db.sqlite import SQLiteStore
from prediction_ai.evaluation.history_backfill import backfill_polymarket_price_history
from prediction_ai.integrations.http import ProviderHttpClient
from prediction_ai.integrations.price_history import (
    PolymarketPriceHistoryClient,
    _downsample,
    order_book_to_price_histories,
)
from prediction_ai.schemas.market import NormalizedMarket, NormalizedOrderBook
from prediction_ai.schemas.price_history import OutcomePriceHistory, PricePoint

_T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def _store(tmp_path: Path) -> SQLiteStore:
    store = SQLiteStore(str(tmp_path / "history.db"))
    store.initialize()
    return store


def _settings(**overrides: Any) -> Settings:
    return Settings(ENVIRONMENT="test", PROVIDER_MAX_RETRIES=0, **overrides)


def _history(
    *,
    outcome_id: str = "yes",
    token_id: str = "token-yes",
    points: list[tuple[datetime, str]],
) -> OutcomePriceHistory:
    return OutcomePriceHistory(
        platform="polymarket",
        platform_market_id="market-1",
        outcome_id=outcome_id,
        token_id=token_id,
        label="Up",
        granularity_seconds=60,
        points=[
            PricePoint(
                observed_at=observed_at,
                price=Decimal(price),
                source="polymarket_clob_prices_history",
            )
            for observed_at, price in points
        ],
    )


@pytest.mark.asyncio
async def test_polymarket_history_parses_and_caps_points() -> None:
    settings = _settings(PRICE_HISTORY_MAX_POINTS=50)
    raw = [
        {
            "t": int((_T0 + timedelta(minutes=index)).timestamp()),
            "p": "0.4",
        }
        for index in range(200)
    ]
    raw.append({"t": "not-a-timestamp", "p": "0.5"})
    raw.append({"t": int(_T0.timestamp()), "p": "1.5"})

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/prices-history"
        assert request.url.params["market"] == "token-yes"
        assert request.url.params["fidelity"] == "1"
        return httpx.Response(200, json={"history": raw})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        clob = ProviderHttpClient(
            http,
            settings,
            provider="polymarket_clob",
            base_url="https://clob.example",
        )
        client = PolymarketPriceHistoryClient(settings, clob)
        history = await client.fetch_outcome_history(
            platform_market_id="market-1",
            outcome_id="yes",
            token_id="token-yes",
            label="Up",
            start=_T0,
            end=_T0 + timedelta(hours=4),
        )

    assert len(history.points) == 50
    assert history.points[0].observed_at == _T0
    assert history.points[-1].observed_at == _T0 + timedelta(minutes=199)
    assert history.granularity_seconds == 60
    assert all(point.price == Decimal("0.4") for point in history.points)


def test_downsample_preserves_both_endpoints() -> None:
    points = [
        PricePoint(
            observed_at=_T0 + timedelta(minutes=index),
            price=Decimal("0.5"),
            source="polymarket_clob_prices_history",
        )
        for index in range(10)
    ]

    reduced = _downsample(points, 4)

    assert len(reduced) == 4
    assert reduced[0] == points[0]
    assert reduced[-1] == points[-1]


@pytest.mark.asyncio
async def test_repository_round_trip_is_idempotent_and_lookahead_safe(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    first = _T0
    second = _T0 + timedelta(minutes=1)
    history = _history(points=[(first, "0.40"), (second, "0.44")])

    assert await store.record_price_history(history) == 2
    assert await store.record_price_history(history) == 0

    rows = await store.get_price_history("polymarket", "market-1", "yes")
    assert [row["price"] for row in rows] == [Decimal("0.40"), Decimal("0.44")]

    latest = await store.get_latest_price_before(
        "polymarket", "market-1", "yes", second
    )
    assert latest is not None
    assert latest["price"] == Decimal("0.44")
    assert latest["observed_at"] == second.isoformat()

    before_any = await store.get_latest_price_before(
        "polymarket", "market-1", "yes", first - timedelta(seconds=1)
    )
    assert before_any is None


def test_order_book_snapshot_converts_to_decimal_midpoints() -> None:
    market = NormalizedMarket.model_validate(
        {
            "platform": "polymarket",
            "platform_market_id": "market-1",
            "title": "Bitcoin Up or Down - 15 minutes?",
            "category": "crypto",
            "market_type": "binary",
            "outcomes": [
                {"outcome_id": "yes", "token_id": "token-yes", "label": "Up"},
                {"outcome_id": "no", "token_id": "token-no", "label": "Down"},
            ],
            "status": "active",
        }
    )
    book = NormalizedOrderBook.model_validate(
        {
            "platform": "polymarket",
            "platform_market_id": "market-1",
            "observed_at": _T0,
            "quotes": [
                {
                    "outcome_id": "yes",
                    "token_id": "token-yes",
                    "label": "Up",
                    "best_bid": "0.40",
                    "best_ask": "0.44",
                },
                {
                    "outcome_id": "no",
                    "token_id": "token-no",
                    "label": "Down",
                    "best_bid": "0.56",
                    "best_ask": "0.60",
                },
            ],
        }
    )

    histories = order_book_to_price_histories(market, book)

    assert len(histories) == 2
    by_outcome = {history.outcome_id: history for history in histories}
    assert by_outcome["yes"].points[0].price == Decimal("0.42")
    assert by_outcome["no"].points[0].price == Decimal("0.58")
    assert by_outcome["yes"].points[0].source == "polymarket_clob_snapshot"


class _Fetcher:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def fetch_outcome_history(
        self,
        *,
        platform_market_id: str,
        outcome_id: str,
        token_id: str,
        label: str | None,
        start: datetime,
        end: datetime,
        fidelity_minutes: int | None = None,
    ) -> OutcomePriceHistory:
        self.calls.append(
            {
                "platform_market_id": platform_market_id,
                "outcome_id": outcome_id,
                "token_id": token_id,
                "label": label,
                "start": start,
                "end": end,
                "fidelity_minutes": fidelity_minutes,
            }
        )
        return OutcomePriceHistory(
            platform="polymarket",
            platform_market_id=platform_market_id,
            outcome_id=outcome_id,
            token_id=token_id,
            label=label,
            granularity_seconds=60,
            points=[
                PricePoint(
                    observed_at=start,
                    price=Decimal("0.50"),
                    source="polymarket_clob_prices_history",
                ),
                PricePoint(
                    observed_at=end,
                    price=Decimal("0.60"),
                    source="polymarket_clob_prices_history",
                ),
            ],
        )


async def _save_resolved_pair(
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
    token_ids: tuple[str, str] | None = None,
) -> None:
    yes_token, no_token = token_ids or (f"{market_id}-yes", f"{market_id}-no")
    market = {
        "platform": "polymarket",
        "platform_market_id": market_id,
        "title": "Bitcoin Up or Down - 15 minutes?",
        "category": "crypto",
        "market_type": "binary",
        "outcomes": [
            {"outcome_id": "yes", "token_id": yes_token, "label": "Up"},
            {"outcome_id": "no", "token_id": no_token, "label": "Down"},
        ],
        "resolves_at": settlement_at.isoformat(),
        "closes_at": settlement_at.isoformat(),
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
            "contract_spec": {
                "affirmative_outcome_id": "yes",
                "negative_outcome_id": "no",
            },
            "target_definition": {
                "affirmative_outcome_id": "yes",
                "settlement_timestamp": settlement_at.isoformat(),
            },
            "timing_provenance": {"exact_resolves_at": settlement_at.isoformat()},
            "input_snapshot": (
                {"market_implied": market_implied}
                if market_implied is not None
                else {}
            ),
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


@pytest.mark.asyncio
async def test_backfill_fetches_both_outcomes_and_persists_idempotently(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    forecasted_at = _T0
    settlement_at = _T0 + timedelta(minutes=15)
    await _save_resolved_pair(
        store,
        forecast_id="forecast-1",
        analysis_id="analysis-1",
        market_id="market-1",
        forecasted_at=forecasted_at,
        settlement_at=settlement_at,
        actual_outcome="UP",
        probability_yes=0.6,
        recommendation="BUY_YES",
        market_implied=0.55,
        cost_yes="0.50",
    )

    fetcher = _Fetcher()
    summary = await backfill_polymarket_price_history(
        store,
        fetcher,
        category="crypto",
        lookback_hours=1,
    )

    assert summary.pairs_scanned == 1
    assert summary.pairs_requested == 1
    assert summary.outcomes_requested == 2
    assert summary.points_fetched == 4
    assert summary.points_inserted == 4
    assert summary.skipped == 0
    assert [call["token_id"] for call in fetcher.calls] == [
        "market-1-yes",
        "market-1-no",
    ]
    assert all(call["start"] == forecasted_at - timedelta(hours=1) for call in fetcher.calls)
    assert all(call["end"] == settlement_at for call in fetcher.calls)

    rows = await store.get_price_history("polymarket", "market-1", "yes")
    assert len(rows) == 2
    assert rows[0]["observed_at"] == (forecasted_at - timedelta(hours=1)).isoformat()

    repeat = await backfill_polymarket_price_history(
        store,
        fetcher,
        category="crypto",
        lookback_hours=1,
    )
    assert repeat.points_inserted == 0


@pytest.mark.asyncio
async def test_backfill_skips_outcomes_without_token_ids(tmp_path: Path) -> None:
    store = _store(tmp_path)
    forecasted_at = _T0
    settlement_at = _T0 + timedelta(minutes=15)
    await _save_resolved_pair(
        store,
        forecast_id="forecast-2",
        analysis_id="analysis-2",
        market_id="market-2",
        forecasted_at=forecasted_at,
        settlement_at=settlement_at,
        actual_outcome="DOWN",
        probability_yes=0.4,
        recommendation="BUY_NO",
        token_ids=("", ""),
    )

    fetcher = _Fetcher()
    summary = await backfill_polymarket_price_history(store, fetcher, category="crypto")

    assert fetcher.calls == []
    assert summary.outcomes_requested == 0
    assert summary.skipped_reasons == {"missing_token_id": 2}
    assert summary.points_inserted == 0
