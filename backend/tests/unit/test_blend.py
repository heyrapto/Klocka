from decimal import Decimal

import pytest
from pydantic import ValidationError

from prediction_ai.core.config import Settings
from prediction_ai.forecasting.blend import blend_probabilities


def test_crypto_blend_assigns_full_probability_mass() -> None:
    result = blend_probabilities(
        math_probability=Decimal("0.90"),
        llm_probability=Decimal("0.50"),
        market_probability=Decimal("0.50"),
        math_weight=Decimal("0.40"),
        llm_weight=Decimal("0.10"),
        market_weight=Decimal("0.50"),
    )

    assert result.probability_yes == Decimal("0.66")
    assert result.model_probability == Decimal("0.82")
    assert sum(result.weights.values()) == Decimal("1.00")
    assert result.components == {
        "math": Decimal("0.90"),
        "llm": Decimal("0.50"),
        "market": Decimal("0.50"),
    }


def test_general_blend_assigns_full_probability_mass() -> None:
    result = blend_probabilities(
        llm_probability=Decimal("0.80"),
        market_probability=Decimal("0.40"),
        llm_weight=Decimal("0.35"),
        market_weight=Decimal("0.65"),
    )

    assert result.probability_yes == Decimal("0.54")
    assert result.model_probability == Decimal("0.80")
    assert sum(result.weights.values()) == Decimal("1.00")


def test_blend_rejects_weights_that_do_not_sum_to_one() -> None:
    with pytest.raises(ValueError, match="must sum to 1.0"):
        blend_probabilities(
            math_probability=Decimal("0.60"),
            llm_probability=Decimal("0.50"),
            market_probability=Decimal("0.50"),
            math_weight=Decimal("0.50"),
            llm_weight=Decimal("0.15"),
            market_weight=Decimal("0.25"),
        )


def test_blend_rejects_out_of_range_probabilities() -> None:
    with pytest.raises(ValueError, match="probability must be in"):
        blend_probabilities(
            llm_probability=Decimal("1.20"),
            market_probability=Decimal("0.50"),
        )


def test_blend_omits_missing_market_prior_and_renormalizes() -> None:
    result = blend_probabilities(
        math_probability=Decimal("0.90"),
        llm_probability=Decimal("0.50"),
        market_probability=None,
        math_weight=Decimal("0.40"),
        llm_weight=Decimal("0.10"),
        market_weight=Decimal("0.50"),
    )

    assert result.market_available is False
    assert result.probability_yes == Decimal("0.82")
    assert result.model_probability == Decimal("0.82")
    assert result.components == {
        "math": Decimal("0.90"),
        "llm": Decimal("0.50"),
    }
    assert result.weights == {"math": Decimal("0.8"), "llm": Decimal("0.2")}


def test_general_blend_without_market_prior_uses_llm_only() -> None:
    result = blend_probabilities(
        llm_probability=Decimal("0.70"),
        market_probability=None,
        llm_weight=Decimal("0.35"),
        market_weight=Decimal("0.65"),
    )

    assert result.market_available is False
    assert result.probability_yes == Decimal("0.70")
    assert result.components == {"llm": Decimal("0.70")}
    assert result.weights == {"llm": Decimal("1.0")}


def test_blend_requires_a_model_component_when_math_is_present() -> None:
    with pytest.raises(ValueError, match="At least one of math_weight or llm_weight"):
        blend_probabilities(
            math_probability=Decimal("0.60"),
            llm_probability=Decimal("0.50"),
            market_probability=Decimal("0.50"),
            math_weight=Decimal("0.0"),
            llm_weight=Decimal("0.0"),
            market_weight=Decimal("1.0"),
        )


def test_settings_reject_crypto_weights_that_do_not_sum_to_one() -> None:
    with pytest.raises(ValidationError, match="FORECAST_CRYPTO"):
        Settings(
            FORECAST_CRYPTO_MATH_WEIGHT=0.50,
            FORECAST_CRYPTO_LLM_WEIGHT=0.15,
            FORECAST_CRYPTO_MARKET_WEIGHT=0.25,
        )
