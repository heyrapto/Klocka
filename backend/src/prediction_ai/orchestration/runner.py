import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import httpx
import structlog

from prediction_ai.core.config import Settings, get_settings
from prediction_ai.core.workflow import AnalysisStatus, EventType, Stage
from prediction_ai.db.mongo import get_mongo_db
from prediction_ai.db.sqlite import get_sqlite_db
from prediction_ai.decision.costs import walk_book_for_notional
from prediction_ai.decision.engine import evaluate_decision
from prediction_ai.forecasting.gemini_forecaster import GeminiForecaster
from prediction_ai.integrations.bayse import BayseAdapter
from prediction_ai.integrations.http import ProviderHttpClient
from prediction_ai.integrations.network import build_resilient_client
from prediction_ai.integrations.polymarket import PolymarketAdapter
from prediction_ai.markets.contract_spec import build_contract_spec, resolve_contract_direction
from prediction_ai.markets.matcher import match_candidate_to_platform_market
from prediction_ai.markets.quote_mapping import find_outcome_quote as _find_outcome_quote
from prediction_ai.orchestration.events import EventRecorder
from prediction_ai.schemas.analysis import ScreenshotMarketCandidate
from prediction_ai.schemas.contract import ContractSpec
from prediction_ai.schemas.market import (
    NormalizedMarket,
    NormalizedOutcome,
)
from prediction_ai.specialists.research import SpecialistResearchService
from prediction_ai.storage.factory import create_storage
from prediction_ai.vision.factory import create_vision_extractor
from prediction_ai.vision.images import inspect_image

logger = structlog.get_logger(__name__)

_AFFIRMATIVE_LABELS = frozenset({"yes", "up", "higher", "over", "above", "buy up", "buy yes"})
_NEGATIVE_LABELS = frozenset({"no", "down", "lower", "under", "below", "buy down", "buy no"})


def _quote_age_seconds(observed_at: datetime, decision_at: datetime) -> Decimal:
    observed = observed_at if observed_at.tzinfo is not None else observed_at.replace(tzinfo=UTC)
    decision = decision_at if decision_at.tzinfo is not None else decision_at.replace(tzinfo=UTC)
    return Decimal(str(round(max(0.0, (decision - observed).total_seconds()), 3)))


def _market_outcome(
    market: NormalizedMarket,
    *,
    outcome_id: str | None,
    token_id: str | None,
) -> NormalizedOutcome | None:
    """Resolve one market outcome by ID, falling back to its token ID."""
    if outcome_id:
        match = next(
            (item for item in market.outcomes if item.outcome_id == outcome_id),
            None,
        )
        if match is not None:
            return match
    if token_id:
        return next(
            (item for item in market.outcomes if item.token_id == token_id),
            None,
        )
    return None


def _build_target_definition(
    market: NormalizedMarket,
    contract_spec: ContractSpec,
) -> dict[str, Any]:
    """Describe the exact event that ``probability_yes`` refers to.

    The affirmative/negative ordering is explicit because a generic
    ``["YES", "NO"]`` label loses the UP/DOWN direction used by crypto
    interval contracts.
    """
    affirmative = _market_outcome(
        market,
        outcome_id=contract_spec.affirmative_outcome_id,
        token_id=contract_spec.affirmative_token_id,
    )
    negative = _market_outcome(
        market,
        outcome_id=contract_spec.negative_outcome_id,
        token_id=contract_spec.negative_token_id,
    )
    mapping_complete = affirmative is not None and negative is not None
    if mapping_complete:
        assert affirmative is not None and negative is not None
        outcome_labels = [affirmative.label, negative.label]
    else:
        outcome_labels = [item.label for item in market.outcomes]

    return {
        "question": market.title,
        "outcomes": outcome_labels,
        "outcome_mapping_complete": mapping_complete,
        "affirmative_label": affirmative.label if affirmative is not None else None,
        "negative_label": negative.label if negative is not None else None,
        "affirmative_outcome_id": contract_spec.affirmative_outcome_id,
        "negative_outcome_id": contract_spec.negative_outcome_id,
        "affirmative_token_id": contract_spec.affirmative_token_id,
        "negative_token_id": contract_spec.negative_token_id,
        "direction": contract_spec.direction,
        "comparator": contract_spec.comparator,
        "strike_value": (
            str(contract_spec.strike_value)
            if contract_spec.strike_value is not None
            else None
        ),
        "strike_source": contract_spec.strike_source,
        "settlement_source": contract_spec.settlement_source,
        "settlement_timestamp": (
            contract_spec.settlement_timestamp.isoformat()
            if contract_spec.settlement_timestamp is not None
            else None
        ),
        "tradeable": contract_spec.tradeable,
        "contract_spec_hash": contract_spec.spec_hash,
    }


