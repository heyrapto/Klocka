from decimal import Decimal

import pytest

from prediction_ai.decision.engine import evaluate_decision


def test_decision_buy_yes():
    # p_yes = 0.70, ask_yes = 0.55 -> net edge should exceed 4% threshold
    eval_result = evaluate_decision(
        probability_yes=Decimal("0.70"),
        ask_yes=Decimal("0.55"),
        ask_no=Decimal("0.45"),
        min_edge=Decimal("0.04"),
        is_data_fresh=True,
    )
    assert eval_result.recommendation == "BUY_YES"
    assert eval_result.is_abstention is False
    assert eval_result.metrics.estimated_net_edge_yes > Decimal("0.04")


def test_decision_no_trade_insufficient_edge():
    # p_yes = 0.56, ask_yes = 0.55 -> net edge ~ 0.01 < 0.06 (threshold with margin)
    eval_result = evaluate_decision(
        probability_yes=Decimal("0.56"),
        ask_yes=Decimal("0.55"),
        ask_no=Decimal("0.45"),
        min_edge=Decimal("0.04"),
        is_data_fresh=True,
    )
    assert eval_result.recommendation == "NO_TRADE"
    assert eval_result.is_abstention is True
    assert "INSUFFICIENT_EDGE" in eval_result.gating_flags


def test_decision_no_trade_stale_feed():
    eval_result = evaluate_decision(
        probability_yes=Decimal("0.85"),
        ask_yes=Decimal("0.50"),
        ask_no=Decimal("0.50"),
        min_edge=Decimal("0.04"),
        is_data_fresh=False,
    )
    assert eval_result.recommendation == "NO_TRADE"
    assert eval_result.is_abstention is True
    assert "DATA_STALE" in eval_result.gating_flags


def test_decision_no_trade_excessive_spread():
    eval_result = evaluate_decision(
        probability_yes=Decimal("0.70"),
        ask_yes=Decimal("0.60"),
        ask_no=Decimal("0.60"),
        max_spread=Decimal("0.05"),
        spread=Decimal("0.20"),
        is_data_fresh=True,
    )
    assert eval_result.recommendation == "NO_TRADE"
    assert eval_result.is_abstention is True
    assert "EXCESSIVE_SPREAD" in eval_result.gating_flags


def test_decision_missing_quotes_are_never_synthesized():
    eval_result = evaluate_decision(
        probability_yes=Decimal("0.90"),
        ask_yes=None,
        ask_no=None,
        min_edge=Decimal("0.04"),
    )
    assert eval_result.recommendation == "NO_TRADE"
    assert eval_result.is_abstention is True
    assert "MISSING_EXECUTABLE_QUOTE" in eval_result.gating_flags
    assert eval_result.metrics.executable_ask_yes is None
    assert eval_result.metrics.executable_ask_no is None
    assert eval_result.metrics.estimated_net_edge_yes is None


def test_decision_missing_one_quote_can_trade_only_the_quoted_side():
    eval_result = evaluate_decision(
        probability_yes=Decimal("0.70"),
        ask_yes=Decimal("0.55"),
        ask_no=None,
        min_edge=Decimal("0.04"),
    )
    assert eval_result.recommendation == "BUY_YES"
    assert "MISSING_EXECUTABLE_QUOTE" in eval_result.gating_flags
    assert eval_result.metrics.executable_ask_no is None


def test_decision_no_trade_stale_quote_age():
    eval_result = evaluate_decision(
        probability_yes=Decimal("0.85"),
        ask_yes=Decimal("0.50"),
        ask_no=Decimal("0.50"),
        quote_age_seconds=Decimal("30"),
        max_quote_age_seconds=Decimal("15"),
    )
    assert eval_result.recommendation == "NO_TRADE"
    assert "STALE_QUOTE" in eval_result.gating_flags


def test_decision_no_trade_when_both_sides_lack_depth():
    eval_result = evaluate_decision(
        probability_yes=Decimal("0.90"),
        ask_yes=Decimal("0.50"),
        ask_no=Decimal("0.50"),
        depth_known=True,
        depth_ask_usd_yes=Decimal("25"),
        depth_ask_usd_no=Decimal("25"),
        target_order_usd=Decimal("100"),
    )
    assert eval_result.recommendation == "NO_TRADE"
    assert "INSUFFICIENT_DEPTH" in eval_result.gating_flags


