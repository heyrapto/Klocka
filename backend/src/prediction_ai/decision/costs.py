"""Execution-cost models and order-book walking.

The platform fee schedules are not flat percentages.  Polymarket uses a
variance fee ``feeRate * p * (1 - p)``; Bayse uses
``feeRate * p * max(1 - p, 0.5)``.  Treating both as a flat 1% materially
understates the cost of trading near 50 cents, which is exactly where most
short-horizon crypto contracts live.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal

from prediction_ai.schemas.market import BookLevel


def fee_per_share(
    price: Decimal,
    taker_fee_rate: Decimal,
    fee_model: str = "variance",
) -> Decimal:
    """Return the taker fee in quote currency per share at ``price``."""
    if price <= 0 or price >= 1 or taker_fee_rate <= 0:
        return Decimal("0.0")

    model = fee_model.strip().lower()
    if model in {"none", "zero", "fee_free"}:
        return Decimal("0.0")
    if model in {"variance", "polymarket"}:
        return taker_fee_rate * price * (Decimal("1.0") - price)
    if model in {"variance_floor_half", "bayse"}:
        return taker_fee_rate * price * max(Decimal("1.0") - price, Decimal("0.5"))
    # Unknown models are treated conservatively: never understate the fee.
    return taker_fee_rate * price * max(Decimal("1.0") - price, Decimal("0.5"))


def calculate_effective_cost(
    price: Decimal,
    *,
    taker_fee_rate: Decimal = Decimal("0.01"),
    fee_model: str = "variance",
    slippage_bps: int = 15,
) -> Decimal:
    """Effective all-in cost per share, including fees and slippage."""
    slippage_rate = Decimal(slippage_bps) / Decimal("10000")
    slippage = price * slippage_rate
    return price + fee_per_share(price, taker_fee_rate, fee_model) + slippage


def calculate_effective_costs(
    ask_yes: Decimal,
    ask_no: Decimal,
    taker_fee_rate: Decimal = Decimal("0.01"),
    slippage_bps: int = 15,
    fee_model: str = "variance",
    taker_fee_rate_no: Decimal | None = None,
) -> tuple[Decimal, Decimal]:
    """Backward-compatible pair helper for YES and NO effective costs."""
    fee_no = taker_fee_rate if taker_fee_rate_no is None else taker_fee_rate_no
    return (
        calculate_effective_cost(
            ask_yes,
            taker_fee_rate=taker_fee_rate,
            fee_model=fee_model,
            slippage_bps=slippage_bps,
        ),
        calculate_effective_cost(
            ask_no,
            taker_fee_rate=fee_no,
            fee_model=fee_model,
            slippage_bps=slippage_bps,
        ),
    )


@dataclass(frozen=True)
class BookWalk:
    average_price: Decimal | None
    shares: Decimal
    filled_notional: Decimal
    complete: bool


def walk_book_for_notional(
    levels: Sequence[BookLevel] | Iterable[tuple[Decimal, Decimal]],
    notional_usd: Decimal,
    *,
    limit_price: Decimal | None = None,
) -> BookWalk:
    """Walk an ask ladder to estimate the VWAP for a target notional.

    ``levels`` may be ``BookLevel`` objects or ``(price, size)`` tuples.  The
    size is interpreted as shares.  The result is used for the depth gate and
    for decision diagnostics; it is not an execution guarantee.
    """
    if notional_usd <= 0:
        return BookWalk(None, Decimal("0"), Decimal("0"), True)

    remaining = notional_usd
    shares = Decimal("0")
    cost = Decimal("0")

    for level in levels:
        if isinstance(level, BookLevel):
            price, size = level.price, level.size
        else:
            price, size = level
        if price <= 0 or size <= 0:
            continue
        if limit_price is not None and price > limit_price:
            break
        level_notional = price * size
        take_notional = min(remaining, level_notional)
        take_shares = take_notional / price
        shares += take_shares
        cost += take_notional
        remaining -= take_notional
        if remaining <= 0:
            break

    complete = remaining <= 0
    average_price = (cost / shares) if shares > 0 else None
    return BookWalk(
        average_price=average_price,
        shares=shares,
        filled_notional=cost,
        complete=complete,
    )
