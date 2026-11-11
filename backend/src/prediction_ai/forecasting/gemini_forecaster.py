import json
from decimal import Decimal

import structlog
from google import genai
from google.genai import errors, types

from prediction_ai.core.config import Settings, get_settings
from prediction_ai.core.exceptions import ForecastFailedException
from prediction_ai.core.gemini_schema import build_gemini_response_schema
from prediction_ai.forecasting.blend import blend_probabilities
from prediction_ai.forecasting.schemas import (
    ForecastEvidenceItem,
    ForecastResult,
    GeminiForecastOutput,
)
from prediction_ai.markets.quote_mapping import find_outcome_quote
from prediction_ai.schemas.contract import ContractSpec
from prediction_ai.schemas.market import (
    NormalizedMarket,
    NormalizedOrderBook,
    OutcomeQuote,
)

logger = structlog.get_logger(__name__)

_AFFIRMATIVE_LABELS = frozenset(
    {"yes", "up", "higher", "over", "above", "buy up", "buy yes"}
)


def _market_implied_probability(quote: OutcomeQuote | None) -> Decimal | None:
    """Return an informative market prior, or ``None`` when none exists.

    Endpoint mid-prices (0 or 1) are not usable priors: they imply certainty and
    would collapse the blend.  A two-sided bid/ask midpoint is an acceptable
    fallback when the adapter did not populate ``mid_price``.
    """
    if quote is None:
        return None
    if quote.mid_price is not None and Decimal("0") < quote.mid_price < Decimal("1"):
        return quote.mid_price
    if quote.best_bid is not None and quote.best_ask is not None:
        midpoint = (quote.best_bid + quote.best_ask) / Decimal("2")
        if Decimal("0") < midpoint < Decimal("1"):
            return midpoint
    return None


FORECAST_SYSTEM_INSTRUCTION = """You are an elite quantitative prediction intelligence engine for binary prediction markets (Polymarket & Bayse).
Your objective is to ingest contract specifications, visual descriptions, market microstructure, technical interval patterns, and documentary evidence to output calibrated, highly reliable outcome probabilities.

Core Analytical Principles:
1. Grounding in Exact Resolution Criteria: Carefully inspect the official resolution source, closing timestamps, and edge-case rules. Ambiguous settlement terms or high tail-risk scenarios require wide uncertainty intervals or abstention recommendations.
2. Market Microstructure & Bayesian Shrinkage: The order book mid-price represents capital-backed market consensus. Treat it as your Bayesian prior. Only diverge when documentary evidence, interval pattern studies, or domain signals clearly substantiate edge.
3. Interval Pattern & Momentum Study: For short-horizon crypto or sports contracts (e.g. 15-minute or 1-hour intervals), evaluate recent movement velocity, volatility spikes, and order book imbalance.
4. Continuous Improvement & Postmortem Feedback: When past postmortem reflections are supplied, explicitly avoid the patterns that led to previous forecasting errors.
5. Absolute Precision: Never assign certainty (0.0 or 1.0). Output probabilities strictly in [0.01, 0.99] with realistic credible intervals and concrete evidence citations.
6. Schema Compliance: Return strictly a single valid JSON object adhering to the specified schema without markdown code blocks.
"""


