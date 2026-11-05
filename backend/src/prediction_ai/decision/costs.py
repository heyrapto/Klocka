from decimal import Decimal


def calculate_effective_costs(
    ask_yes: Decimal,
    ask_no: Decimal,
    taker_fee_rate: Decimal = Decimal("0.01"),  # 1% platform fee
    slippage_bps: int = 15,  # 15 bps slippage allowance
) -> tuple[Decimal, Decimal]:
    """Calculate effective entry costs including fees and expected execution slippage."""
    slippage_rate = Decimal(slippage_bps) / Decimal("10000")

    effective_cost_yes = ask_yes * (Decimal("1.0") + taker_fee_rate + slippage_rate)
    effective_cost_no = ask_no * (Decimal("1.0") + taker_fee_rate + slippage_rate)

    return effective_cost_yes, effective_cost_no
