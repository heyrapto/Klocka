from decimal import Decimal

from prediction_ai.decision.costs import calculate_effective_cost
from prediction_ai.schemas.decision import DecisionEvaluation, DecisionMetrics

_ZERO = Decimal("0")
_ONE = Decimal("1")


def _kelly_fraction(
    fraction: Decimal,
    probability: Decimal,
    effective_cost: Decimal | None,
) -> Decimal:
    """Fractional Kelly stake for a binary contract bought at ``effective_cost``."""
    if effective_cost is None or effective_cost <= 0 or effective_cost >= _ONE:
        return _ZERO
    net_odds = (_ONE - effective_cost) / effective_cost
    if net_odds <= 0:
        return _ZERO
    raw = (net_odds * probability - (_ONE - probability)) / net_odds
    return min(max(fraction * raw, _ZERO), _ONE)


def _side_cost(
    ask: Decimal | None,
    *,
    fee_rate: Decimal,
    fee_model: str,
    slippage_bps: int,
) -> Decimal | None:
    if ask is None:
        return None
    return calculate_effective_cost(
        ask,
        taker_fee_rate=fee_rate,
        fee_model=fee_model,
        slippage_bps=slippage_bps,
    )


def evaluate_decision(
    probability_yes: Decimal,
    ask_yes: Decimal | None,
    ask_no: Decimal | None,
    min_edge: Decimal = Decimal("0.04"),
    max_spread: Decimal = Decimal("0.05"),
    spread: Decimal | None = None,
    is_data_fresh: bool = True,
    uncertainty_margin: Decimal = Decimal("0.02"),
    *,
    taker_fee_rate_yes: Decimal = Decimal("0.01"),
    taker_fee_rate_no: Decimal = Decimal("0.01"),
    fee_model: str = "variance",
    slippage_bps: int = 15,
    kelly_fraction: Decimal = Decimal("0.25"),
    quote_age_seconds: Decimal | None = None,
    max_quote_age_seconds: Decimal = Decimal("15.0"),
    depth_ask_usd_yes: Decimal | None = None,
    depth_ask_usd_no: Decimal | None = None,
    min_depth_usd: Decimal = Decimal("100.0"),
    target_order_usd: Decimal = Decimal("100.0"),
    fill_complete_yes: bool = True,
    fill_complete_no: bool = True,
    depth_known: bool = False,
    resolution_risk_flags: list[str] | None = None,
    blocking_resolution_flags: list[str] | None = None,
    block_on_resolution_risk: bool = False,
) -> DecisionEvaluation:
    """Evaluate expected value after real execution costs and risk gates.

    ``ask_yes``/``ask_no`` are optional on purpose.  The previous implementation
    substituted 0.50 for a missing quote, which silently manufactured a
    tradeable price.  A missing executable quote is now passed through as
    missing and can never become a recommendation on that side.

    Optional execution inputs default to the historical policy (1% variance
    fee, 15 bps slippage, unknown depth).  The orchestration layer supplies the
    platform-specific fee rate, fee model, quote age, and depth.
    """
    probability_no = _ONE - probability_yes
    resolution_flags = list(resolution_risk_flags or [])
    blocking_flags = list(
        blocking_resolution_flags
        if blocking_resolution_flags is not None
        else (resolution_flags if block_on_resolution_risk else [])
    )

    # An ask at or beyond the [0, 1] endpoints is not an executable quote: at
    # 0.00 it would fabricate a free contract, and at 1.00 it cannot produce a
    # positive edge.  Treat both as missing rather than pricing against them.
    invalid_quote = False
    if ask_yes is not None and (ask_yes <= _ZERO or ask_yes >= _ONE):
        ask_yes = None
        invalid_quote = True
    if ask_no is not None and (ask_no <= _ZERO or ask_no >= _ONE):
        ask_no = None
        invalid_quote = True
    invalid_quote_flags = (
        ["INVALID_EXECUTABLE_QUOTE"] if invalid_quote else []
    )

    if ask_yes is not None and ask_no is not None:
        spread_value = ask_yes + ask_no - _ONE if spread is None else spread
    else:
        spread_value = _ZERO

    effective_cost_yes = _side_cost(
        ask_yes,
        fee_rate=taker_fee_rate_yes,
        fee_model=fee_model,
        slippage_bps=slippage_bps,
    )
    effective_cost_no = _side_cost(
        ask_no,
        fee_rate=taker_fee_rate_no,
        fee_model=fee_model,
        slippage_bps=slippage_bps,
    )

    net_edge_yes = (
        probability_yes - effective_cost_yes if effective_cost_yes is not None else None
    )
    net_edge_no = probability_no - effective_cost_no if effective_cost_no is not None else None

    kelly_yes = _kelly_fraction(kelly_fraction, probability_yes, effective_cost_yes)
    kelly_no = _kelly_fraction(kelly_fraction, probability_no, effective_cost_no)

    required_threshold = min_edge + uncertainty_margin
    gating_flags: list[str] = list(invalid_quote_flags)

    depth_yes_ok = True
    depth_no_ok = True
    if depth_known:
        if depth_ask_usd_yes is None or depth_ask_usd_yes < target_order_usd:
            depth_yes_ok = False
        if depth_ask_usd_no is None or depth_ask_usd_no < target_order_usd:
            depth_no_ok = False
        if depth_ask_usd_yes is not None and not fill_complete_yes:
            depth_yes_ok = False
        if depth_ask_usd_no is not None and not fill_complete_no:
            depth_no_ok = False
        if not depth_yes_ok or not depth_no_ok:
            gating_flags.append("INSUFFICIENT_DEPTH")

    metrics = DecisionMetrics(
        executable_ask_yes=ask_yes,
        executable_ask_no=ask_no,
        effective_cost_yes=effective_cost_yes,
        effective_cost_no=effective_cost_no,
        estimated_net_edge_yes=net_edge_yes,
        estimated_net_edge_no=net_edge_no,
        minimum_required_edge=required_threshold,
        spread=spread_value,
        slippage_bps=slippage_bps,
        quarter_kelly_fraction=round(max(kelly_yes, kelly_no), 4),
        kelly_fraction_yes=round(kelly_yes, 4),
        kelly_fraction_no=round(kelly_no, 4),
        kelly_fraction_used=round(kelly_yes if kelly_yes >= kelly_no else kelly_no, 4),
        taker_fee_rate_yes=taker_fee_rate_yes,
        taker_fee_rate_no=taker_fee_rate_no,
        depth_ask_usd_yes=depth_ask_usd_yes,
        depth_ask_usd_no=depth_ask_usd_no,
        target_order_usd=target_order_usd,
        fill_complete_yes=fill_complete_yes,
        fill_complete_no=fill_complete_no,
        depth_known=depth_known,
        quote_age_seconds=quote_age_seconds,
        max_quote_age_seconds=max_quote_age_seconds,
        minimum_depth_usd=min_depth_usd,
        fee_model=fee_model,
        resolution_risk_flags=resolution_flags,
        blocking_resolution_flags=blocking_flags,
    )

    def abstain(reason: str) -> DecisionEvaluation:
        return DecisionEvaluation(
            recommendation="NO_TRADE",
            metrics=metrics,
            reason=reason,
            is_abstention=True,
            gating_flags=list(gating_flags),
        )

    # Gate 1: data freshness declared by the upstream data layer.
    if not is_data_fresh:
        gating_flags.append("DATA_STALE")
        return abstain("Critical market data feeds exceed freshness threshold.")

    # Gate 2: order-book snapshot age, independent of feed-level freshness.
    if quote_age_seconds is not None and quote_age_seconds > max_quote_age_seconds:
        gating_flags.append("STALE_QUOTE")
        return abstain(
            f"Order-book snapshot is {quote_age_seconds}s old; "
            f"maximum allowed age is {max_quote_age_seconds}s."
        )

    # Gate 3: executable quotes.  Never substitute a synthetic 0.50.
    if ask_yes is None and ask_no is None:
        gating_flags.append("MISSING_EXECUTABLE_QUOTE")
        return abstain("Neither outcome has an executable ask; no trade can be priced.")
    if ask_yes is None or ask_no is None:
        gating_flags.append("MISSING_EXECUTABLE_QUOTE")

    # Gate 4: unresolved settlement semantics.
    if block_on_resolution_risk and blocking_flags:
        gating_flags.append("RESOLUTION_RISK")
        return abstain(
            "Contract settlement semantics are unresolved: " + ", ".join(blocking_flags)
        )

    # Gate 5: maximum allowable spread.
    if ask_yes is not None and ask_no is not None and spread_value > max_spread:
        gating_flags.append("EXCESSIVE_SPREAD")
        return abstain(
            f"Market spread ({spread_value}) exceeds risk threshold ({max_spread})."
        )

    # Gate 6: depth sufficiency.  Only the illiquid side is blocked; an
    # executable side remains tradeable when depth on it is adequate.
    if depth_known and not depth_yes_ok and not depth_no_ok:
        return abstain(
            f"Neither outcome has at least ${min_depth_usd} of ask depth for a "
            f"${target_order_usd} order."
        )

    yes_tradeable = (
        ask_yes is not None
        and net_edge_yes is not None
        and net_edge_yes >= required_threshold
        and (not depth_known or depth_yes_ok)
    )
    no_tradeable = (
        ask_no is not None
        and net_edge_no is not None
        and net_edge_no >= required_threshold
        and (not depth_known or depth_no_ok)
    )

    # Gate 7: expected value after fees and slippage.
    if yes_tradeable:
        metrics.kelly_fraction_used = round(kelly_yes, 4)
        return DecisionEvaluation(
            recommendation="BUY_YES",
            metrics=metrics,
            reason=(
                f"Positive net edge on YES ({net_edge_yes:.3f}) exceeds "
                f"threshold ({required_threshold:.3f})."
            ),
            is_abstention=False,
            gating_flags=list(gating_flags),
        )
    if no_tradeable:
        metrics.kelly_fraction_used = round(kelly_no, 4)
        return DecisionEvaluation(
            recommendation="BUY_NO",
            metrics=metrics,
            reason=(
                f"Positive net edge on NO ({net_edge_no:.3f}) exceeds "
                f"threshold ({required_threshold:.3f})."
            ),
            is_abstention=False,
            gating_flags=list(gating_flags),
        )

    gating_flags.append("INSUFFICIENT_EDGE")
    return abstain(
        "Estimated statistical edge does not clear execution costs and uncertainty buffer."
    )
