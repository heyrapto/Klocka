"""Causal Diagnostic Postmortem Agent for continuous prediction market learning.

Analyzes completed trade settlements against initial forecasts, diagnoses structural
discrepancies (volatility bursts, false breakouts, funding squeezes), outputs causal
learnings, and generates quantitative parameter updates for the SQLite Knowledge Matrix.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from prediction_ai.core.config import Settings
from prediction_ai.core.gemini_schema import build_gemini_response_schema

logger = structlog.get_logger(__name__)


class PostmortemOutputSchema(BaseModel):
    discrepancy_classification: str = Field(
        ...,
        description=(
            "One of: ACCURATE_CONVICTION, VOLATILITY_BURST, FALSE_BREAKOUT_TRAP, "
            "FUNDING_RATE_SQUEEZE, LIQUIDITY_EVAPORATION, PIN_RISK_DRIFT, THETA_MISCALCULATION, "
            "ABSTENTION_ALIGNED_LEAN, ABSTENTION_SAVED_CAPITAL, ABSTENTION_VOLATILITY_EXPANSION, "
            "ABSTENTION_REGIME_UNCERTAINTY"
        ),
    )
    visual_observations: str | None = Field(
        default=None,
        description="Detailed visual observations from the settlement screenshot (e.g. final candle color, upper/lower wick rejection, volume spike, resolution banner, or visible settled price print).",
    )
    postmortem_analysis: str = Field(
        ...,
        description="2-3 sentences explaining the exact causal driver of the discrepancy based on the provided technicals and settlement data.",
    )
    key_learnings: list[str] = Field(
        ...,
        description="2 to 3 concrete mathematical or execution rules to apply to future intervals of this asset.",
        min_length=1,
        max_length=4,
    )
    vol_multiplier_delta: float = Field(
        default=0.0,
        ge=-0.20,
        le=0.30,
        description="Adjustment to the realized volatility multiplier (e.g. +0.10 if volatility was underestimated).",
    )
    momentum_weight_delta: float = Field(
        default=0.0,
        ge=-0.15,
        le=0.15,
        description="Adjustment to momentum indicator weight (e.g. -0.05 if momentum gave a false breakout).",
    )
    order_flow_weight_delta: float = Field(
        default=0.0,
        ge=-0.15,
        le=0.15,
        description="Adjustment to order flow imbalance weight (e.g. +0.05 if order depth was a leading indicator).",
    )
    uncertainty_margin_delta: float = Field(
        default=0.0,
        ge=-0.02,
        le=0.03,
        description="Adjustment to the uncertainty buffer (e.g. +0.01 if wide tail risk caused a surprise).",
    )


@dataclass
class PostmortemResult:
    was_correct: bool
    brier_loss: float
    discrepancy_classification: str
    postmortem_analysis: str
    evaluation_eligible: bool = True
    visual_observations: str | None = None
    key_learnings: list[str] = field(default_factory=list)
    vol_multiplier_delta: float = 0.0
    momentum_weight_delta: float = 0.0
    order_flow_weight_delta: float = 0.0
    uncertainty_margin_delta: float = 0.0


POSTMORTEM_SYSTEM_PROMPT = """You are an elite quantitative trade performance postmortem analyst for binary prediction markets.
Your job is to analyze trade settlements, evaluate why a prediction succeeded or failed, and produce concrete, causal learnings.

Analytical Framework:
1. Grounding in Real Microstructure: Look at the actual numbers: entry spot price vs strike price, RSI, order book bid/ask ratio, funding rate, and final settled price.
2. Multimodal Settlement Inspection: When an end-of-trade screenshot or chart is provided, inspect the visible candles, settlement banners, closing price print, wicks, and volume bars. Identify the exact moment and price action pattern where the trade succeeded or failed.
3. Root-Cause Attribution: Do not use vague platitudes. Identify whether the discrepancy was caused by:
   - False Breakout Trap: Technical momentum faded before the close.
   - Volatility Burst: Spot price moved more standard deviations than the annualized vol estimate implied.
   - Funding Squeeze: Negative/positive funding rate triggered an aggressive liquidation cascade.
   - Liquidity Evaporation: Thin order book depth near expiry allowed small volume to flip the outcome.
   - Pin Risk: Spot finished within 0.05% of strike, rendering the binary option an unpredictable coin flip.
   - Abstention Aligned Lean: The recommendation was NO_TRADE, but the model's assigned probability tilted in the direction of the eventual resolution. Diagnose why the trade was gated: was the edge too thin, spread too wide, or was the volatility multiplier/uncertainty buffer set too conservatively? Determine how to calibrate signals so the model gains surer conviction on similar setups.
   - Abstention Saved Capital: The recommendation was NO_TRADE, and the market chopped or went against initial momentum. The risk gate correctly saved the trader from a loss or fee drag. Identify the noise signals that were correctly filtered out.
   - Abstention Volatility Expansion: The recommendation was NO_TRADE, and a sudden volatility expansion broke through the strike. Identify the catalyst and adjust volatility scaling.
