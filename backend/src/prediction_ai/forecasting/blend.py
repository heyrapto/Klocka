"""Explicit, normalized probability blending.

The previous crypto blend assigned weights of 0.50 + 0.15 + 0.35 = 0.65 for the
model components and then blended that 0.65 with 0.35 market weight, leaving
22.75% of the probability mass unassigned.  This module makes the weights
explicit, validates that they sum to one, and returns the individual components
for the immutable forecast snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class BlendResult:
    probability_yes: Decimal
    model_probability: Decimal
    components: dict[str, Decimal]
    weights: dict[str, Decimal]
    version: str
    market_available: bool = True


def _as_decimal(value: Decimal | float | int) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _validate_weight(name: str, value: Decimal) -> None:
    if value < 0 or value > 1:
        raise ValueError(f"{name} weight must be in [0, 1], got {value}")


def _validate_probability(name: str, value: Decimal) -> None:
    if value < 0 or value > 1:
        raise ValueError(f"{name} probability must be in [0, 1], got {value}")


def blend_probabilities(
    *,
    llm_probability: Decimal | float,
    market_probability: Decimal | float | None = None,
    math_probability: Decimal | float | None = None,
    math_weight: Decimal | float = Decimal("0.0"),
    llm_weight: Decimal | float = Decimal("0.35"),
    market_weight: Decimal | float = Decimal("0.65"),
    version: str = "linear_normalized_v2",
) -> BlendResult:
    """Blend component probabilities with weights that must sum to one.

    When ``math_probability`` is present, the model component is the normalized
    weighted average of the math and LLM probabilities.  The final probability
    is then the weighted average of that model component and the market prior.
    This is algebraically identical to assigning each of the three components
    its own weight, but keeps the diagnostics interpretable.

    ``market_probability`` is optional on purpose.  If the platform has no
    executable quote, there is no market prior.  In that case the configured
    weights must still sum to one, but the available model weights are
    renormalized and the market component is omitted from the diagnostics.
    The function never substitutes a neutral 0.50 anchor.
    """
    llm = _as_decimal(llm_probability)
    _validate_probability("llm", llm)
    market = (
        _as_decimal(market_probability) if market_probability is not None else None
    )
    if market is not None:
        _validate_probability("market", market)
    math_w = _as_decimal(math_weight)
    llm_w = _as_decimal(llm_weight)
    market_w = _as_decimal(market_weight)

    for name, value in (("math", math_w), ("llm", llm_w), ("market", market_w)):
        _validate_weight(name, value)

    if math_probability is not None:
        math = _as_decimal(math_probability)
        _validate_probability("math", math)
        total = math_w + llm_w + market_w
        if abs(total - Decimal("1.0")) > Decimal("1e-9"):
            raise ValueError(f"Blend weights must sum to 1.0, got {total}")
        model_total = math_w + llm_w
        if model_total <= 0:
            raise ValueError("At least one of math_weight or llm_weight must be positive.")
        model_probability = ((math_w * math) + (llm_w * llm)) / model_total
        if market is None:
            probability = model_probability
            components = {"math": math, "llm": llm}
            weights = {
                "math": math_w / model_total,
                "llm": llm_w / model_total,
            }
        else:
            probability = (math_w * math) + (llm_w * llm) + (market_w * market)
            components = {"math": math, "llm": llm, "market": market}
            weights = {"math": math_w, "llm": llm_w, "market": market_w}
    else:
        total = llm_w + market_w
        if abs(total - Decimal("1.0")) > Decimal("1e-9"):
            raise ValueError(f"LLM and market weights must sum to 1.0, got {total}")
        if market is None:
            if llm_w <= 0:
                raise ValueError(
                    "At least one model component must have positive weight when "
                    "the market prior is unavailable."
                )
            model_probability = llm
            probability = llm
            components = {"llm": llm}
            weights = {"llm": Decimal("1.0")}
        else:
            model_probability = llm
            probability = (llm_w * llm) + (market_w * market)
            components = {"llm": llm, "market": market}
            weights = {"llm": llm_w, "market": market_w}

    probability = min(max(probability, Decimal("0.001")), Decimal("0.999"))
    model_probability = min(max(model_probability, Decimal("0.001")), Decimal("0.999"))
    return BlendResult(
        probability_yes=probability,
        model_probability=model_probability,
        components=components,
        weights=weights,
        version=version,
        market_available=market is not None,
    )