def test_decision_can_trade_the_deep_side_when_other_side_is_thin():
    eval_result = evaluate_decision(
        probability_yes=Decimal("0.30"),
        ask_yes=Decimal("0.50"),
        ask_no=Decimal("0.55"),
        depth_known=True,
        depth_ask_usd_yes=Decimal("25"),
        depth_ask_usd_no=Decimal("500"),
        target_order_usd=Decimal("100"),
    )
    assert eval_result.recommendation == "BUY_NO"
    assert "INSUFFICIENT_DEPTH" in eval_result.gating_flags


def test_decision_blocks_unresolved_contract_semantics():
    eval_result = evaluate_decision(
        probability_yes=Decimal("0.90"),
        ask_yes=Decimal("0.50"),
        ask_no=Decimal("0.50"),
        resolution_risk_flags=["UNKNOWN_STRIKE", "UNKNOWN_COMPARATOR"],
        blocking_resolution_flags=["UNKNOWN_STRIKE", "UNKNOWN_COMPARATOR"],
        block_on_resolution_risk=True,
    )
    assert eval_result.recommendation == "NO_TRADE"
    assert "RESOLUTION_RISK" in eval_result.gating_flags
    assert eval_result.metrics.blocking_resolution_flags == [
        "UNKNOWN_STRIKE",
        "UNKNOWN_COMPARATOR",
    ]


def test_decision_resolution_flags_do_not_block_when_policy_disabled():
    eval_result = evaluate_decision(
        probability_yes=Decimal("0.90"),
        ask_yes=Decimal("0.50"),
        ask_no=Decimal("0.50"),
        resolution_risk_flags=["MINOR_WORDING_RISK"],
        blocking_resolution_flags=["UNKNOWN_STRIKE"],
        block_on_resolution_risk=False,
    )
    assert eval_result.recommendation == "BUY_YES"
    assert "RESOLUTION_RISK" not in eval_result.gating_flags


def test_decision_kelly_fraction_is_configurable():
    base = evaluate_decision(
        probability_yes=Decimal("0.70"),
        ask_yes=Decimal("0.50"),
        ask_no=Decimal("0.50"),
        kelly_fraction=Decimal("0.25"),
    )
    aggressive = evaluate_decision(
        probability_yes=Decimal("0.70"),
        ask_yes=Decimal("0.50"),
        ask_no=Decimal("0.50"),
        kelly_fraction=Decimal("0.50"),
    )
    assert base.recommendation == "BUY_YES"
    assert aggressive.metrics.kelly_fraction_used > base.metrics.kelly_fraction_used
    assert aggressive.metrics.kelly_fraction_used == pytest.approx(
        base.metrics.kelly_fraction_used * 2, rel=Decimal("0.01")
    )


def test_decision_rejects_zero_ask_instead_of_pricing_a_free_contract():
    eval_result = evaluate_decision(
        probability_yes=Decimal("0.90"),
        ask_yes=Decimal("0"),
        ask_no=Decimal("0.50"),
        min_edge=Decimal("0.04"),
    )

    assert eval_result.recommendation == "NO_TRADE"
    assert eval_result.is_abstention is True
    assert "INVALID_EXECUTABLE_QUOTE" in eval_result.gating_flags
    assert "MISSING_EXECUTABLE_QUOTE" in eval_result.gating_flags
    assert eval_result.metrics.executable_ask_yes is None
    assert eval_result.metrics.effective_cost_yes is None
    assert eval_result.metrics.estimated_net_edge_yes is None


def test_decision_rejects_dollar_ask_as_non_executable():
    eval_result = evaluate_decision(
        probability_yes=Decimal("0.90"),
        ask_yes=Decimal("1"),
        ask_no=Decimal("0.50"),
        min_edge=Decimal("0.04"),
    )

    assert eval_result.recommendation == "NO_TRADE"
    assert "INVALID_EXECUTABLE_QUOTE" in eval_result.gating_flags
    assert eval_result.metrics.executable_ask_yes is None
    assert eval_result.metrics.estimated_net_edge_yes is None


def test_decision_rejects_endpoint_asks_on_both_sides():
    eval_result = evaluate_decision(
        probability_yes=Decimal("0.90"),
        ask_yes=Decimal("0"),
        ask_no=Decimal("1"),
        min_edge=Decimal("0.04"),
    )

    assert eval_result.recommendation == "NO_TRADE"
    assert eval_result.is_abstention is True
    assert eval_result.gating_flags.count("INVALID_EXECUTABLE_QUOTE") == 1
    assert "MISSING_EXECUTABLE_QUOTE" in eval_result.gating_flags
