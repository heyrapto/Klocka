from decimal import Decimal

import pytest

from prediction_ai.decision.costs import (
    calculate_effective_cost,
    fee_per_share,
    walk_book_for_notional,
)
from prediction_ai.schemas.market import BookLevel


def test_polymarket_variance_fee_is_exact_at_midprice() -> None:
    # Polymarket: feeRate * p * (1 - p).
    assert fee_per_share(Decimal("0.50"), Decimal("0.07"), "variance") == Decimal(
        "0.0175"
    )
    assert fee_per_share(Decimal("0.80"), Decimal("0.07"), "variance") == Decimal(
        "0.0112"
    )


def test_bayse_fee_uses_half_floor_above_midprice() -> None:
    # Bayse: feeRate * p * max(1 - p, 0.5).
    assert fee_per_share(
        Decimal("0.50"), Decimal("0.10"), "variance_floor_half"
    ) == Decimal("0.025")
    assert fee_per_share(
        Decimal("0.80"), Decimal("0.10"), "variance_floor_half"
    ) == Decimal("0.04")


def test_fee_free_market_costs_zero_and_unknown_model_is_conservative() -> None:
    assert fee_per_share(Decimal("0.50"), Decimal("0.10"), "none") == Decimal("0.0")
    unknown = fee_per_share(Decimal("0.80"), Decimal("0.10"), "mystery")
    bayse = fee_per_share(Decimal("0.80"), Decimal("0.10"), "variance_floor_half")
    assert unknown == bayse


def test_effective_cost_includes_fee_and_slippage() -> None:
    cost = calculate_effective_cost(
        Decimal("0.50"),
        taker_fee_rate=Decimal("0.07"),
        fee_model="variance",
        slippage_bps=15,
    )
    # 0.50 + 0.0175 fee + 0.00075 slippage
    assert cost == Decimal("0.51825")


def test_walk_book_returns_depth_aware_vwap_and_completion() -> None:
    levels = [
        BookLevel(price=Decimal("0.50"), size=Decimal("100")),
        BookLevel(price=Decimal("0.60"), size=Decimal("100")),
    ]

    filled = walk_book_for_notional(levels, Decimal("100"))
    assert filled.complete is True
    assert filled.filled_notional == Decimal("100")
    assert filled.average_price == pytest.approx(
        Decimal("0.5454545454545454545454545455"), rel=Decimal("0.0001")
    )

    partial = walk_book_for_notional(levels, Decimal("500"))
    assert partial.complete is False
    assert partial.filled_notional == Decimal("110")
