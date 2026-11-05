from decimal import Decimal

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