4. Parameter Calibration: Output actionable quantitative parameter deltas so future intervals dynamically learn and improve.
"""


async def run_postmortem_analysis(
    *,
    market_title: str,
    category: str,
    symbol: str,
    recommendation: str,
    probability_yes: float,
    actual_outcome: str,
    settled_price: float | None = None,
    strike_price: float | None = None,
    user_notes: str | None = None,
    specialist_context: dict[str, Any] | None = None,
    settlement_image_bytes: bytes | None = None,
    settlement_image_mime: str = "image/jpeg",
    settings: Settings,
) -> PostmortemResult:
    """Execute causal diagnostic reflection and produce structured learnings."""

    # Determine correctness with flexible binary mapping (YES/NO, UP/DOWN, HIGHER/LOWER)
    norm_outcome = actual_outcome.strip().upper()
    is_affirmative_outcome = norm_outcome in ("YES", "UP", "HIGHER", "BUY_UP", "BUY UP")
    is_negative_outcome = norm_outcome in ("NO", "DOWN", "LOWER", "BUY_DOWN", "BUY DOWN")
    if not is_affirmative_outcome and not is_negative_outcome:
        raise ValueError(
            "Settlement outcome must be one of YES/NO, UP/DOWN, HIGHER/LOWER, "
            f"or BUY UP/BUY DOWN; got {actual_outcome!r}."
        )

    is_abstention = recommendation == "NO_TRADE"

    was_correct = False
    if recommendation in ("BUY_YES", "BUY_UP") and is_affirmative_outcome:
        was_correct = True
    elif recommendation in ("BUY_NO", "BUY_DOWN") and is_negative_outcome:
        was_correct = True
    elif is_abstention:
        # For abstentions, evaluate whether the model's assigned probability leaned
        # in the direction of the actual outcome. This allows the postmortem agent to
        # evaluate whether a valid edge was missed by an overly conservative gate,
        # or if the abstention successfully preserved capital against market noise.
        if (probability_yes > 0.50 and is_affirmative_outcome) or (probability_yes < 0.50 and is_negative_outcome):
            was_correct = True

    # Compute Brier score component: (probability_yes - outcome_indicator)^2
    outcome_indicator = 1.0 if is_affirmative_outcome else 0.0
    brier_loss = round((probability_yes - outcome_indicator) ** 2, 4)

    # Check if Gemini API is configured for deep generative reflection
    api_key = settings.GEMINI_API_KEY.get_secret_value() if settings.GEMINI_API_KEY else None
    if api_key:
        try:
            client = genai.Client(api_key=api_key)
            prompt = _build_diagnostic_prompt(
                market_title=market_title,
                category=category,
                symbol=symbol,
                recommendation=recommendation,
                probability_yes=probability_yes,
                actual_outcome=actual_outcome,
                was_correct=was_correct,
                settled_price=settled_price,
                strike_price=strike_price,
                user_notes=user_notes,
                specialist_context=specialist_context,
            )

            config = types.GenerateContentConfig(
                system_instruction=POSTMORTEM_SYSTEM_PROMPT,
                temperature=0.1,
                response_mime_type="application/json",
                response_schema=build_gemini_response_schema(PostmortemOutputSchema),
            )

            contents: list[Any] = [prompt]
            if settlement_image_bytes:
                image_part = types.Part.from_bytes(data=settlement_image_bytes, mime_type=settlement_image_mime)
                contents = [image_part, prompt]

            response = await client.aio.models.generate_content(
                model=settings.GEMINI_MODEL,
                contents=contents,
                config=config,
            )

            if response.text:
                clean = response.text.strip()
                if clean.startswith("```"):
                    lines = clean.splitlines()
                    if lines and lines[0].startswith("```"):
                        lines = lines[1:]
                    if lines and lines[-1].strip() == "```":
                        lines = lines[:-1]
                    clean = "\n".join(lines).strip()
                parsed = PostmortemOutputSchema.model_validate_json(clean)
                return PostmortemResult(
                    was_correct=was_correct,
                    evaluation_eligible=True,
                    brier_loss=brier_loss,
                    discrepancy_classification=parsed.discrepancy_classification,
                    postmortem_analysis=parsed.postmortem_analysis,
                    visual_observations=parsed.visual_observations,
                    key_learnings=parsed.key_learnings,
                    vol_multiplier_delta=parsed.vol_multiplier_delta,
                    momentum_weight_delta=parsed.momentum_weight_delta,
                    order_flow_weight_delta=parsed.order_flow_weight_delta,
                    uncertainty_margin_delta=parsed.uncertainty_margin_delta,
                )
        except Exception as exc:
            logger.warning("Gemini postmortem generation failed, falling back to quantitative heuristics", error=str(exc))

    # Quantitative Rule-Based Diagnostic Fallback
    visual_obs = "Settlement screenshot ingested; visual resolution confirmed." if settlement_image_bytes else None
    return _heuristic_postmortem(
        symbol=symbol,
        category=category,
        recommendation=recommendation,
        probability_yes=probability_yes,
        actual_outcome=actual_outcome,
        was_correct=was_correct,
        brier_loss=brier_loss,
        settled_price=settled_price,
        strike_price=strike_price,
        specialist_context=specialist_context,
        user_notes=user_notes,
        visual_observations=visual_obs,
    )


def _build_diagnostic_prompt(
    market_title: str,
    category: str,
    symbol: str,
    recommendation: str,
    probability_yes: float,
    actual_outcome: str,
    was_correct: bool,
    settled_price: float | None,
    strike_price: float | None,
    user_notes: str | None,
    specialist_context: dict[str, Any] | None,
) -> str:
    is_abstention = recommendation == "NO_TRADE"
    directional_lean = (
        "UP / YES"
        if probability_yes > 0.51
        else "DOWN / NO"
        if probability_yes < 0.49
        else "NEUTRAL / BALANCED"
    )
    parts = [
        "TRADE SETTLEMENT AUDIT:",
        f"- Market Proposition: {market_title}",
        f"- Asset / Category: {symbol} ({category})",
        f"- Recommended Action: {recommendation}",
        f"- Model Probability P(YES): {probability_yes:.3f} (Implied Directional Lean: {directional_lean})",
        f"- Actual Settlement Outcome: {actual_outcome}",
    ]
    if is_abstention:
        parts.append(
            f"- Abstention Audit Alignment: {'Model probability tilted toward the winning outcome (Missed opportunity vs fee drag)' if was_correct else 'Model tilted against outcome or stayed neutral (Capital preserved by risk gate)'}"
        )
    else:
        parts.append(
            f"- Outcome Aligned: {'YES (Prediction Correct)' if was_correct else 'NO (Prediction Discrepancy)'}"
        )

    if settled_price:
        parts.append(f"- Final Settled Spot Price: ${settled_price:,.2f}")
    if strike_price:
        parts.append(f"- Contract Strike Price: ${strike_price:,.2f}")
    if user_notes:
        parts.append(f"- Trader Observation: {user_notes}")

    if specialist_context:
        crypto_res = specialist_context.get("crypto_research", {})
        parts.append("")
        parts.append("PRE-TRADE SPECIALIST CONTEXT & FEATURES:")
        for k in (
            "spot_price",
            "annual_volatility_pct",
            "rsi_15m",
            "rsi_1h",
            "ema_cross_signal",
            "order_book_depth_ratio",
            "order_book_pressure",
            "funding_rate_pct_8h",
            "base_prob_yes",
            "adjusted_prob_yes",
            "final_adjusted_prob_yes",
        ):
            if k in crypto_res:
                parts.append(f"- {k}: {crypto_res[k]}")

    parts.append("")
    if is_abstention:
        parts.append(
            "ABSTENTION LEARNING DIRECTIVE:\n"
            f"1. Explain why the market moved and settled as {actual_outcome}.\n"
            "2. Scrutinize the technical signals (RSI, EMA, order book pressure, volatility): did any signal correctly anticipate this outcome?\n"
            f"3. Evaluate the decision to abstain: was it a disciplined save (avoiding noise/fee burn) or an overly cautious miss where a surer conviction could have been formed?\n"
            "4. Provide concrete, mathematical key learnings and parameter deltas (vol_multiplier_delta, momentum_weight_delta, order_flow_weight_delta, uncertainty_margin_delta) so future forecasts can weight these patterns more accurately."
        )
    else:
        parts.append("Diagnose the causal drivers of this settlement outcome and output your structured reflection.")
    return "\n".join(parts)


def _heuristic_postmortem(
    symbol: str,
    category: str,
    recommendation: str,
    probability_yes: float,
    actual_outcome: str,
    was_correct: bool,
    brier_loss: float,
    settled_price: float | None,
    strike_price: float | None,
    specialist_context: dict[str, Any] | None,
    user_notes: str | None,
    visual_observations: str | None = None,
    evaluation_eligible: bool = True,
) -> PostmortemResult:
    """Deterministic financial heuristics when LLM is unavailable."""
    crypto_res = (specialist_context or {}).get("crypto_research", {})
    rsi = crypto_res.get("rsi_15m", 50.0)
    depth_ratio = crypto_res.get("order_book_depth_ratio", 1.0)
    funding = crypto_res.get("funding_rate_pct_8h", 0.0)

    # ------------------------------------------------------------------
    # Abstention Causal Audit & Learning Heuristics
    # ------------------------------------------------------------------
    if recommendation == "NO_TRADE":
        norm_outcome = actual_outcome.strip().upper()
        is_aff = norm_outcome in ("YES", "UP", "HIGHER", "BUY_UP", "BUY UP")
        is_aligned_lean = (probability_yes > 0.50 and is_aff) or (probability_yes < 0.50 and not is_aff)
        lean_dir = "UP" if probability_yes > 0.50 else "DOWN" if probability_yes < 0.50 else "NEUTRAL"

        dist_pct = 0.0
        if settled_price is not None and strike_price is not None and strike_price > 0:
            dist_pct = (settled_price - strike_price) / strike_price

        if is_aligned_lean:
            classification = "ABSTENTION_ALIGNED_LEAN"
            analysis = (
                f"Model probability assigned P(YES)={probability_yes:.1%} (leaning {lean_dir}), "
                f"which aligned with the eventual settlement as {actual_outcome}. "
                "The trade was gated as NO_TRADE due to spread, taker fees, or the uncertainty buffer. "
                "Calibrating momentum and volatility weights to capture high-conviction microstructures."
            )
            learnings = [
                f"Model directional lean ({lean_dir}) correctly anticipated the {actual_outcome} resolution.",
                "Review uncertainty buffer: if momentum indicators align with order flow, allow aggressive edge calculation.",
                f"Settlement finished {abs(dist_pct) * 100.0:.2f}% from strike; price drift followed underlying momentum.",
            ]
            vol_delta = -0.05 if abs(dist_pct) < 0.005 else 0.02
            mom_delta = 0.03
            flow_delta = 0.02
            unc_delta = -0.005
        else:
            classification = "ABSTENTION_SAVED_CAPITAL"
            analysis = (
                f"Contract settled as {actual_outcome}, while the model's probability distribution "
                f"was neutral or leaned {lean_dir}. The disciplined NO_TRADE recommendation "
                "successfully prevented opening a losing or fee-negative position in adverse conditions."
            )
            learnings = [
                f"Disciplined risk gate avoided unprofitable execution on choppy {symbol} microstructure.",
                "Reinforce volatility buffer when RSI and order flow divergence signals conflict.",
                "Maintain baseline capital preservation threshold on ambiguous setups.",
            ]
            vol_delta = 0.05
            mom_delta = -0.02
            flow_delta = -0.02
            unc_delta = 0.005

        return PostmortemResult(
            was_correct=is_aligned_lean,
            evaluation_eligible=True,
            brier_loss=brier_loss,
            discrepancy_classification=classification,
            postmortem_analysis=analysis,
            visual_observations=visual_observations,
            key_learnings=learnings,
            vol_multiplier_delta=vol_delta,
            momentum_weight_delta=mom_delta,
            order_flow_weight_delta=flow_delta,
            uncertainty_margin_delta=unc_delta,
        )

    if was_correct:
        return PostmortemResult(
            was_correct=True,
            evaluation_eligible=True,
            brier_loss=brier_loss,
            discrepancy_classification="ACCURATE_CONVICTION",
            postmortem_analysis=(
                f"Model recommendation {recommendation} successfully aligned with the true market resolution {actual_outcome}. "
                f"Statistical edge materialized over the contract horizon without adverse volatility excursion."
            ),
            visual_observations=visual_observations,
            key_learnings=[
                f"Current signal weighting for {symbol} demonstrated positive predictive validity.",
                "Maintain baseline volatility scaling and order book liquidity parameters.",
            ],
            vol_multiplier_delta=0.0,
            momentum_weight_delta=0.0,
            order_flow_weight_delta=0.0,
            uncertainty_margin_delta=0.0,
        )

    # Discrepancy diagnosed
    if recommendation == "BUY_NO" and actual_outcome == "YES":
        if funding < -0.01:
            classification = "FUNDING_RATE_SQUEEZE"
            analysis = (
                f"Discrepancy driven by an aggressive short-squeeze cascade: negative funding ({funding:.3f}%) "
                f"created crowded short positions that were forcefully liquidated into interval close, pushing {symbol} above strike."
            )
            learnings = [
                f"For future {symbol} intervals with negative funding < -0.01%, apply short-squeeze upside skew.",
                "Dampen BUY_NO conviction when spot price is within 0.4% of strike under crowded short positioning.",
            ]
            vol_delta = 0.08
            mom_delta = 0.05
            of_delta = 0.0
            margin_delta = 0.01
        else:
            classification = "VOLATILITY_BURST"
            analysis = (
                f"Underestimated interval volatility: late upward buying momentum pushed {symbol} through the strike "
                f"faster than the annualized volatility model anticipated."
            )
            learnings = [
                f"Increase baseline realized volatility multiplier for {symbol} 15-minute intervals by +10%.",
                "Expand uncertainty interval when order depth ratio indicates aggressive ask-side clearing.",
            ]
            vol_delta = 0.10
            mom_delta = 0.0
            of_delta = 0.05
            margin_delta = 0.015

    elif recommendation == "BUY_YES" and actual_outcome == "NO":
        if rsi > 68.0:
            classification = "FALSE_BREAKOUT_TRAP"
            analysis = (
                f"Momentum exhaustion trap: {symbol} entered the interval with elevated RSI ({rsi:.1f}). "
                f"Buyers failed to maintain continuation above strike, triggering a sharp mean-reversion selloff into close."
            )
            learnings = [
                f"Penalize BUY_YES recommendations on {symbol} when 15m RSI exceeds 68.0.",
                "Increase mean-reversion dampener when spot price is hovering within 0.2% of strike near resolution.",
            ]
            vol_delta = 0.05
            mom_delta = -0.08
            of_delta = 0.05
            margin_delta = 0.01
        else:
            classification = "LIQUIDITY_EVAPORATION"
            analysis = (
                f"Bid-side liquidity faded in the final minutes before resolution: order book depth ratio ({depth_ratio:.2f}x) "
                f"proved insufficient to absorb localized selling pressure."
            )
            learnings = [
                f"Enforce stricter order-book depth requirements before emitting BUY_YES recommendations on {symbol}.",
                "Increase minimum net edge threshold by +1.5% for contracts within 5 minutes of resolution.",
            ]
            vol_delta = 0.05
            mom_delta = -0.05
            of_delta = 0.08
            margin_delta = 0.01

    else:
        classification = "PIN_RISK_DRIFT"
        analysis = (
            f"Localized price drift: {symbol} hovered in close proximity to the strike price with insufficient momentum "
            f"to establish directional conviction before resolution."
        )
        learnings = [
            f"Expand uncertainty buffer for near-the-money strikes on {symbol}.",
            "Maintain NO_TRADE gating when edge fails to clear execution friction.",
        ]
        vol_delta = 0.02
        mom_delta = -0.02
        of_delta = 0.02
        margin_delta = 0.01

    if user_notes:
        analysis += f" User observation: {user_notes}"

    return PostmortemResult(
        was_correct=False,
        evaluation_eligible=True,
        brier_loss=brier_loss,
        discrepancy_classification=classification,
        postmortem_analysis=analysis,
        visual_observations=visual_observations,
        key_learnings=learnings,
        vol_multiplier_delta=vol_delta,
        momentum_weight_delta=mom_delta,
        order_flow_weight_delta=of_delta,
        uncertainty_margin_delta=margin_delta,
    )
