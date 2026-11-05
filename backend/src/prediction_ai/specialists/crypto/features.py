import math
from decimal import Decimal


def extract_btc_microstructure_features(
    prices: list[Decimal],
    bids: list[tuple[Decimal, Decimal]],  # [(price, quantity)]
    asks: list[tuple[Decimal, Decimal]],  # [(price, quantity)]
) -> dict[str, float]:
    """Calculate point-in-time microstructure features for Bitcoin."""
    if len(prices) < 2:
        return {"return_1m": 0.0, "realized_volatility": 0.0, "depth_imbalance": 0.0}

    # Vectorized return calculation
    float_prices = [float(p) for p in prices]
    log_returns = [
        math.log(float_prices[i] / float_prices[i - 1]) for i in range(1, len(float_prices))
    ]

    # Realized volatility
    mean_ret = sum(log_returns) / len(log_returns)
    variance = sum((r - mean_ret) ** 2 for r in log_returns) / max(1, len(log_returns) - 1)
    realized_vol = math.sqrt(variance)

    # Multi-level depth imbalance: (sum_bid - sum_ask) / (sum_bid + sum_ask)
    total_bid_depth = sum(float(qty) for _, qty in bids[:5]) if bids else 0.0
    total_ask_depth = sum(float(qty) for _, qty in asks[:5]) if asks else 0.0
    denom = total_bid_depth + total_ask_depth
    depth_imbalance = (total_bid_depth - total_ask_depth) / denom if denom > 0 else 0.0

    return {
        "return_latest": log_returns[-1],
        "realized_volatility": realized_vol,
        "depth_imbalance": depth_imbalance,
    }