class GeminiForecaster:
    """Probabilistic forecasting & contract synthesis agent powered by Google Gemini."""

    def __init__(
        self,
        settings: Settings,
        client: genai.Client | None = None,
    ) -> None:
        self._settings = settings
        api_key = settings.GEMINI_API_KEY.get_secret_value() if settings.GEMINI_API_KEY else None
        self._client = client or (genai.Client(api_key=api_key) if api_key else None)
        self._model = settings.GEMINI_MODEL

    async def forecast(
        self,
        market: NormalizedMarket,
        order_book: NormalizedOrderBook,
        *,
        contract_spec: ContractSpec | None = None,
        specialist_context: dict | None = None,
        screenshot_observation: dict | None = None,
    ) -> ForecastResult:
        if self._client is None:
            raise ForecastFailedException(
                "GEMINI_API_KEY is not configured. An official Google Gemini API key is required to produce calibrated probability forecasts."
            )

        yes_quote = find_outcome_quote(
            order_book,
            token_id=contract_spec.affirmative_token_id if contract_spec else None,
            outcome_id=contract_spec.affirmative_outcome_id if contract_spec else None,
            fallback_labels=_AFFIRMATIVE_LABELS,
        )
        market_implied = _market_implied_probability(yes_quote)

        prompt = self._build_prompt(
            market,
            order_book,
            market_implied,
            specialist_context,
            screenshot_observation,
        )

        config = types.GenerateContentConfig(
            system_instruction=FORECAST_SYSTEM_INSTRUCTION,
            temperature=0.1,
            response_mime_type="application/json",
            response_schema=build_gemini_response_schema(GeminiForecastOutput),
        )

        try:
            response = await self._client.aio.models.generate_content(
                model=self._model,
                contents=prompt,
                config=config,
            )
            raw_text = response.text or ""
            parsed = self._parse_output(raw_text)
            return self._blend_and_calibrate(market, order_book, market_implied, parsed, specialist_context)
        except errors.APIError as err:
            logger.error("Gemini forecasting API error", error=str(err))
            raise ForecastFailedException(f"Gemini API returned error: {err}") from err
        except Exception as err:
            logger.exception("Unexpected error in Gemini forecaster", error=str(err))
            raise ForecastFailedException(f"Forecasting calculation failed: {err}") from err

    def _build_prompt(
        self,
        market: NormalizedMarket,
        order_book: NormalizedOrderBook,
        implied_p: Decimal | None,
        context: dict | None,
        screenshot_observation: dict | None,
    ) -> str:
        prompt_parts = [
            "CONTRACT SPECIFICATION:",
            f"- Platform: {market.platform.upper()}",
            f"- Title: {market.title}",
            f"- Category: {market.category}",
            f"- Description: {market.description or 'None provided'}",
            f"- Resolution Criteria: {market.resolution_criteria or market.resolution_source or 'Standard platform resolution'}",
            f"- Closes At (UTC): {market.closes_at.isoformat() if market.closes_at else 'Unknown'}",
            f"- Resolves At (UTC): {market.resolves_at.isoformat() if market.resolves_at else 'Unknown'}",
        ]
        prompt_parts.extend([
            "",
            "LIVE ORDER BOOK DATA:",
            (
                f"- Market-Implied P(YES): {implied_p:.4f}"
                if implied_p is not None
                else "- Market-Implied P(YES): Unavailable (no executable quote; "
                "do not assume 0.50)"
            ),
        ])
        for q in order_book.quotes:
            prompt_parts.append(
                f"- Token '{q.label}': Best Bid = {q.best_bid}, Best Ask = {q.best_ask}, "
                f"Mid = {q.mid_price}, Bid Depth = ${q.depth_bid_usd:.0f}, Ask Depth = ${q.depth_ask_usd:.0f}"
            )
        if screenshot_observation:
            prompt_parts.extend([
                "",
                "HISTORICAL SCREENSHOT OBSERVATION (NOT LIVE TRUTH):",
                "These values were visible when the screenshot was captured or uploaded. "
                "Compare them with the verified contract and current context; never use "
                "them as current executable prices.",
                f"- {screenshot_observation}",
            ])
        if context:
            prompt_parts.append("")
            prompt_parts.append("DOMAIN SIGNALS & CONTEXT:")
            for k, v in context.items():
                prompt_parts.append(f"- {k}: {v}")

        prompt_parts.append("")
        prompt_parts.append("Evaluate this contract and provide your calibrated assessment in the required JSON schema.")
        return "\n".join(prompt_parts)

    def _parse_output(self, raw_text: str) -> GeminiForecastOutput:
        clean = raw_text.strip()
        if clean.startswith("```"):
            lines = clean.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            clean = "\n".join(lines).strip()
        data = json.loads(clean)
        return GeminiForecastOutput.model_validate(data)

    def _blend_and_calibrate(
        self,
        market: NormalizedMarket,
        order_book: NormalizedOrderBook,
        market_implied: Decimal | None,
        output: GeminiForecastOutput,
        specialist_context: dict | None = None,
    ) -> ForecastResult:
        llm_prob = Decimal(str(round(output.raw_probability_yes, 4)))
        settings = getattr(self, "_settings", None) or get_settings()

        # If the crypto specialist produced a math-based log-normal P(YES), use it as
        # the model anchor instead of the raw LLM guess.  The log-normal formula is
        # grounded in real OHLCV data and is more reliable than a pure LLM intuition.
        crypto_ctx = (specialist_context or {}).get("crypto_research", {})
        # ``final_adjusted_prob_yes`` is direction-aware (inverted for "below"
        # markets).  Falling back to the raw above-strike probability would
        # silently flip the forecast for every "below" contract.
        math_prob_str = crypto_ctx.get("final_adjusted_prob_yes")
        if math_prob_str is None:
            math_prob_str = crypto_ctx.get("adjusted_prob_yes")
        if math_prob_str is None:
            math_prob_str = crypto_ctx.get("base_prob_yes")
        math_prob: Decimal | None = None
        if math_prob_str is not None:
            try:
                math_prob = Decimal(str(round(float(math_prob_str), 4)))
            except (ValueError, TypeError):
                math_prob = None

        if math_prob is not None:
            blend = blend_probabilities(
                math_probability=math_prob,
                llm_probability=llm_prob,
                market_probability=market_implied,
                math_weight=Decimal(str(settings.FORECAST_CRYPTO_MATH_WEIGHT)),
                llm_weight=Decimal(str(settings.FORECAST_CRYPTO_LLM_WEIGHT)),
                market_weight=Decimal(str(settings.FORECAST_CRYPTO_MARKET_WEIGHT)),
                version=settings.FORECAST_BLEND_VERSION,
            )
        else:
            blend = blend_probabilities(
                llm_probability=llm_prob,
                market_probability=market_implied,
                llm_weight=Decimal(str(settings.FORECAST_GENERAL_LLM_WEIGHT)),
                market_weight=Decimal(str(settings.FORECAST_GENERAL_MARKET_WEIGHT)),
                version=settings.FORECAST_BLEND_VERSION,
            )

        model_raw = blend.model_probability
        p_yes = blend.probability_yes
        p_no = Decimal("1.0") - p_yes

        lower = Decimal(str(round(output.uncertainty_lower, 4)))
        upper = Decimal(str(round(output.uncertainty_upper, 4)))
        # Ensure uncertainty interval brackets p_yes
        interval_lower = min(lower, p_yes - Decimal("0.02"))
        interval_upper = max(upper, p_yes + Decimal("0.02"))
        interval_lower = max(Decimal("0.001"), interval_lower)
        interval_upper = min(Decimal("0.999"), interval_upper)

        # Merge evidence items with market microstructure evidence.  When the
        # platform has no executable quote, say so explicitly instead of
        # presenting a fabricated neutral prior as market consensus.
        evidence_items = list(output.evidence)
        if market_implied is not None:
            evidence_items.append(
                ForecastEvidenceItem(
                    evidence_type="market_microstructure",
                    stance="neutral",
                    relevance_score=Decimal("0.90"),
                    snippet=(
                        "Platform CLOB implied probability is "
                        f"{market_implied:.3f} across binary token order books."
                    ),
                    source_citation=f"{market.platform.upper()} CLOB",
                    source_url=market.url,
                )
            )
        else:
            evidence_items.append(
                ForecastEvidenceItem(
                    evidence_type="market_microstructure",
                    stance="neutral",
                    relevance_score=Decimal("0.50"),
                    snippet=(
                        "No executable platform quote was available. The market "
                        "prior is excluded from the blend; no 0.50 substitute "
                        "was used."
                    ),
                    source_citation=f"{market.platform.upper()} CLOB",
                    source_url=market.url,
                )
            )

        return ForecastResult(
            probability_yes=p_yes,
            probability_no=p_no,
            uncertainty_interval=[interval_lower, interval_upper],
            model_version=f"gemini_blended_{self._model}",
            prompt_version="forecast_gemini_v1",
            # This is a transparent, unfitted linear blend, not a fitted
            # calibration.  Naming it honestly prevents an unvalidated label
            # from being mistaken for a calibrated model.
            calibration_version=f"unfitted_{blend.version}",
            market_implied_probability=market_implied,
            reasoning=output.reasoning_summary,
            key_drivers=output.key_drivers,
            resolution_risk_flags=output.resolution_risk_flags,
            evidence=evidence_items,
            input_snapshot={
                "platform": market.platform,
                "market_id": market.platform_market_id,
                "market_implied": (
                    float(market_implied) if market_implied is not None else None
                ),
                "market_anchor_available": blend.market_available,
                "model_raw": float(model_raw),
                "blended_p": float(p_yes),
                "llm_probability": float(llm_prob),
                "math_probability": float(math_prob) if math_prob is not None else None,
                "blend_version": blend.version,
                "blend_weights": {key: float(value) for key, value in blend.weights.items()},
                "component_probabilities": {
                    key: float(value) for key, value in blend.components.items()
                },
            },
        )
