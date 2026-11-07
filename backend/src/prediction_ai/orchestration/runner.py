from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import httpx
import structlog

from prediction_ai.core.config import Settings, get_settings
from prediction_ai.core.workflow import AnalysisStatus, EventType, Stage
from prediction_ai.db.mongo import get_mongo_db
from prediction_ai.db.sqlite import get_sqlite_db
from prediction_ai.decision.engine import evaluate_decision
from prediction_ai.forecasting.gemini_forecaster import GeminiForecaster
from prediction_ai.integrations.bayse import BayseAdapter
from prediction_ai.integrations.http import ProviderHttpClient
from prediction_ai.integrations.polymarket import PolymarketAdapter
from prediction_ai.markets.matcher import match_candidate_to_platform_market
from prediction_ai.orchestration.events import EventRecorder
from prediction_ai.schemas.analysis import ScreenshotMarketCandidate
from prediction_ai.schemas.market import NormalizedMarket
from prediction_ai.specialists.research import SpecialistResearchService
from prediction_ai.storage.factory import create_storage
from prediction_ai.vision.factory import create_vision_extractor
from prediction_ai.vision.images import inspect_image

logger = structlog.get_logger(__name__)


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

    @staticmethod
    @asynccontextmanager
    async def _client_context(
        client: httpx.AsyncClient | None,
    ) -> AsyncIterator[httpx.AsyncClient]:
        if client is not None:
            yield client
        else:
            async with httpx.AsyncClient() as new_client:
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
        if best_market.resolves_at is None:
            best_market.resolves_at = timing_ctx.exact_resolves_at
        if best_market.closes_at is None:
            best_market.closes_at = timing_ctx.exact_resolves_at

        time_to_close = timing_ctx.decision_seconds_remaining
        forecast_cutoff = datetime.now(UTC)

        category = best_market.category if best_market.category and best_market.category != "unknown" else "crypto"
        target_price_float = float(candidate.target_price) if candidate.target_price is not None else None
        displayed_price_float = float(candidate.displayed_underlying_price) if candidate.displayed_underlying_price is not None else None
        specialist_context = await specialist_service.gather_context(
            category=category,
            asset=best_market.underlying_symbol or candidate.asset_or_entity,
            title=best_market.title,
            time_to_close_seconds=time_to_close,
            target_price=target_price_float,
            displayed_underlying_price=displayed_price_float,
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

        await recorder.emit(
            EventType.STAGE,
            Stage.FEATURE_BUILDING,
            {
                "status": "completed",
                "message": f"Domain research synthesized for {category.upper()}: intervals & momentum calibrated",
                "specialist_context": specialist_context,
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
        # STAGE 8: DECISION EVALUATION
        # -------------------------------------------------------------
        await recorder.emit(
            EventType.STAGE,
            Stage.DECISION_EVALUATION,
            {"status": "in_progress", "message": "Evaluating expected value, execution friction, and risk limits"},
        )

        ask_yes = Decimal("0.50")
        ask_no = Decimal("0.50")
        for q in order_book.quotes:
            label = q.label.strip().lower()
            if label in ("yes", "up", "higher", "over") and q.best_ask is not None:
                ask_yes = q.best_ask
            elif label in ("no", "down", "lower", "under") and q.best_ask is not None:
                ask_no = q.best_ask

        # If quotes are a binary pair that didn't match the standard labels, map index 0 to YES and 1 to NO
        if ask_yes == Decimal("0.50") and ask_no == Decimal("0.50") and len(order_book.quotes) == 2:
            if order_book.quotes[0].best_ask is not None:
                ask_yes = order_book.quotes[0].best_ask
            if order_book.quotes[1].best_ask is not None:
                ask_no = order_book.quotes[1].best_ask

        decision = evaluate_decision(
            probability_yes=forecast_result.probability_yes,
            ask_yes=ask_yes,
            ask_no=ask_no,
            min_edge=Decimal(str(self.settings.DECISION_MIN_EDGE)),
            max_spread=Decimal(str(self.settings.DECISION_MAX_SPREAD)),
        )

        forecast_id = uuid4()
        now_dt = datetime.now(UTC)

        forecast_doc = {
            "id": str(forecast_id),
            "forecast_id": str(forecast_id),
            "analysis_id": str(analysis_id),
            "user_id": str(user_id),
            "market_id": best_market.platform_market_id,
            "forecasted_at": now_dt.isoformat(),
            "forecast_cutoff_at": forecast_cutoff.isoformat(),
            "seconds_to_target": time_to_close,
            "target_definition": {"question": best_market.title, "outcomes": ["YES", "NO"]},
            "probability_yes": float(forecast_result.probability_yes),
            "probability_no": float(forecast_result.probability_no),
            "uncertainty_interval": [float(x) for x in forecast_result.uncertainty_interval],
            "recommendation": decision.recommendation,
            "decision_metrics": decision.metrics.model_dump(mode="json"),
            "data_quality": {"flags": order_book.quality_flags},
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
        )