class AnalysisWorkflowRunner:
    """Production asynchronous pipeline runner executing market verification, forecasting, and EV decisioning."""

    def __init__(self, settings: Settings | None = None, http_client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings or get_settings()
        self._external_client = http_client

    async def run(self, analysis_id: UUID) -> None:
        """Execute end-to-end intelligence workflow for a given analysis record."""
        sqlite = get_sqlite_db()
        doc = await sqlite.get_analysis(str(analysis_id))
        if doc is None:
            mongo = get_mongo_db()
            doc = await mongo.get_analysis(str(analysis_id))

        if doc is None:
            logger.error("Analysis record not found", analysis_id=str(analysis_id))
            return

        hint = doc.get("hint")
        object_path = doc.get("object_path")
        user_id = UUID("00000000-0000-0000-0000-000000000001")
        if doc.get("user_id"):
            try:
                user_id = UUID(str(doc["user_id"]))
            except Exception:
                pass

        recorder = EventRecorder(None, analysis_id)
        await recorder.init_sequence()

        async with self._client_context(self._external_client) as client:
            await self._execute_pipeline(
                analysis_id=analysis_id,
                user_id=user_id,
                hint=hint,
                object_path=object_path,
                recorder=recorder,
                client=client,
            )

    @asynccontextmanager
    async def _client_context(
        self,
        client: httpx.AsyncClient | None,
    ) -> AsyncIterator[httpx.AsyncClient]:
        if client is not None:
            yield client
        else:
            async with build_resilient_client(self.settings) as new_client:
                yield new_client

    async def _execute_pipeline(
        self,
        analysis_id: UUID,
        user_id: UUID,
        hint: str | None,
        object_path: str | None,
        recorder: EventRecorder,
        client: httpx.AsyncClient,
    ) -> None:
        workflow_started_at = datetime.now(UTC)
        logger.info("Starting pipeline execution", analysis_id=str(analysis_id), started_at=workflow_started_at.isoformat())
        sqlite = get_sqlite_db()
        mongo = get_mongo_db()

        # Helper to update status in SQLite and MongoDB
        async def update_status(status_val: str, error_code: str | None = None, error_message: str | None = None) -> None:
            updates = {"status": status_val}
            if error_code:
                updates["error_code"] = error_code
            if error_message:
                updates["error_message"] = error_message
            await sqlite.update_analysis(str(analysis_id), updates)
            try:
                await mongo.update_analysis(str(analysis_id), updates)
            except Exception:
                pass

        # -------------------------------------------------------------
        # STAGE 1: IMAGE VALIDATION
        # -------------------------------------------------------------
        await recorder.emit(
            EventType.STAGE,
            Stage.IMAGE_VALIDATION,
            {"status": "in_progress", "message": "Reading and validating screenshot dimensions"},
        )
        storage = create_storage(self.settings, client)
        image_bytes: bytes = b""
        mime_type = "image/webp"

        if object_path:
            try:
                image_bytes = await storage.get(object_path)
                mime_type, width, height = inspect_image(image_bytes, self.settings)
                logger.info("Image inspected successfully", width=width, height=height)
            except Exception as err:
                logger.warning("Image validation warning", error=str(err))

        await recorder.emit(
            EventType.STAGE,
            Stage.IMAGE_VALIDATION,
            {"status": "completed", "message": "Image validation successful"},
        )

        # -------------------------------------------------------------
        # STAGE 2: SCREENSHOT EXTRACTION (GEMINI VLM)
        # -------------------------------------------------------------
        await recorder.emit(
            EventType.STAGE,
            Stage.SCREENSHOT_EXTRACTION,
            {"status": "in_progress", "message": "Extracting market question and contract data using Gemini VLM"},
        )

        candidate: ScreenshotMarketCandidate
        try:
            extractor = create_vision_extractor(self.settings, http_client=client)
            candidate = await extractor.extract(image_bytes, mime_type, hint=hint)
        except Exception as err:
            logger.error("Vision extraction failed", error=str(err))
            await update_status(
                AnalysisStatus.EXTRACTION_FAILED,
                error_code="EXTRACTION_FAILED",
                error_message=f"Screenshot extraction failed: {err}",
            )
            await recorder.emit(
                EventType.FAILED,
                Stage.SCREENSHOT_EXTRACTION,
                {"error_code": "EXTRACTION_FAILED", "message": str(err)},
            )
            return

        extraction_data = candidate.model_dump(mode="json")
        await sqlite.update_analysis(
            str(analysis_id),
            {
                "extraction_result": extraction_data,
            },
        )
        try:
            await mongo.update_analysis(
                str(analysis_id),
                {"extraction_result": extraction_data},
            )
        except Exception:
            pass

        await recorder.emit(
            EventType.STAGE,
            Stage.SCREENSHOT_EXTRACTION,
            {
                "status": "completed",
                "message": f"Identified candidate contract: '{candidate.market_title or candidate.asset_or_entity}'",
            },
        )

        # -------------------------------------------------------------
        # STAGE 3: MARKET IDENTIFICATION
        # -------------------------------------------------------------
        await recorder.emit(
            EventType.STAGE,
            Stage.MARKET_IDENTIFICATION,
            {"status": "in_progress", "message": "Searching official platform APIs for matching contracts"},
        )

        gamma_client = ProviderHttpClient(
            client,
            self.settings,
            provider="polymarket_gamma",
            base_url=self.settings.POLYMARKET_GAMMA_API_URL,
        )
        clob_client = ProviderHttpClient(
            client,
            self.settings,
            provider="polymarket_clob",
            base_url=self.settings.POLYMARKET_CLOB_API_URL,
        )
        poly_adapter = PolymarketAdapter(self.settings, gamma_client, clob_client)
        bayse_client = ProviderHttpClient(
            client,
            self.settings,
            provider="bayse",
            base_url=self.settings.BAYSE_API_URL,
        )
        bayse_adapter = BayseAdapter(self.settings, bayse_client)

        found_markets: list[NormalizedMarket] = []
        search_query = candidate.market_title or candidate.asset_or_entity or ""

        if candidate.platform == "polymarket" or not candidate.platform or candidate.platform == "unknown":
            try:
                if candidate.market_slug:
                    found_markets.extend(await poly_adapter.get_market_by_slug(candidate.market_slug))
                if not found_markets and search_query:
                    found_markets.extend(await poly_adapter.search_markets(search_query, limit=5))
            except Exception as err:
                logger.warning("Polymarket search failed", error=str(err))

        if not found_markets and (candidate.platform in ("bayse", "unknown") or not candidate.platform):
            try:
                if candidate.market_slug:
                    found_markets.extend(await bayse_adapter.get_market_by_slug(candidate.market_slug))
                if not found_markets and search_query:
                    found_markets.extend(await bayse_adapter.search_markets(search_query, limit=5))
            except Exception as err:
                logger.warning("Bayse search failed", error=str(err))

        if not found_markets:
            err_msg = "No matching active contract found on Polymarket or Bayse."
            await update_status(AnalysisStatus.MARKET_NOT_FOUND, error_code="MARKET_NOT_FOUND", error_message=err_msg)
            await recorder.emit(
                EventType.FAILED,
                Stage.MARKET_IDENTIFICATION,
                {"error_code": "MARKET_NOT_FOUND", "message": err_msg},
            )
            return

        analysis_doc = await sqlite.get_analysis(str(analysis_id))
        verification_result = (analysis_doc or {}).get("verification_result") or {}
        confirmed_id = str(verification_result.get("user_confirmed_id") or "")

        await recorder.emit(
            EventType.STAGE,
            Stage.MARKET_IDENTIFICATION,
            {"status": "completed", "message": f"Discovered {len(found_markets)} platform candidate contracts"},
        )

        # -------------------------------------------------------------
        # STAGE 4: MARKET VERIFICATION
        # -------------------------------------------------------------
        await recorder.emit(
            EventType.STAGE,
            Stage.MARKET_VERIFICATION,
            {"status": "in_progress", "message": "Cross-verifying title, settlement date, and token identities"},
        )

        best_market: NormalizedMarket | None = None
        best_score = -1.0
        best_status = "UNVERIFIED"
        best_discrepancies: list[str] = []

        if confirmed_id:
            best_market = next(
                (market for market in found_markets if market.platform_market_id == confirmed_id),
                None,
            )
            if best_market is not None:
                _, best_score, best_discrepancies = match_candidate_to_platform_market(candidate, best_market)
                best_status = "VERIFIED"

        if best_market is None:
            for m in found_markets:
                status_match, score, discrepancies = match_candidate_to_platform_market(candidate, m)
                if score > best_score:
                    best_score = score
                    best_market = m
                    best_status = status_match
                    best_discrepancies = discrepancies

        if best_market is None or best_status == "UNVERIFIED":
            err_msg = "Market candidate could not be verified against official contract specifications."
            await update_status(AnalysisStatus.VERIFICATION_FAILED, error_code="VERIFICATION_FAILED", error_message=err_msg)
            await recorder.emit(
                EventType.FAILED,
                Stage.MARKET_VERIFICATION,
                {"error_code": "VERIFICATION_FAILED", "message": err_msg},
            )
            return

        if best_status == "NEEDS_CONFIRMATION":
            ver_res = {
                "status": best_status,
                "score": best_score,
                "discrepancies": best_discrepancies,
                "candidates": [
                    {
                        "candidate_id": best_market.platform_market_id,
                        "platform": best_market.platform,
                        "title": best_market.title,
                        "similarity_score": round(best_score, 3),
                    }
                ],
            }
            await sqlite.update_analysis(
                str(analysis_id),
                {
                    "status": "NEEDS_USER_CONFIRMATION",
                    "verification_result": ver_res,
                },
            )
            try:
                await mongo.update_analysis(
                    str(analysis_id),
                    {
                        "status": "NEEDS_USER_CONFIRMATION",
                        "verification_result": ver_res,
                    },
                )
            except Exception:
                pass

            await recorder.emit(
                EventType.NEEDS_CONFIRMATION,
                Stage.MARKET_VERIFICATION,
                {
                    "message": "Market verification requires user confirmation due to slight title/date difference.",
                    "status": "NEEDS_USER_CONFIRMATION",
                    "extracted_title": candidate.market_title,
                    "candidates": ver_res["candidates"],
                },
            )
            return

        # Market is verified! Persist to SQLite & MongoDB
        await sqlite.upsert_market(best_market.model_dump(mode="json"))
        try:
            await mongo.upsert_market(best_market.model_dump(mode="json"))
        except Exception:
            pass

        ver_info = {
            "status": best_status,
            "score": best_score,
            "discrepancies": best_discrepancies,
        }
        await sqlite.update_analysis(
            str(analysis_id),
            {
                "verification_result": ver_info,
            },
        )
        try:
            await mongo.update_analysis(
                str(analysis_id),
                {
                    "platform": best_market.platform,
                    "market_id": best_market.platform_market_id,
                    "verification_result": ver_info,
                },
            )
        except Exception:
            pass

        await recorder.emit(
            EventType.STAGE,
            Stage.MARKET_VERIFICATION,
            {
                "status": "completed",
                "message": f"Market verified on {best_market.platform.upper()} (score: {best_score:.2f})",
            },
        )

        # -------------------------------------------------------------
        # STAGE 5: DATA COLLECTION (ORDER BOOK INGESTION)
        # -------------------------------------------------------------
        await recorder.emit(
            EventType.STAGE,
            Stage.DATA_COLLECTION,
            {"status": "in_progress", "message": "Ingesting real-time CLOB order book and spread data"},
        )

        adapter = poly_adapter if best_market.platform == "polymarket" else bayse_adapter
        try:
            order_book = await adapter.get_order_book(best_market)
        except Exception as err:
            logger.error("Order book fetch error from platform API", error=str(err))
            err_msg = f"Failed to retrieve live order book from {best_market.platform}: {err}"
            await update_status(AnalysisStatus.DATA_STALE, error_code="DATA_UNAVAILABLE", error_message=err_msg)
            await recorder.emit(
                EventType.FAILED,
                Stage.DATA_COLLECTION,
                {"error_code": "DATA_UNAVAILABLE", "message": err_msg},
            )
            return

        await recorder.emit(
            EventType.STAGE,
            Stage.DATA_COLLECTION,
            {"status": "completed", "message": "Order book snapshots recorded"},
        )

        # -------------------------------------------------------------
        # STAGE 6: FEATURE BUILDING & SPECIALIST RESEARCH
        # -------------------------------------------------------------
        await recorder.emit(
            EventType.STAGE,
            Stage.FEATURE_BUILDING,
            {"status": "in_progress", "message": "Analyzing interval movement patterns, technicals, and order flow"},
        )

        specialist_service = SpecialistResearchService(http_client=client)
        from prediction_ai.specialists.crypto.time_tracking import resolve_market_timing

        timing_ctx = resolve_market_timing(
            workflow_started_at=workflow_started_at,
            countdown_text=candidate.countdown_text,
            displayed_expiry=candidate.displayed_expiry,
            market_closes_at=best_market.closes_at,
        )
        best_market.resolves_at = timing_ctx.exact_resolves_at
        best_market.closes_at = timing_ctx.exact_resolves_at

        time_to_close = timing_ctx.decision_seconds_remaining
        forecast_cutoff = datetime.now(UTC)

        category = best_market.category
        target_price_float = float(candidate.target_price) if candidate.target_price is not None else None
        displayed_price_float = float(candidate.displayed_underlying_price) if candidate.displayed_underlying_price is not None else None

        # Direction is taken from the platform's own outcome labels/title, not
        # from screenshot free text.  A title like "Bitcoin Up or Down" must
        # never be interpreted as a below-threshold contract.
        crypto_direction: str | None = None
        if category == "crypto":
            contract_direction = resolve_contract_direction(best_market)
            if contract_direction in ("up", "above"):
                crypto_direction = "above"
            elif contract_direction in ("down", "below"):
                crypto_direction = "below"

        specialist_context = await specialist_service.gather_context(
            category=category,
            asset=best_market.underlying_symbol or candidate.asset_or_entity,
            title=best_market.title,
            time_to_close_seconds=time_to_close,
            target_price=target_price_float,
            displayed_underlying_price=displayed_price_float,
            direction=crypto_direction,
        )

        screenshot_observation = candidate.model_dump(
            mode="json",
            include={
                "target_price",
                "target_currency",
                "comparison_operator",
                "displayed_underlying_price",
                "displayed_probability",
                "outcomes",
                "countdown_text",
                "current_round_label",
                "previous_round_labels",
                "chart_observation",
                "visible_trade_count",
                "visible_timestamp_text",
            },
        )
        specialist_context["time_context"] = {
            "forecast_cutoff_at": forecast_cutoff.isoformat(),
            "target_at": best_market.closes_at.isoformat() if best_market.closes_at else None,
            "seconds_to_target": time_to_close,
        }
        specialist_context["screenshot_observation"] = screenshot_observation

        if category == "sports":
            sports_research = specialist_context.get("sports_research") or {}
            if sports_research.get("recommendation_gate") != "eligible":
                error_code = "SPORTS_RESEARCH_INCOMPLETE"
                error_message = (
                    "Sports research could not verify the exact proposition, deadline, and "
                    "approved source evidence. No sports forecast was produced."
                )
                await update_status(
                    AnalysisStatus.RESEARCH_FAILED,
                    error_code=error_code,
                    error_message=error_message,
                )
                await recorder.emit(
                    EventType.FAILED,
                    Stage.FEATURE_BUILDING,
                    {"error_code": error_code, "message": error_message},
                )
                return

        if category == "crypto":
            crypto_research = specialist_context.get("crypto_research") or {}
            crypto_status = crypto_research.get("status")
            if crypto_status in {"DATA_UNAVAILABLE", "STRIKE_UNKNOWN"}:
                if crypto_status == "STRIKE_UNKNOWN":
                    error_code = "CRYPTO_STRIKE_UNKNOWN"
                    error_message = (
                        "Crypto research resolved a spot price but could not verify the "
                        "contract strike. No crypto forecast was produced; confirm the "
                        "target price and retry."
                    )
                else:
                    error_code = "CRYPTO_SPOT_UNAVAILABLE"
                    error_message = (
                        "Crypto research could not resolve a spot price from the exchange "
                        "mesh, closed candles, or the screenshot. No crypto forecast was "
                        "produced."
                    )
                await update_status(
                    AnalysisStatus.RESEARCH_FAILED,
                    error_code=error_code,
                    error_message=error_message,
                )
                await recorder.emit(
                    EventType.FAILED,
                    Stage.FEATURE_BUILDING,
                    {"error_code": error_code, "message": error_message},
                )
                return

        crypto_research = specialist_context.get("crypto_research") or {}
        strike_override: Decimal | None = None
        raw_strike = crypto_research.get("strike") if category == "crypto" else None
        if raw_strike is not None:
            try:
                parsed_strike = Decimal(str(raw_strike))
                if parsed_strike > 0:
                    strike_override = parsed_strike
            except Exception:
                strike_override = None

        contract_spec = build_contract_spec(
            best_market,
            candidate,
            settlement_timestamp=timing_ctx.exact_resolves_at,
            strike_override=strike_override,
        )

        await recorder.emit(
            EventType.STAGE,
            Stage.FEATURE_BUILDING,
            {
                "status": "completed",
                "message": f"Domain research synthesized for {category.upper()}: intervals & momentum calibrated",
                "specialist_context": specialist_context,
                "contract_spec": contract_spec.model_dump(mode="json"),
            },
        )

        # -------------------------------------------------------------
        # STAGE 7: FORECASTING (GEMINI + CALIBRATION)
        # -------------------------------------------------------------
        await recorder.emit(
            EventType.STAGE,
            Stage.FORECASTING,
            {"status": "in_progress", "message": "Synthesizing probabilistic contract forecast with Gemini"},
        )

        forecaster = GeminiForecaster(self.settings)
        forecast_result = await forecaster.forecast(
            best_market,
            order_book,
            contract_spec=contract_spec,
            specialist_context=specialist_context,
            screenshot_observation=screenshot_observation,
        )

        await recorder.emit(
            EventType.STAGE,
            Stage.FORECASTING,
            {
                "status": "completed",
                "message": f"Calibrated P(YES): {forecast_result.probability_yes:.3f} | P(NO): {forecast_result.probability_no:.3f}",
            },
        )

        # -------------------------------------------------------------
        # PRE-DECISION ORDER BOOK REFRESH
        # Gemini forecasting can take 1–3 minutes.  The order book snapshot
        # fetched during DATA_COLLECTION is therefore always stale by the time
        # we reach DECISION_EVALUATION, causing STALE_QUOTE abstention on every
        # slow LLM call.  The correct fix is not to inflate the age threshold
        # (which would let arbitrarily old prices through) but to re-fetch the
        # book right before we evaluate the decision — a fresh, executable quote.
        #
        # We attempt a lightweight re-fetch and fall back to the original book
        # if it fails so the pipeline never hard-fails purely due to a refresh
        # timeout.  The source of the quote used for the decision is recorded in
        # execution_snapshot for full auditability (engineering-rules §1.5).
        # -------------------------------------------------------------
        order_book_refreshed = False
        try:
            refreshed_book = await asyncio.wait_for(
                adapter.get_order_book(best_market),
                timeout=10.0,
            )
            order_book = refreshed_book
            order_book_refreshed = True
            logger.info(
                "Order book refreshed before decision evaluation",
                platform=best_market.platform,
                market_id=best_market.platform_market_id,
            )
        except Exception as _refresh_err:
            # Non-fatal: continue with the original book and let the quote-age
            # gate decide whether it is still within tolerance.
            logger.warning(
                "Pre-decision order book refresh failed; using original snapshot",
                platform=best_market.platform,
                market_id=best_market.platform_market_id,
                error=type(_refresh_err).__name__,
            )

        # -------------------------------------------------------------
        # STAGE 8: DECISION EVALUATION
        # -------------------------------------------------------------
        await recorder.emit(
            EventType.STAGE,
            Stage.DECISION_EVALUATION,
            {"status": "in_progress", "message": "Evaluating expected value, execution friction, and risk limits"},
        )

        yes_quote = _find_outcome_quote(
            order_book,
            token_id=contract_spec.affirmative_token_id,
            outcome_id=contract_spec.affirmative_outcome_id,
            fallback_labels=_AFFIRMATIVE_LABELS,
        )
        no_quote = _find_outcome_quote(
            order_book,
            token_id=contract_spec.negative_token_id,
            outcome_id=contract_spec.negative_outcome_id,
            fallback_labels=_NEGATIVE_LABELS,
        )

        # Missing quotes stay missing.  Never substitute 0.50: that manufactures
        # an executable price and can produce a fabricated trade recommendation.
        ask_yes = yes_quote.best_ask if yes_quote is not None else None
        ask_no = no_quote.best_ask if no_quote is not None else None

        decision_at = datetime.now(UTC)
        quote_age_seconds = _quote_age_seconds(order_book.observed_at, decision_at)
        target_order_usd = Decimal(str(self.settings.DECISION_TARGET_ORDER_USD))
        min_depth_usd = Decimal(str(self.settings.DECISION_MIN_DEPTH_USD))

        yes_walk = (
            walk_book_for_notional(yes_quote.asks, target_order_usd)
            if yes_quote is not None
            else None
        )
        no_walk = (
            walk_book_for_notional(no_quote.asks, target_order_usd)
            if no_quote is not None
            else None
        )

        if ask_yes is not None and ask_no is not None:
            spread_value = ask_yes + ask_no - Decimal("1.0")
        else:
            spread_value = None

        critical_contract_flags = contract_spec.critical_flags()
        resolution_flags = sorted(
            set(critical_contract_flags) | set(forecast_result.resolution_risk_flags)
        )
        strict_crypto = category == "crypto" and self.settings.CONTRACT_SPEC_STRICT_CRYPTO

        # Apply learned uncertainty_margin from asset_calibration when available.
        # The postmortem loop updates this column to tighten or widen the edge
        # threshold based on past abstention outcomes.
        _default_margin = Decimal(str(self.settings.DECISION_UNCERTAINTY_MARGIN))
        learned_calibration: dict[str, Any] = (
            crypto_research.get("learned_calibration") or {}
            if category == "crypto"
            else {}
        )
        _cal_margin_raw = learned_calibration.get("uncertainty_margin")
        if _cal_margin_raw is not None:
            try:
                _cal_margin = Decimal(str(round(float(_cal_margin_raw), 4)))
                # Clamp to [0.005, 0.10] — never allow a learned value to
                # collapse the edge gate to zero or expand it unreasonably.
                if Decimal("0.005") <= _cal_margin <= Decimal("0.10"):
                    effective_uncertainty_margin = _cal_margin
                else:
                    effective_uncertainty_margin = _default_margin
            except Exception:
                effective_uncertainty_margin = _default_margin
        else:
            effective_uncertainty_margin = _default_margin

        # Confidence-adaptive edge adjustment using σ-distance.
        # When the market is far from the strike (σ-distance > 1.5), the outcome
        # is directionally clear — reduce the uncertainty buffer so we don't
        # over-gate on easy trades.  When near the strike (< 0.3), oracle/model
        # mismatch risk is highest — tighten the buffer.
        # This applies on top of the learned calibration margin.
        if category == "crypto":
            _sigma_dist_raw = (crypto_research.get("sigma_distance") or "0.0")
            try:
                _sigma_dist = float(_sigma_dist_raw)
            except (ValueError, TypeError):
                _sigma_dist = 0.0

            signal_confidence = crypto_research.get("signal_confidence", "medium")
            if _sigma_dist >= 1.5 and signal_confidence == "high":
                # Far from strike + high-quality data: relax buffer by 0.5%
                effective_uncertainty_margin = max(
                    Decimal("0.005"),
                    effective_uncertainty_margin - Decimal("0.005"),
                )
            elif _sigma_dist < 0.3:
                # Near knife-edge: tighten buffer (require more edge)
                effective_uncertainty_margin = min(
                    Decimal("0.08"),
                    effective_uncertainty_margin + Decimal("0.02"),
                )

        decision = evaluate_decision(
            probability_yes=forecast_result.probability_yes,
            ask_yes=ask_yes,
            ask_no=ask_no,
            min_edge=Decimal(str(self.settings.DECISION_MIN_EDGE)),
            max_spread=Decimal(str(self.settings.DECISION_MAX_SPREAD)),
            spread=spread_value,
            uncertainty_margin=effective_uncertainty_margin,
            taker_fee_rate_yes=best_market.taker_fee_rate,
            taker_fee_rate_no=best_market.taker_fee_rate,
            fee_model=best_market.fee_model,
            slippage_bps=self.settings.DECISION_SLIPPAGE_BPS,
            kelly_fraction=Decimal(str(self.settings.DECISION_KELLY_FRACTION)),
            quote_age_seconds=quote_age_seconds,
            max_quote_age_seconds=Decimal(str(self.settings.DECISION_MAX_QUOTE_AGE_SECONDS)),
            depth_ask_usd_yes=(
                yes_quote.depth_ask_usd if yes_quote is not None else None
            ),
            depth_ask_usd_no=(
                no_quote.depth_ask_usd if no_quote is not None else None
            ),
            min_depth_usd=min_depth_usd,
            target_order_usd=target_order_usd,
            fill_complete_yes=yes_walk.complete if yes_walk is not None else False,
            fill_complete_no=no_walk.complete if no_walk is not None else False,
            depth_known=order_book.depth_known,
            resolution_risk_flags=resolution_flags,
            blocking_resolution_flags=critical_contract_flags,
            block_on_resolution_risk=strict_crypto,
        )

        forecast_id = uuid4()
        now_dt = datetime.now(UTC)

        execution_snapshot = {
            "observed_at": order_book.observed_at.isoformat(),
            "quote_age_seconds": str(quote_age_seconds),
            "quote_refreshed": order_book_refreshed,
            "ask_yes": str(ask_yes) if ask_yes is not None else None,
            "ask_no": str(ask_no) if ask_no is not None else None,
            "spread": str(spread_value) if spread_value is not None else None,
            "depth_ask_usd_yes": str(yes_quote.depth_ask_usd) if yes_quote is not None else None,
            "depth_ask_usd_no": str(no_quote.depth_ask_usd) if no_quote is not None else None,
            "depth_known": order_book.depth_known,
            "fill_complete_yes": yes_walk.complete if yes_walk is not None else False,
            "fill_complete_no": no_walk.complete if no_walk is not None else False,
            "target_order_usd": str(target_order_usd),
            "taker_fee_rate": str(best_market.taker_fee_rate),
            "fee_model": best_market.fee_model,
            "slippage_bps": self.settings.DECISION_SLIPPAGE_BPS,
            "quote_source": "platform_clob",
        }
        input_snapshot = dict(forecast_result.input_snapshot)
        input_snapshot["execution"] = execution_snapshot
        input_snapshot["contract_spec_hash"] = contract_spec.spec_hash
        input_snapshot["contract_tradeable"] = contract_spec.tradeable
        input_snapshot["resolution_risk_flags"] = resolution_flags

        feature_snapshot = {
            "category": category,
            "time_context": specialist_context.get("time_context"),
            "crypto_research": crypto_research if category == "crypto" else {},
            "market_microstructure": {
                "observed_at": order_book.observed_at.isoformat(),
                "quality_flags": order_book.quality_flags,
                "depth_known": order_book.depth_known,
            },
            "decision_gate_inputs": {
                "sigma_distance": crypto_research.get("sigma_distance") if category == "crypto" else None,
                "signal_confidence": crypto_research.get("signal_confidence") if category == "crypto" else None,
                "effective_uncertainty_margin": str(effective_uncertainty_margin),
                "order_book_refreshed": order_book_refreshed,
            },
        }
        model_components = {
            "llm_probability": forecast_result.input_snapshot.get("llm_probability"),
            "math_probability": forecast_result.input_snapshot.get("math_probability"),
            "market_implied_probability": (
                float(forecast_result.market_implied_probability)
                if forecast_result.market_implied_probability is not None
                else None
            ),
            "market_anchor_available": forecast_result.input_snapshot.get(
                "market_anchor_available"
            ),
            "pre_blend_model_probability": forecast_result.input_snapshot.get("model_raw"),
            "blend_version": forecast_result.input_snapshot.get("blend_version"),
            "blend_weights": forecast_result.input_snapshot.get("blend_weights"),
            "component_probabilities": forecast_result.input_snapshot.get(
                "component_probabilities"
            ),
        }

        forecast_doc = {
            "id": str(forecast_id),
            "forecast_id": str(forecast_id),
            "analysis_id": str(analysis_id),
            "user_id": str(user_id),
            "market_id": best_market.platform_market_id,
            "forecasted_at": now_dt.isoformat(),
            "forecast_cutoff_at": forecast_cutoff.isoformat(),
            "seconds_to_target": time_to_close,
            "target_definition": _build_target_definition(best_market, contract_spec),
            "probability_yes": float(forecast_result.probability_yes),
            "probability_no": float(forecast_result.probability_no),
            "uncertainty_interval": [float(x) for x in forecast_result.uncertainty_interval],
            "recommendation": decision.recommendation,
            "decision_metrics": decision.metrics.model_dump(mode="json"),
            "model_version": forecast_result.model_version,
            "calibration_version": forecast_result.calibration_version,
            "prompt_version": forecast_result.prompt_version,
            "decision_policy_version": self.settings.DECISION_POLICY_VERSION,
            "explanation": {
                "reasoning": forecast_result.reasoning,
                "key_drivers": forecast_result.key_drivers,
                "decision_reason": decision.reason,
            },
            "evidence": [ev.model_dump(mode="json") for ev in forecast_result.evidence],
            "market": best_market.model_dump(mode="json"),
            "specialist_context": specialist_context,
            "timing_provenance": timing_ctx.to_dict(),
            "contract_spec": contract_spec.model_dump(mode="json"),
            "input_snapshot": input_snapshot,
            "feature_snapshot": feature_snapshot,
            "model_components": model_components,
            "data_quality": {
                "flags": order_book.quality_flags,
                "quote_age_seconds": float(quote_age_seconds),
                "depth_known": order_book.depth_known,
                "contract_ambiguities": [
                    ambiguity.model_dump(mode="json")
                    for ambiguity in contract_spec.ambiguities
                ],
            },
        }

        # 1. Save to SQLite
        await sqlite.save_forecast(forecast_doc)
        await sqlite.update_analysis(
            str(analysis_id),
            {
                "status": "COMPLETED",
                "completed_at": now_dt.isoformat(),
                "forecast_id": str(forecast_id),
                "forecast": forecast_doc,
                "specialist_context": specialist_context,
            },
        )

        # 2. Mirror to MongoDB if available
        try:
            await mongo.save_forecast(forecast_doc)
            await mongo.update_analysis(
                str(analysis_id),
                {
                    "status": "COMPLETED",
                    "completed_at": now_dt.isoformat(),
                    "forecast_id": str(forecast_id),
                    "forecast": forecast_doc,
                    "specialist_context": specialist_context,
                },
            )
        except Exception:
            pass

        await recorder.emit(
            EventType.COMPLETED,
            Stage.DECISION_EVALUATION,
            {
                "analysis_id": str(analysis_id),
                "status": "COMPLETED",
                "forecast_id": str(forecast_id),
                "recommendation": decision.recommendation,
                "probability_yes": str(forecast_result.probability_yes),
                "probability_no": str(forecast_result.probability_no),
                "market_title": best_market.title,
                "result_url": f"/api/v1/forecasts/{forecast_id}",
            },
        )

        await recorder.emit(
            EventType.STAGE,
            Stage.DECISION_EVALUATION,
            {
                "status": "completed",
                "message": f"Decision: {decision.recommendation} ({decision.reason})",
            },
        )
        logger.info(
            "Pipeline completed successfully",
            analysis_id=str(analysis_id),
            recommendation=decision.recommendation,
            reason=decision.reason,
            gating_flags=decision.gating_flags,
            sigma_distance=crypto_research.get("sigma_distance") if category == "crypto" else None,
            signal_confidence=crypto_research.get("signal_confidence") if category == "crypto" else None,
            effective_uncertainty_margin=str(effective_uncertainty_margin),
            order_book_refreshed=order_book_refreshed,
        )
