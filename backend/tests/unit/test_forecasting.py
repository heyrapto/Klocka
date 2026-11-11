from datetime import UTC, datetime
from decimal import Decimal

import pytest

from prediction_ai.forecasting.baseline import BaselineForecaster
from prediction_ai.schemas.market import (
    BookLevel,
    NormalizedMarket,
    NormalizedOrderBook,
    NormalizedOutcome,
    OutcomeQuote,
)


def _make_test_market_and_book(
    mid_price: Decimal = Decimal("0.55"),
    bid_depth: Decimal = Decimal("2000"),
    ask_depth: Decimal = Decimal("2000"),
) -> tuple[NormalizedMarket, NormalizedOrderBook]:
    market = NormalizedMarket(
        platform="polymarket",
        platform_market_id="poly_test_1",
        title="Will Bitcoin exceed $100k by year-end 2026?",
        category="crypto",
        market_type="binary",
        underlying_symbol="BTC",
        outcomes=[
            NormalizedOutcome(outcome_id="1", token_id="tok_yes", label="Yes"),
            NormalizedOutcome(outcome_id="2", token_id="tok_no", label="No"),
        ],
        closes_at=datetime(2026, 12, 31, 23, 59, 59, tzinfo=UTC),
        resolves_at=datetime(2026, 12, 31, 23, 59, 59, tzinfo=UTC),
        status="active",
        order_book_enabled=True,
        taker_fee_rate=Decimal("0.05"),
        fee_model="variance",
        raw_metadata={},
    )
    order_book = NormalizedOrderBook(
        platform="polymarket",
        platform_market_id="poly_test_1",
        observed_at=datetime.now(UTC),
        quotes=[
            OutcomeQuote(
                outcome_id="1",
                token_id="tok_yes",
                label="Yes",
                best_bid=mid_price - Decimal("0.01"),
                best_ask=mid_price + Decimal("0.01"),
                mid_price=mid_price,
                bids=[BookLevel(price=mid_price - Decimal("0.01"), size=bid_depth)],
                asks=[BookLevel(price=mid_price + Decimal("0.01"), size=ask_depth)],
                depth_bid_usd=bid_depth,
                depth_ask_usd=ask_depth,
            ),
            OutcomeQuote(
                outcome_id="2",
                token_id="tok_no",
                label="No",
                best_bid=Decimal("1.0") - mid_price - Decimal("0.01"),
                best_ask=Decimal("1.0") - mid_price + Decimal("0.01"),
                mid_price=Decimal("1.0") - mid_price,
                depth_bid_usd=ask_depth,
                depth_ask_usd=bid_depth,
            ),
        ],
        depth_known=True,
    )
    return market, order_book


def test_baseline_forecaster_balanced():
    market, order_book = _make_test_market_and_book(mid_price=Decimal("0.60"))
    forecaster = BaselineForecaster()
    result = forecaster.forecast(market, order_book)

    assert result.probability_yes == Decimal("0.60")
    assert result.probability_no == Decimal("0.40")
    assert len(result.uncertainty_interval) == 2
    assert result.uncertainty_interval[0] < result.probability_yes < result.uncertainty_interval[1]
    assert len(result.evidence) > 0


def test_baseline_forecaster_imbalance():
    # Massive bid depth relative to ask depth
    market, order_book = _make_test_market_and_book(
        mid_price=Decimal("0.50"),
        bid_depth=Decimal("10000"),
        ask_depth=Decimal("100"),
    )
    forecaster = BaselineForecaster()
    result = forecaster.forecast(market, order_book)

    # Imbalance should shift probability upward within bounded limit
    assert result.probability_yes > Decimal("0.50")
    assert result.probability_yes + result.probability_no == Decimal("1.00")


def test_baseline_forecaster_refuses_missing_market_quote():
    market, order_book = _make_test_market_and_book()
    order_book.quotes[0].mid_price = None
    order_book.quotes[0].best_bid = None
    order_book.quotes[0].best_ask = None
    forecaster = BaselineForecaster()

    with pytest.raises(ValueError, match="no 0.50 substitute"):
        forecaster.forecast(market, order_book)


@pytest.mark.asyncio
async def test_gemini_forecaster_no_key_strictly_fails():
    from prediction_ai.core.config import Settings
    from prediction_ai.core.exceptions import ForecastFailedException
    from prediction_ai.forecasting.gemini_forecaster import GeminiForecaster

    settings = Settings(GEMINI_API_KEY=None)
    forecaster = GeminiForecaster(settings=settings)
    market, order_book = _make_test_market_and_book()

    with pytest.raises(ForecastFailedException) as exc_info:
        await forecaster.forecast(market, order_book)
    assert "GEMINI_API_KEY is not configured" in str(exc_info.value)
