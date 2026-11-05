from decimal import Decimal

from prediction_ai.decision.costs import calculate_effective_costs
from prediction_ai.schemas.decision import DecisionEvaluation, DecisionMetrics


def evaluate_decision(
    probability_yes: Decimal,
    ask_yes: Decimal,
    ask_no: Decimal,
    min_edge: Decimal = Decimal("0.04"),
    max_spread: Decimal = Decimal("0.05"),
    spread: Decimal | None = None,
    is_data_fresh: bool = True,
    uncertainty_margin: Decimal = Decimal("0.02"),
) -> DecisionEvaluation:
    """Evaluate Expected Value (EV), apply risk thresholds, and emit recommendation."""
    probability_no = Decimal("1.0") - probability_yes
    spread_calc = spread if spread is not None else (ask_yes + ask_no - Decimal("1.0"))

    effective_cost_yes, effective_cost_no = calculate_effective_costs(ask_yes, ask_no)

    # EV = P(win) * 1.0 - effective_cost
    net_edge_yes = probability_yes - effective_cost_yes
    net_edge_no = probability_no - effective_cost_no

    required_threshold = min_edge + uncertainty_margin

    # Quarter-Kelly fraction calculation: f* = 0.25 * (b * p - q) / b
    kelly_fraction = Decimal("0.0")
    if net_edge_yes > 0 and effective_cost_yes > 0:
        b = (Decimal("1.0") - effective_cost_yes) / effective_cost_yes
        kelly_fraction = max(
            Decimal("0.0"),
            Decimal("0.25") * ((b * probability_yes - (Decimal("1.0") - probability_yes)) / b),
        )

    metrics = DecisionMetrics(
        executable_ask_yes=ask_yes,
        executable_ask_no=ask_no,
        effective_cost_yes=effective_cost_yes,
        effective_cost_no=effective_cost_no,
        estimated_net_edge_yes=net_edge_yes,
        estimated_net_edge_no=net_edge_no,
        minimum_required_edge=required_threshold,
        spread=spread_calc,
        slippage_bps=15,
        quarter_kelly_fraction=round(kelly_fraction, 4),
    )

    gating_flags: list[str] = []

    # Gate 1: Data freshness
    if not is_data_fresh:
        gating_flags.append("DATA_STALE")
        return DecisionEvaluation(
            recommendation="NO_TRADE",
            metrics=metrics,
            reason="Critical market data feeds exceed freshness threshold.",
            is_abstention=True,
            gating_flags=gating_flags,
        )

    # Gate 2: Maximum allowable spread
    if spread_calc > max_spread:
        gating_flags.append("EXCESSIVE_SPREAD")
        return DecisionEvaluation(
            recommendation="NO_TRADE",
            metrics=metrics,
            reason=f"Market spread ({spread_calc}) exceeds risk threshold ({max_spread}).",
            is_abstention=True,
            gating_flags=gating_flags,
        )

    # Gate 3: Positive Expected Value on YES
    if net_edge_yes >= required_threshold:
        return DecisionEvaluation(
            recommendation="BUY_YES",
            metrics=metrics,
            reason=f"Positive net edge on YES ({net_edge_yes:.3f}) exceeds threshold ({required_threshold:.3f}).",
            is_abstention=False,
            gating_flags=[],
        )

    # Gate 4: Positive Expected Value on NO
    if net_edge_no >= required_threshold:
        return DecisionEvaluation(
            recommendation="BUY_NO",
            metrics=metrics,
            reason=f"Positive net edge on NO ({net_edge_no:.3f}) exceeds threshold ({required_threshold:.3f}).",
            is_abstention=False,
            gating_flags=[],
        )

    # Default: Abstention
    gating_flags.append("INSUFFICIENT_EDGE")
    return DecisionEvaluation(
        recommendation="NO_TRADE",
        metrics=metrics,
        reason="Estimated statistical edge does not clear execution costs and uncertainty buffer.",
        is_abstention=True,
        gating_flags=gating_flags,
    )
