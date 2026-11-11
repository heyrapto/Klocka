from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from prediction_ai.core.config import Settings
from prediction_ai.forecasting.gemini_forecaster import (
    GeminiForecaster,
    _market_implied_probability,
)
from prediction_ai.forecasting.schemas import GeminiForecastOutput
from prediction_ai.schemas.contract import ContractSpec
from prediction_ai.schemas.market import (
    NormalizedMarket,
    NormalizedOrderBook,
    NormalizedOutcome,
    OutcomeQuote,
)


def _market() -> NormalizedMarket:
    return NormalizedMarket(
        platform="polymarket",
        platform_market_id="btc-below-74000",
        title="Will BTC be below 74000 in 15 minutes?",
        category="crypto",
        market_type="binary",
        outcomes=[
            NormalizedOutcome(outcome_id="yes", token_id="yes-token", label="YES"),
            NormalizedOutcome(outcome_id="no", token_id="no-token", label="NO"),
        ],
        status="active",
    )


def _order_book() -> NormalizedOrderBook:
    return NormalizedOrderBook(
        platform="polymarket",
        platform_market_id="btc-below-74000",
        observed_at=datetime.now(UTC),
        quotes=[
            OutcomeQuote(
                outcome_id="yes",
                token_id="yes-token",
                label="YES",
                best_bid=Decimal("0.49"),
                best_ask=Decimal("0.51"),
                mid_price=Decimal("0.50"),
                depth_bid_usd=Decimal("1000"),
                depth_ask_usd=Decimal("1000"),
            ),
            OutcomeQuote(
                outcome_id="no",
                token_id="no-token",
                label="NO",
                best_bid=Decimal("0.49"),
                best_ask=Decimal("0.51"),
                mid_price=Decimal("0.50"),
                depth_bid_usd=Decimal("1000"),
                depth_ask_usd=Decimal("1000"),
            ),
        ],
    )


def test_blend_prefers_direction_aware_crypto_probability() -> None:
    forecaster = object.__new__(GeminiForecaster)
    forecaster._model = "test-model"
    forecaster._settings = Settings(
        FORECAST_CRYPTO_MATH_WEIGHT=0.40,
        FORECAST_CRYPTO_LLM_WEIGHT=0.10,
        FORECAST_CRYPTO_MARKET_WEIGHT=0.50,
        FORECAST_BLEND_VERSION="linear_normalized_v2",
    )
    output = GeminiForecastOutput(
        raw_probability_yes=0.50,
        uncertainty_lower=0.40,
        uncertainty_upper=0.60,
        reasoning_summary="Test reasoning",
    )

    result = forecaster._blend_and_calibrate(
        _market(),
        _order_book(),
        Decimal("0.50"),
        output,
        specialist_context={
            "crypto_research": {
                "final_adjusted_prob_yes": "0.90",
                "adjusted_prob_yes": "0.10",
                "base_prob_yes": "0.10",
            }
        },
    )

    # 40% math (0.90) + 10% LLM (0.50) + 50% market (0.50) = 0.66.
    # Model component = (0.40*0.90 + 0.10*0.50) / 0.50 = 0.82.
    assert result.input_snapshot["model_raw"] == pytest.approx(0.82)
    assert result.input_snapshot["component_probabilities"]["math"] == pytest.approx(0.90)
    assert sum(result.input_snapshot["blend_weights"].values()) == pytest.approx(1.0)
    assert float(result.probability_yes) == pytest.approx(0.66)
    assert result.calibration_version == "unfitted_linear_normalized_v2"


