import json
from decimal import Decimal

import structlog
from google import genai
from google.genai import errors, types

from prediction_ai.core.config import Settings
from prediction_ai.core.exceptions import ForecastFailedException
from prediction_ai.core.gemini_schema import build_gemini_response_schema
from prediction_ai.forecasting.schemas import (
    ForecastEvidenceItem,
    ForecastResult,
    GeminiForecastOutput,
)
from prediction_ai.schemas.market import NormalizedMarket, NormalizedOrderBook

logger = structlog.get_logger(__name__)

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
        specialist_context: dict | None = None,
        screenshot_observation: dict | None = None,
    ) -> ForecastResult:
        if self._client is None:
            raise ForecastFailedException(
                "GEMINI_API_KEY is not configured. An official Google Gemini API key is required to produce calibrated probability forecasts."
            )

        yes_quote = next(
            (
                q
                for q in order_book.quotes
                if q.label.strip().lower() in ("yes", "up", "higher", "over")
            ),
            None,
        )
        if yes_quote is None and len(order_book.quotes) == 2:
            # Default first outcome of binary pair to YES/Up
            yes_quote = order_book.quotes[0]

        market_implied = yes_quote.mid_price if (yes_quote and yes_quote.mid_price) else Decimal("0.50")

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
        implied_p: Decimal,
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
            f"- Market-Implied P(YES): {implied_p:.4f}",
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
        market_implied: Decimal,
        output: GeminiForecastOutput,
        specialist_context: dict | None = None,
    ) -> ForecastResult:
        model_raw = Decimal(str(round(output.raw_probability_yes, 4)))

        # If the crypto specialist produced a math-based log-normal P(YES), use it as
        # the model anchor instead of the raw LLM guess.  The log-normal formula is
        # grounded in real OHLCV data and is more reliable than a pure LLM intuition.
        crypto_ctx = (specialist_context or {}).get("crypto_research", {})
        math_prob_str = crypto_ctx.get("adjusted_prob_yes") or crypto_ctx.get("base_prob_yes")
        if math_prob_str is not None:
            try:
                math_prob = Decimal(str(round(float(math_prob_str), 4)))
                # Blend: 50% math model, 15% LLM interpretation, 35% market
                model_raw = (Decimal("0.50") * math_prob) + (Decimal("0.15") * model_raw)
            except (ValueError, TypeError):
                pass  # Fallback to plain LLM probability if parsing fails

        # Bayesian shrinkage: 65% model evidence / 35% market prior.
        # Increased model weight because we now supply real data signals that
        # the LLM integrates into its probability estimate — not just intuition.
        blended_p = (Decimal("0.65") * model_raw) + (Decimal("0.35") * market_implied)
        p_yes = min(max(blended_p, Decimal("0.01")), Decimal("0.99"))
        p_no = Decimal("1.0") - p_yes

        lower = Decimal(str(round(output.uncertainty_lower, 4)))
        upper = Decimal(str(round(output.uncertainty_upper, 4)))
        # Ensure uncertainty interval brackets p_yes
        interval_lower = min(lower, p_yes - Decimal("0.02"))
        interval_upper = max(upper, p_yes + Decimal("0.02"))
        interval_lower = max(Decimal("0.001"), interval_lower)
        interval_upper = min(Decimal("0.999"), interval_upper)

        # Merge evidence items with market microstructure evidence
        evidence_items = list(output.evidence)
        evidence_items.append(
            ForecastEvidenceItem(
                evidence_type="market_microstructure",
                stance="neutral",
                relevance_score=Decimal("0.90"),
                snippet=f"Platform CLOB implied probability is {market_implied:.3f} across binary token order books.",
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
            calibration_version="bayesian_shrinkage_v2",
            market_implied_probability=market_implied,
            reasoning=output.reasoning_summary,
            key_drivers=output.key_drivers,
            resolution_risk_flags=output.resolution_risk_flags,
            evidence=evidence_items,
            input_snapshot={
                "platform": market.platform,
                "market_id": market.platform_market_id,
                "market_implied": float(market_implied),
                "model_raw": float(model_raw),
                "blended_p": float(p_yes),
            },
        )
