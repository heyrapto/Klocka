from datetime import UTC, datetime
from decimal import Decimal

from prediction_ai.forecasting.schemas import ForecastEvidenceItem, ForecastResult
from prediction_ai.schemas.market import NormalizedMarket, NormalizedOrderBook


class BaselineForecaster:
    """Deterministic statistical microstructure baseline forecaster."""

    def __init__(self, model_version: str = "statistical_baseline_v1") -> None:
        self.model_version = model_version

    def forecast(
        self,
        market: NormalizedMarket,
        order_book: NormalizedOrderBook,
    ) -> ForecastResult:
        # 1. Compute market implied price from quotes.  A missing quote is not
        # a neutral 0.50 prior: this baseline is a market model and cannot
        # produce a probability without an executable market reference.
        yes_quote = next(
            (q for q in order_book.quotes if q.label.strip().lower() == "yes"),
            None,
        )
        if yes_quote is None:
            yes_quote = next(
                (
                    q
                    for q in order_book.quotes
                    if q.label.strip().lower()
                    in {"up", "higher", "over", "above", "buy up", "buy yes"}
                ),
                None,
            )

        if yes_quote is not None and yes_quote.mid_price is not None:
            if Decimal("0") < yes_quote.mid_price < Decimal("1"):
                p_implied = yes_quote.mid_price
            else:
                raise ValueError(
                    "Baseline forecaster requires a market mid-price strictly "
                    "between 0 and 1; the platform returned an endpoint quote."
                )
        elif (
            yes_quote is not None
            and yes_quote.best_bid is not None
            and yes_quote.best_ask is not None
        ):
            p_implied = (yes_quote.best_bid + yes_quote.best_ask) / Decimal("2")
            if p_implied <= 0 or p_implied >= 1:
                raise ValueError(
                    "Baseline forecaster requires a market midpoint strictly "
                    "between 0 and 1; the platform returned an endpoint quote."
                )
        else:
            raise ValueError(
                "Baseline forecaster requires an executable market quote for the "
                "affirmative outcome; no 0.50 substitute is used."
            )

        # 2. Compute order book imbalance adjustment
        bid_depth = yes_quote.depth_bid_usd if yes_quote else Decimal("0")
        ask_depth = yes_quote.depth_ask_usd if yes_quote else Decimal("0")
        total_depth = bid_depth + ask_depth

        imbalance_adj = Decimal("0")
        if total_depth > Decimal("10"):
            # Imbalance in [-1, +1]
            imbalance = (bid_depth - ask_depth) / total_depth
            # Microstructure adjustment bounded by ±0.015
            imbalance_adj = Decimal("0.015") * imbalance

        p_yes = min(max(p_implied + imbalance_adj, Decimal("0.01")), Decimal("0.99"))
        p_no = Decimal("1.0") - p_yes

        # 3. Uncertainty interval based on spread and depth
        spread = Decimal("0.02")
        if yes_quote and yes_quote.best_bid is not None and yes_quote.best_ask is not None:
            spread = max(Decimal("0.01"), yes_quote.best_ask - yes_quote.best_bid)

        half_width = min(Decimal("0.20"), max(Decimal("0.03"), spread * Decimal("1.5")))
        lower = max(Decimal("0.001"), p_yes - half_width)
        upper = min(Decimal("0.999"), p_yes + half_width)

        evidence: list[ForecastEvidenceItem] = []
        if yes_quote and yes_quote.mid_price is not None:
            evidence.append(
                ForecastEvidenceItem(
                    evidence_type="market_microstructure",
                    stance="neutral",
                    relevance_score=Decimal("0.95"),
                    snippet=f"Order book mid-price for YES token is ${yes_quote.mid_price:.3f} with depth ${total_depth:.0f}.",
                    source_citation=f"{market.platform.upper()} Central Limit Order Book",
                    source_url=market.url,
                )
            )

        time_to_close_str = "Unknown"
        if market.closes_at:
            delta = market.closes_at - datetime.now(UTC)
            hours = int(delta.total_seconds() // 3600)
            time_to_close_str = f"{hours} hours remaining"
            evidence.append(
                ForecastEvidenceItem(
                    evidence_type="time_decay",
                    stance="neutral",
                    relevance_score=Decimal("0.80"),
                    snippet=f"Contract resolution scheduled for {market.closes_at.isoformat()} ({time_to_close_str}).",
                    source_citation="Platform Contract Specification",
                    source_url=market.url,
                )
            )

        return ForecastResult(
            probability_yes=p_yes,
            probability_no=p_no,
            uncertainty_interval=[lower, upper],
            model_version=self.model_version,
            prompt_version="baseline_v1",
            market_implied_probability=p_implied,
            reasoning=(
                f"Statistical microstructure baseline: Market implied probability at {p_implied:.3f} "
                f"with order-book imbalance {imbalance_adj:+.3f}. "
                f"Uncertainty interval [{lower:.3f}, {upper:.3f}] reflects order book spread of {spread:.3f}."
            ),
            key_drivers=[
                f"Market Implied Price: {p_implied:.3f}",
                f"Order Book Imbalance Adjustment: {imbalance_adj:+.3f}",
                f"Time Horizon: {time_to_close_str}",
            ],
            resolution_risk_flags=[],
            evidence=evidence,
            input_snapshot={
                "platform": market.platform,
                "market_id": market.platform_market_id,
                "mid_price": float(p_implied),
                "total_depth": float(total_depth),
            },
        )