def test_blend_falls_back_when_final_probability_is_absent() -> None:
    forecaster = object.__new__(GeminiForecaster)
    forecaster._model = "test-model"
    forecaster._settings = Settings(
        FORECAST_CRYPTO_MATH_WEIGHT=0.40,
        FORECAST_CRYPTO_LLM_WEIGHT=0.10,
        FORECAST_CRYPTO_MARKET_WEIGHT=0.50,
        FORECAST_BLEND_VERSION="linear_normalized_v2",
    )
    output = GeminiForecastOutput(
        raw_probability_yes=0.50,
        uncertainty_lower=0.40,
        uncertainty_upper=0.60,
        reasoning_summary="Test reasoning",
    )

    result = forecaster._blend_and_calibrate(
        _market(),
        _order_book(),
        Decimal("0.50"),
        output,
        specialist_context={"crypto_research": {"adjusted_prob_yes": "0.20"}},
    )

    # 40% math (0.20) + 10% LLM (0.50) + 50% market (0.50) = 0.38.
    # Model component = (0.40*0.20 + 0.10*0.50) / 0.50 = 0.26.
    assert result.input_snapshot["model_raw"] == pytest.approx(0.26)
    assert float(result.probability_yes) == pytest.approx(0.38)


class _FakeModels:
    def __init__(self, text: str) -> None:
        self._text = text

    async def generate_content(self, **_: object) -> SimpleNamespace:
        return SimpleNamespace(text=self._text)


class _FakeGeminiClient:
    def __init__(self, text: str) -> None:
        self.aio = SimpleNamespace(models=_FakeModels(text))


def _contract_spec() -> ContractSpec:
    return ContractSpec(
        platform="polymarket",
        platform_market_id="btc-below-74000",
        title="Will BTC be below 74000 in 15 minutes?",
        category="crypto",
        market_type="binary",
        direction="below",
        outcomes=_market().outcomes,
        affirmative_outcome_id="yes",
        negative_outcome_id="no",
        affirmative_token_id="yes-token",
        negative_token_id="no-token",
        comparator="lt",
        strike_value=Decimal("74000"),
        spec_hash="test-spec",
    )


def test_market_implied_rejects_endpoint_mid_and_uses_two_sided_midpoint() -> None:
    quote = OutcomeQuote(
        outcome_id="yes",
        token_id="yes-token",
        label="YES",
        best_bid=Decimal("0.60"),
        best_ask=Decimal("0.64"),
        mid_price=Decimal("1.0"),
    )

    assert _market_implied_probability(quote) == Decimal("0.62")


def test_market_implied_is_none_without_a_quote_or_two_sided_book() -> None:
    endpoint_quote = OutcomeQuote(
        outcome_id="yes",
        token_id="yes-token",
        label="YES",
        mid_price=Decimal("0.0"),
    )

    assert _market_implied_probability(None) is None
    assert _market_implied_probability(endpoint_quote) is None


@pytest.mark.asyncio
async def test_forecast_excludes_market_prior_when_no_quote_exists() -> None:
    settings = Settings(
        GEMINI_API_KEY="test-key",
        GEMINI_MODEL="test-model",
        FORECAST_CRYPTO_MATH_WEIGHT=0.40,
        FORECAST_CRYPTO_LLM_WEIGHT=0.10,
        FORECAST_CRYPTO_MARKET_WEIGHT=0.50,
        FORECAST_BLEND_VERSION="linear_normalized_v2",
    )
    order_book = _order_book()
    for quote in order_book.quotes:
        quote.mid_price = None
        quote.best_bid = None
        quote.best_ask = None

    response = json.dumps(
        {
            "raw_probability_yes": 0.50,
            "uncertainty_lower": 0.40,
            "uncertainty_upper": 0.60,
            "reasoning_summary": "Test reasoning",
        }
    )
    forecaster = GeminiForecaster(
        settings, client=_FakeGeminiClient(response)  # type: ignore[arg-type]
    )

    result = await forecaster.forecast(
        _market(),
        order_book,
        contract_spec=_contract_spec(),
        specialist_context={
            "crypto_research": {"final_adjusted_prob_yes": "0.90"}
        },
    )

    # Without a market anchor the model weights renormalize: math 0.8, LLM 0.2.
    assert result.market_implied_probability is None
    assert result.input_snapshot["market_implied"] is None
    assert result.input_snapshot["market_anchor_available"] is False
    assert result.input_snapshot["blend_weights"] == pytest.approx(
        {"math": 0.8, "llm": 0.2}
    )
    assert float(result.probability_yes) == pytest.approx(0.82)
    assert "No executable platform quote" in result.evidence[-1].snippet
