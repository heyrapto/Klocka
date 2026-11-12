import asyncio
import sqlite3
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import structlog
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import StreamingResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prediction_ai.api.deps import get_current_user, get_db, get_mongo, get_sqlite
from prediction_ai.auth.jwt import AuthenticatedUser
from prediction_ai.core.config import Settings, get_settings
from prediction_ai.core.workflow import TERMINAL_STATUSES, AnalysisStatus
from prediction_ai.db.models.analysis import Analysis, AnalysisEvent, UploadedAsset
from prediction_ai.db.models.forecast import Forecast
from prediction_ai.db.models.market import PredictionMarket
from prediction_ai.db.models.profile import Profile
from prediction_ai.db.mongo import MongoDBStore
from prediction_ai.db.sqlite import SQLiteStore, get_sqlite_db
from prediction_ai.orchestration.events import format_sse_message
from prediction_ai.orchestration.runner import AnalysisWorkflowRunner
from prediction_ai.schemas.analysis import (
    AnalysisCreateResponse,
    AnalysisDetailResponse,
    AnalysisErrorInfo,
    AnalysisFeedbackRequest,
    AnalysisFeedbackResponse,
    AssetCalibrationResponse,
    EventEnvelope,
    MarketConfirmationRequest,
)
from prediction_ai.schemas.forecast import ForecastSummary, MarketSummary
from prediction_ai.schemas.market import NormalizedOutcome
from prediction_ai.storage.factory import create_storage
from prediction_ai.vision.images import validate_and_normalize_image

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/analyses", tags=["Analyses"])

_ALLOWED_SETTLEMENT_OUTCOMES = frozenset(
    {"YES", "NO", "UP", "DOWN", "HIGHER", "LOWER", "BUY UP", "BUY DOWN"}
)


def _normalize_settlement_outcome(value: str) -> str:
    normalized = value.strip().upper().replace("_", " ")
    if normalized not in _ALLOWED_SETTLEMENT_OUTCOMES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "actual_outcome must be one of YES, NO, UP, DOWN, HIGHER, LOWER, "
                "BUY UP, or BUY DOWN."
            ),
        )
    return normalized


def _ensure_analysis_access(doc: dict[str, Any], user_id: UUID) -> None:
    owner = doc.get("user_id")
    if owner and str(owner) != str(user_id):
        # Do not reveal whether another user's analysis exists.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Analysis not found")


def _feedback_response_from_doc(doc: dict[str, Any]) -> AnalysisFeedbackResponse:
    recommendation = str(doc.get("recommendation") or "NO_TRADE")
    probability_yes = doc.get("probability_yes")
    if probability_yes is None:
        prediction_made = recommendation
    else:
        prediction_made = f"{recommendation} (P(YES)={float(probability_yes):.2f})"
    return AnalysisFeedbackResponse(
        feedback_id=UUID(str(doc["feedback_id"])),
        analysis_id=UUID(str(doc["analysis_id"])),
        actual_outcome=str(doc.get("actual_outcome") or ""),
        was_correct=bool(doc.get("was_correct")),
        evaluation_eligible=bool(doc.get("evaluation_eligible", True)),
        prediction_made=prediction_made,
        postmortem_analysis=str(doc.get("postmortem_analysis") or ""),
        visual_observations=doc.get("visual_observations"),
        discrepancy_classification=doc.get("discrepancy_classification"),
        key_learnings=list(doc.get("key_learnings") or []),
        created_at=(
            datetime.fromisoformat(str(doc["created_at"]))
            if doc.get("created_at")
            else datetime.now(UTC)
        ),
    )


async def _ensure_profile_exists(session: AsyncSession, user_id: UUID) -> None:
    """Ensure a user profile row exists so foreign key constraints succeed."""
    profile = await session.get(Profile, user_id)
    if profile is None:
        profile = Profile(
            id=user_id,
            display_name=f"User {str(user_id)[:8]}",
            tier="standard",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        session.add(profile)
        await session.flush()


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=AnalysisCreateResponse)
async def create_analysis(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile | None = File(default=None),
    hint: str | None = Form(default=None),
    screenshot_asset_id: UUID | None = Form(default=None),
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession | None = Depends(get_db),
    sqlite: SQLiteStore = Depends(get_sqlite),
    mongo: MongoDBStore = Depends(get_mongo),
    settings: Settings = Depends(get_settings),
) -> AnalysisCreateResponse:
    """Initiate an asynchronous analysis job from an uploaded screenshot file or stored asset."""
    asset_id = screenshot_asset_id
    object_path: str | None = None

    # If direct file is provided via multipart/form-data
    if file is not None:
        content = await file.read()
        validated = validate_and_normalize_image(content, settings)

        new_asset_id = uuid4()
        storage = create_storage(settings)
        object_path = f"{current_user.id}/{new_asset_id}.webp"
        await storage.put(object_path, validated.data, validated.mime_type)

        if db is not None:
            try:
                await _ensure_profile_exists(db, current_user.id)
                asset = UploadedAsset(
                    id=new_asset_id,
                    owner_id=current_user.id,
                    bucket="screenshots",
                    object_path=object_path,
                    mime_type=validated.mime_type,
                    size_bytes=len(validated.data),
                    sha256=validated.sha256,
                    asset_type="screenshot",
                    width=validated.width,
                    height=validated.height,
                    created_at=datetime.now(UTC),
                )
                db.add(asset)
                await db.flush()
            except Exception as err:
                logger.warning("Could not persist uploaded asset to SQL database", error=str(err))

        asset_id = new_asset_id

    analysis_id = uuid4()
    now_dt = datetime.now(UTC)

    analysis_record: dict[str, Any] = {
        "analysis_id": str(analysis_id),
        "user_id": str(current_user.id),
        "status": "RECEIVED",
        "hint": hint,
        "screenshot_asset_id": str(asset_id) if asset_id else None,
        "object_path": object_path,
        "created_at": now_dt.isoformat(),
        "updated_at": now_dt.isoformat(),
        "events": [],
    }

    # 1. Primary zero-daemon persistence in SQLite
    await sqlite.create_analysis(dict(analysis_record))

    # 2. Mirror to MongoDB if available
    try:
        await mongo.create_analysis(dict(analysis_record))
    except Exception:
        pass

    # 3. Dual-write to SQL if session is active
    if db is not None:
        try:
            await _ensure_profile_exists(db, current_user.id)
            analysis = Analysis(
                id=analysis_id,
                user_id=current_user.id,
                status=AnalysisStatus.RECEIVED,
                screenshot_asset_id=asset_id,
                hint=hint,
                created_at=now_dt,
                updated_at=now_dt,
            )
            db.add(analysis)
            await db.commit()
        except Exception as err:
            logger.warning("Could not persist analysis row to SQL database", error=str(err))

    # Dispatch workflow execution
    if settings.ANALYSIS_TASK_MODE == "celery":
        from prediction_ai.workers.tasks import run_analysis_pipeline

        run_analysis_pipeline.delay(str(analysis_id))
    else:
        # Run inline asynchronously via FastAPI BackgroundTasks
        runner = AnalysisWorkflowRunner(settings)
        background_tasks.add_task(runner.run, analysis_id)

    logger.info("Analysis job accepted", analysis_id=str(analysis_id), user_id=str(current_user.id))

    return AnalysisCreateResponse(
        analysis_id=analysis_id,
        status="RECEIVED",
        status_url=f"/api/v1/analyses/{analysis_id}",
        events_url=f"/api/v1/analyses/{analysis_id}/events",
    )


@router.get("/calibrations", response_model=list[AssetCalibrationResponse])
async def list_calibrations(
    category: str | None = None,
    sqlite: SQLiteStore = Depends(get_sqlite),
) -> list[AssetCalibrationResponse]:
    """List all tracked asset calibrations ordered by sample count."""
    rows = await sqlite.list_asset_calibrations(category=category)
    results: list[AssetCalibrationResponse] = []
    for cal in rows:
        sample_count = cal.get("sample_count", 0)
        correct_count = cal.get("correct_count", 0)
        win_rate = (correct_count / sample_count) if sample_count > 0 else None

        updated_at_val = cal.get("updated_at")
        if isinstance(updated_at_val, str):
            try:
                updated_dt = datetime.fromisoformat(updated_at_val)
            except Exception:
                updated_dt = datetime.now(UTC)
        elif isinstance(updated_at_val, datetime):
            updated_dt = updated_at_val
        else:
            updated_dt = datetime.now(UTC)

        results.append(
            AssetCalibrationResponse(
                symbol=cal["symbol"],
                category=cal.get("category", "crypto"),
                sample_count=sample_count,
                correct_count=correct_count,
                win_rate=round(win_rate, 4) if win_rate is not None else None,
                brier_score=round(cal.get("brier_score", 0.25), 4),
                vol_multiplier=round(cal.get("vol_multiplier", 1.0), 3),
                momentum_weight=round(cal.get("momentum_weight", 0.35), 3),
                order_flow_weight=round(cal.get("order_flow_weight", 0.30), 3),
                uncertainty_margin=round(cal.get("uncertainty_margin", 0.02), 4),
                last_discrepancy_reason=cal.get("last_discrepancy_reason"),
                updated_at=updated_dt,
            )
        )
    return results


@router.get("/calibration/{symbol}", response_model=AssetCalibrationResponse)
async def get_symbol_calibration(
    symbol: str,
    category: str = "crypto",
    sqlite: SQLiteStore = Depends(get_sqlite),
) -> AssetCalibrationResponse:
    """Retrieve statistical calibration profile and learning weights for a specific asset."""
    cal = await sqlite.get_asset_calibration(symbol=symbol, category=category)
    sample_count = cal.get("sample_count", 0)
    correct_count = cal.get("correct_count", 0)
    win_rate = (correct_count / sample_count) if sample_count > 0 else None

    updated_at_val = cal.get("updated_at")
    if isinstance(updated_at_val, str):
        try:
            updated_dt = datetime.fromisoformat(updated_at_val)
        except Exception:
            updated_dt = datetime.now(UTC)
    elif isinstance(updated_at_val, datetime):
        updated_dt = updated_at_val
    else:
        updated_dt = datetime.now(UTC)

    return AssetCalibrationResponse(
        symbol=cal["symbol"],
        category=cal["category"],
        sample_count=sample_count,
        correct_count=correct_count,
        win_rate=round(win_rate, 4) if win_rate is not None else None,
        brier_score=round(cal.get("brier_score", 0.25), 4),
        vol_multiplier=round(cal.get("vol_multiplier", 1.0), 3),
        momentum_weight=round(cal.get("momentum_weight", 0.35), 3),
        order_flow_weight=round(cal.get("order_flow_weight", 0.30), 3),
        uncertainty_margin=round(cal.get("uncertainty_margin", 0.02), 4),
        last_discrepancy_reason=cal.get("last_discrepancy_reason"),
        updated_at=updated_dt,
    )


@router.get("/{analysis_id}", response_model=AnalysisDetailResponse)
async def get_analysis_status(
    analysis_id: UUID,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession | None = Depends(get_db),
    sqlite: SQLiteStore = Depends(get_sqlite),
    mongo: MongoDBStore = Depends(get_mongo),
) -> AnalysisDetailResponse:
    """Fetch real-time status, candidate extraction, and forecast results for an analysis."""
    # 1. Check zero-daemon SQLite store first
    doc = await sqlite.get_analysis(str(analysis_id))
    if doc is None:
        # Fallback check MongoDB
        try:
            doc = await mongo.get_analysis(str(analysis_id))
        except Exception:
            doc = None

    if doc is not None:
        forecast_data = doc.get("forecast")
        forecast_summary: ForecastSummary | None = None
        if forecast_data:
            m = forecast_data.get("market") or {}
            market_summary = MarketSummary(
                id=uuid4(),
                platform=m.get("platform", "polymarket"),
                platform_market_id=m.get("platform_market_id", ""),
                slug=m.get("slug", ""),
                url=m.get("url"),
                title=m.get("title", ""),
                category=m.get("category", "crypto"),
                market_type=m.get("market_type", "binary"),
                underlying_symbol=m.get("underlying_symbol"),
                outcomes=[],
                resolves_at=datetime.fromisoformat(m["resolves_at"]) if m.get("resolves_at") else None,
                resolution_source=m.get("resolution_source"),
                resolution_criteria=m.get("resolution_criteria"),
                status=m.get("status", "ACTIVE"),
            )
            forecast_summary = ForecastSummary(
                id=UUID(forecast_data.get("id") or str(uuid4())),
                analysis_id=analysis_id,
                market=market_summary,
                forecasted_at=datetime.fromisoformat(forecast_data["forecasted_at"]) if forecast_data.get("forecasted_at") else datetime.now(UTC),
                probability_yes=Decimal(str(forecast_data.get("probability_yes", 0.5))),
                probability_no=Decimal(str(forecast_data.get("probability_no", 0.5))),
                uncertainty_interval=[Decimal(str(x)) for x in forecast_data.get("uncertainty_interval", [0.4, 0.6])],
                recommendation=forecast_data.get("recommendation", "NO_TRADE"),
                decision_metrics=forecast_data.get("decision_metrics", {}),
                data_quality=forecast_data.get("data_quality", {}),
                model_version=forecast_data.get("model_version", "v1"),
                prompt_version=forecast_data.get("prompt_version", "v1"),
                decision_policy_version=forecast_data.get("decision_policy_version", "v1"),
                explanation=forecast_data.get("explanation", {}),
                timing_provenance=forecast_data.get("timing_provenance"),
            )

        error_info: AnalysisErrorInfo | None = None
        if doc.get("error_code") and doc.get("error_message"):
            error_info = AnalysisErrorInfo(
                code=doc["error_code"],
                message=doc["error_message"],
            )

        created_dt = datetime.fromisoformat(doc["created_at"]) if doc.get("created_at") else datetime.now(UTC)
        updated_dt = datetime.fromisoformat(doc["updated_at"]) if doc.get("updated_at") else datetime.now(UTC)
        completed_dt = datetime.fromisoformat(doc["completed_at"]) if doc.get("completed_at") else None

        events = [
            EventEnvelope(
                id=ev.get("sequence_number", idx + 1),
                event=ev.get("event_type", "analysis.stage"),
                stage=ev.get("stage", "STAGE"),
                data=ev.get("payload", {}),
                created_at=datetime.fromisoformat(ev["created_at"]) if ev.get("created_at") else datetime.now(UTC),
            )
            for idx, ev in enumerate(doc.get("events", []))
        ]

        return AnalysisDetailResponse(
            analysis_id=analysis_id,
            status=doc.get("status", "RECEIVED"),
            created_at=created_dt,
            updated_at=updated_dt,
            completed_at=completed_dt,
            extraction=doc.get("extraction_result"),
            verification=doc.get("verification_result"),
            forecast=forecast_summary,
            error=error_info,
            events=events,
        )

    # 2. Fallback to SQL database if available
    if db is not None:
        analysis = await db.get(Analysis, analysis_id)
        if analysis is not None and (analysis.user_id == current_user.id or current_user.tier == "service_role"):
            events_stmt = (
                select(AnalysisEvent)
                .where(AnalysisEvent.analysis_id == analysis_id)
                .order_by(AnalysisEvent.sequence_number.asc())
            )
            events_result = await db.execute(events_stmt)
            sql_events = events_result.scalars().all()

            forecast_stmt = select(Forecast).where(Forecast.analysis_id == analysis_id)
            forecast_result = await db.execute(forecast_stmt)
            forecast_obj = forecast_result.scalar_one_or_none()

            sql_forecast_summary: ForecastSummary | None = None
            if forecast_obj is not None and forecast_obj.market_id is not None:
                market_obj = await db.get(PredictionMarket, forecast_obj.market_id)
                if market_obj is not None:
                    raw_outcomes: list[Any] = market_obj.outcomes if isinstance(market_obj.outcomes, list) else []
                    outcomes = [
                        NormalizedOutcome(
                            outcome_id=str(o.get("outcome_id", "tok_yes")),
                            token_id=str(o.get("token_id", "tok_yes")),
                            label=str(o.get("label", "Yes")),
                        )
                        for o in raw_outcomes
                    ]
                    m_summary = MarketSummary(
                        id=market_obj.id,
                        platform=market_obj.platform,
                        platform_market_id=market_obj.platform_market_id,
                        slug=market_obj.slug,
                        url=market_obj.url,
                        title=market_obj.title,
                        category=market_obj.category,
                        market_type=market_obj.market_type,
                        underlying_symbol=market_obj.underlying_symbol,
                        outcomes=outcomes,
                        resolves_at=market_obj.resolves_at,
                        resolution_source=market_obj.resolution_source,
                        resolution_criteria=market_obj.resolution_criteria,
                        status=market_obj.status,
                    )
                    sql_forecast_summary = ForecastSummary(
                        id=forecast_obj.id,
                        analysis_id=forecast_obj.analysis_id,
                        market=m_summary,
                        forecasted_at=forecast_obj.forecasted_at,
                        probability_yes=forecast_obj.probability_yes,
                        probability_no=forecast_obj.probability_no,
                        uncertainty_interval=forecast_obj.uncertainty_interval or [],
                        recommendation=forecast_obj.recommendation,  # type: ignore[arg-type]
                        decision_metrics=forecast_obj.decision_metrics,  # type: ignore[arg-type]
                        data_quality=forecast_obj.data_quality,
                        model_version=forecast_obj.model_version,
                        prompt_version=forecast_obj.prompt_version,
                        decision_policy_version=forecast_obj.decision_policy_version,
                        explanation=forecast_obj.explanation,
                    )

            sql_error = None
            if analysis.error_code and analysis.error_message:
                sql_error = AnalysisErrorInfo(
                    code=analysis.error_code,
                    message=analysis.error_message,
                )

            return AnalysisDetailResponse(
                analysis_id=analysis.id,
                status=analysis.status,
                created_at=analysis.created_at,
                updated_at=analysis.updated_at,
                completed_at=analysis.completed_at,
                extraction=analysis.extraction_result,
                verification=analysis.verification_result,
                forecast=sql_forecast_summary,
                error=sql_error,
                events=[
                    EventEnvelope(
                        id=ev.id,
                        event=ev.event_type,
                        stage=ev.stage,
                        data=ev.payload,
                        created_at=ev.created_at,
                    )
                    for ev in sql_events
                ],
            )

    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Analysis not found")


@router.get("/{analysis_id}/events")
async def stream_analysis_events(
    analysis_id: UUID,
    request: Request,
    current_user: AuthenticatedUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    after: int = 0,
) -> StreamingResponse:
    """Stream real-time stage progression and decision events over Server-Sent Events (SSE)."""

    async def event_generator() -> AsyncIterator[str]:
        last_seq = max(0, after)
        heartbeat_counter = 0
        sqlite = get_sqlite_db()

        while True:
            if await request.is_disconnected():
                break

            doc = await sqlite.get_analysis(str(analysis_id))
            if doc is None:
                yield format_sse_message("error", {"message": "Analysis not found"}, "err")
                break

            events = doc.get("events", [])
            new_events = [ev for ev in events if ev.get("sequence_number", 0) > last_seq]

            for ev in new_events:
                last_seq = ev.get("sequence_number", last_seq + 1)
                # Merge the stage into the streamed data so SSE clients can map
                # progress; "stage" is stored as a sibling of "payload" in the
                # durable event record and would otherwise never reach clients.
                event_data = {**ev.get("payload", {}), "stage": ev.get("stage", "STAGE")}
                yield format_sse_message(ev.get("event_type", "analysis.stage"), event_data, last_seq)

            if doc.get("status") in TERMINAL_STATUSES:
                break

            heartbeat_counter += 1
            if heartbeat_counter >= 15:
                yield ": keepalive\n\n"
                heartbeat_counter = 0

            await asyncio.sleep(settings.SSE_POLL_INTERVAL_SECONDS)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/{analysis_id}/confirm", status_code=status.HTTP_202_ACCEPTED)
async def confirm_market(
    analysis_id: UUID,
    payload: MarketConfirmationRequest,
    background_tasks: BackgroundTasks,
    current_user: AuthenticatedUser = Depends(get_current_user),
    sqlite: SQLiteStore = Depends(get_sqlite),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Confirm a specific market candidate when verification detected slight title or date divergence."""
    doc = await sqlite.get_analysis(str(analysis_id))
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Analysis not found")

    if doc.get("status") != "NEEDS_USER_CONFIRMATION":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Analysis is in status {doc.get('status')}, not awaiting confirmation",
        )

    ver_res = doc.get("verification_result") or {}
    ver_res["user_confirmed_id"] = payload.candidate_id
    await sqlite.update_analysis(
        str(analysis_id),
        {
            "status": "RECEIVED",
            "verification_result": ver_res,
        },
    )

    runner = AnalysisWorkflowRunner(settings)
    background_tasks.add_task(runner.run, analysis_id)

    return {"status": "CONFIRMED", "analysis_id": str(analysis_id)}


@router.post("/{analysis_id}/feedback", status_code=status.HTTP_200_OK, response_model=AnalysisFeedbackResponse)
async def submit_analysis_feedback(
    analysis_id: UUID,
    request: Request,
    current_user: AuthenticatedUser = Depends(get_current_user),
    sqlite: SQLiteStore = Depends(get_sqlite),
    settings: Settings = Depends(get_settings),
) -> AnalysisFeedbackResponse:
    """Submit post-settlement trade feedback with optional settlement screenshot for multimodal self-improvement."""
    doc = await sqlite.get_analysis(str(analysis_id))
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Analysis not found")
    _ensure_analysis_access(doc, current_user.id)

    content_type = request.headers.get("content-type", "")
    settlement_file_bytes: bytes | None = None
    settlement_file_mime: str = "image/jpeg"

    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        actual_outcome = str(form.get("actual_outcome", "YES")).upper()
        settled_price_raw = form.get("settled_price")
        settled_price = Decimal(str(settled_price_raw)) if settled_price_raw else None
        notes = str(form.get("notes")) if form.get("notes") else None
        file_field = form.get("file")
        if file_field and hasattr(file_field, "read"):
            settlement_file_bytes = await file_field.read()
            settlement_file_mime = getattr(file_field, "content_type", "image/jpeg") or "image/jpeg"
    else:
        body = await request.json()
        try:
            payload = AnalysisFeedbackRequest.model_validate(body)
        except ValidationError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=error.errors(),
            ) from error
        actual_outcome = payload.actual_outcome
        settled_price = payload.settled_price
        notes = payload.notes

    actual_outcome = _normalize_settlement_outcome(actual_outcome)
    if settled_price is not None and not (Decimal("0") <= settled_price <= Decimal("1")):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="settled_price must be between 0 and 1.",
        )

    existing = await sqlite.get_feedback_by_analysis(str(analysis_id))
    if existing is not None:
        existing_outcome = _normalize_settlement_outcome(str(existing.get("actual_outcome", "")))
        if existing_outcome != actual_outcome:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Settlement feedback already exists for this analysis with a "
                    f"different outcome ({existing_outcome})."
                ),
            )
        return _feedback_response_from_doc(existing)

    forecast_doc = doc.get("forecast") or {}
    recommendation = forecast_doc.get("recommendation", "NO_TRADE")
    market_data = forecast_doc.get("market") or {}
    market_title = market_data.get("title") or "Unknown Market"
    category = market_data.get("category") or "crypto"
    symbol = market_data.get("underlying_symbol") or "BTC"
    prob_yes = float(forecast_doc.get("probability_yes", 0.5))
    prediction_made = f"{recommendation} (P(YES)={prob_yes:.2f})"

    # Run Causal Diagnostic Postmortem Agent (with multimodal screenshot inspection)
    from prediction_ai.learning.postmortem_agent import run_postmortem_analysis

    specialist_context = doc.get("specialist_context") or forecast_doc.get("specialist_context")
    extraction_res = doc.get("extraction_result") or {}
    strike_price = float(str(extraction_res.get("target_price"))) if extraction_res.get("target_price") else None

    postmortem = await run_postmortem_analysis(
        market_title=market_title,
        category=category,
        symbol=symbol,
        recommendation=recommendation,
        probability_yes=prob_yes,
        actual_outcome=actual_outcome,
        settled_price=float(settled_price) if settled_price is not None else None,
        strike_price=strike_price,
        user_notes=notes,
        specialist_context=specialist_context,
        settlement_image_bytes=settlement_file_bytes,
        settlement_image_mime=settlement_file_mime,
        settings=settings,
    )

    was_correct = postmortem.was_correct
    evaluation_eligible = postmortem.evaluation_eligible
    postmortem_analysis = postmortem.postmortem_analysis
    key_learnings = postmortem.key_learnings

    feedback_id = uuid4()
    now_dt = datetime.now(UTC)
    contract_spec = forecast_doc.get("contract_spec") or {}
    feedback_doc = {
        "id": str(feedback_id),
        "feedback_id": str(feedback_id),
        "analysis_id": str(analysis_id),
        "user_id": str(current_user.id),
        "category": category,
        "market_title": market_title,
        "recommendation": recommendation,
        "actual_outcome": actual_outcome,
        "was_correct": was_correct,
        "evaluation_eligible": evaluation_eligible,
        "probability_yes": prob_yes,
        "contract_spec_hash": contract_spec.get("spec_hash"),
        "settled_price": float(settled_price) if settled_price is not None else None,
        "user_notes": notes,
        "postmortem_analysis": postmortem_analysis,
        "visual_observations": postmortem.visual_observations,
        "key_learnings": key_learnings,
        "discrepancy_classification": postmortem.discrepancy_classification,
        "brier_loss": postmortem.brier_loss,
        "has_settlement_screenshot": bool(settlement_file_bytes),
        "created_at": now_dt.isoformat(),
    }
    try:
        await sqlite.record_feedback(feedback_doc)
    except sqlite3.IntegrityError:
        existing = await sqlite.get_feedback_by_analysis(str(analysis_id))
        if existing is not None:
            return _feedback_response_from_doc(existing)
        raise

    # Only update decision/calibration parameters when a recommendation was
    # actually made.  An abstention is not evidence for or against a trade.
    if evaluation_eligible:
        await sqlite.update_asset_calibration(
            symbol=symbol,
            category=category,
            was_correct=was_correct,
            brier_loss=postmortem.brier_loss,
            vol_multiplier_delta=postmortem.vol_multiplier_delta,
            momentum_weight_delta=postmortem.momentum_weight_delta,
            order_flow_weight_delta=postmortem.order_flow_weight_delta,
            uncertainty_margin_delta=postmortem.uncertainty_margin_delta,
            reason=postmortem.discrepancy_classification,
            was_trade=recommendation != "NO_TRADE",
        )

    logger.info(
        "Recorded postmortem feedback for continuous learning",
        analysis_id=str(analysis_id),
        was_correct=was_correct,
        evaluation_eligible=evaluation_eligible,
        recommendation=recommendation,
        actual_outcome=actual_outcome,
        has_screenshot=bool(settlement_file_bytes),
    )

    return AnalysisFeedbackResponse(
        feedback_id=feedback_id,
        analysis_id=analysis_id,
        actual_outcome=actual_outcome,
        was_correct=was_correct,
        evaluation_eligible=evaluation_eligible,
        prediction_made=prediction_made,
        postmortem_analysis=postmortem_analysis,
        visual_observations=postmortem.visual_observations,
        discrepancy_classification=postmortem.discrepancy_classification,
        key_learnings=key_learnings,
        created_at=now_dt,
    )


@router.post(
    "/{analysis_id}/auto-settle",
    response_model=AnalysisFeedbackResponse,
    summary="Automatically resolve contract settlement and trigger causal postmortem learning",
)
async def auto_settle_analysis(
    analysis_id: UUID,
    current_user: AuthenticatedUser = Depends(get_current_user),
    sqlite: SQLiteStore = Depends(get_sqlite),
    settings: Settings = Depends(get_settings),
) -> AnalysisFeedbackResponse:
    """Fetch spot price at resolution time, compute outcome against strike, and train AI models."""
    doc = await sqlite.get_analysis(str(analysis_id))
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Analysis not found")
    _ensure_analysis_access(doc, current_user.id)

    existing = await sqlite.get_feedback_by_analysis(str(analysis_id))
    if existing is not None:
        # Auto-settlement is idempotent: a retry must not fetch a new price,
        # re-run the postmortem, or double-count calibration.
        return _feedback_response_from_doc(existing)

    forecast_doc = doc.get("forecast") or {}
    recommendation = forecast_doc.get("recommendation", "NO_TRADE")
    market_data = forecast_doc.get("market") or {}
    market_title = market_data.get("title") or "Unknown Market"
    category = market_data.get("category") or "crypto"
    symbol = market_data.get("underlying_symbol") or "BTC"
    prob_yes = float(forecast_doc.get("probability_yes", 0.5))
    prediction_made = f"{recommendation} (P(YES)={prob_yes:.2f})"

    specialist_context = doc.get("specialist_context") or forecast_doc.get("specialist_context") or {}
    crypto_res = specialist_context.get("crypto_research") or {}

    extraction_res = doc.get("extraction_result") or {}
    strike_price = None
    if crypto_res.get("strike") is not None:
        strike_price = float(crypto_res["strike"])
    elif extraction_res.get("target_price") is not None:
        strike_price = float(extraction_res["target_price"])

    if strike_price is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot auto-settle: strike target price is unknown for this contract",
        )

    direction = crypto_res.get("direction") or "above"

    # Determine resolution anchor
    resolves_at_dt = None
    timing_prov = forecast_doc.get("timing_provenance") or {}
    if timing_prov.get("exact_resolves_at"):
        try:
            resolves_at_dt = datetime.fromisoformat(timing_prov["exact_resolves_at"])
        except Exception:
            pass

    if resolves_at_dt is not None:
        if resolves_at_dt.tzinfo is None:
            resolves_at_dt = resolves_at_dt.replace(tzinfo=UTC)
        if resolves_at_dt > datetime.now(UTC):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Cannot auto-settle before the contract resolves. "
                    f"Detected resolution time is {resolves_at_dt.isoformat()}. "
                    "Wait for the candle to close, then retry. If the contract has "
                    "already resolved but no countdown was detected, use the manual "
                    "settlement form below."
                ),
            )

    from prediction_ai.specialists.crypto.settlement import (
        determine_binary_outcome,
        fetch_crypto_settlement_price,
    )

    settled_price = await fetch_crypto_settlement_price(symbol, target_time=resolves_at_dt)
    if settled_price is None:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                f"Could not retrieve a verified {symbol} spot price from the multi-venue "
                "exchange mesh (Binance, Coinbase, Kraken, OKX, Bybit, MEXC, Gate.io, "
                "Bitstamp, CoinGecko, DefiLlama). Those venues may be blocked on this "
                "network. You can still report the settlement manually below."
            ),
        )

    label_positive = "YES"
    label_negative = "NO"
    extracted_outcomes = extraction_res.get("outcomes") or []
    if extracted_outcomes and len(extracted_outcomes) >= 2:
        l0 = str(extracted_outcomes[0].get("label", "")).strip().upper()
        if "UP" in l0 or "HIGH" in l0:
            label_positive = "UP"
            label_negative = "DOWN"
    elif category == "crypto":
        label_positive = "UP"
        label_negative = "DOWN"

    actual_outcome = determine_binary_outcome(
        strike_price=strike_price,
        settled_price=settled_price,
        direction=direction,
        label_positive=label_positive,
        label_negative=label_negative,
    )

    from prediction_ai.learning.postmortem_agent import run_postmortem_analysis

    notes = (
        f"Auto-settled via verified exchange mesh: {symbol} spot at "
        f"${settled_price:,.2f} vs strike ${strike_price:,.2f} ({direction})."
    )

    postmortem = await run_postmortem_analysis(
        market_title=market_title,
        category=category,
        symbol=symbol,
        recommendation=recommendation,
        probability_yes=prob_yes,
        actual_outcome=actual_outcome,
        settled_price=settled_price,
        strike_price=strike_price,
        user_notes=notes,
        specialist_context=specialist_context,
        settings=settings,
    )

    was_correct = postmortem.was_correct
    evaluation_eligible = postmortem.evaluation_eligible
    postmortem_analysis = postmortem.postmortem_analysis
    key_learnings = postmortem.key_learnings

    feedback_id = uuid4()
    now_dt = datetime.now(UTC)
    contract_spec = forecast_doc.get("contract_spec") or {}
    feedback_doc = {
        "id": str(feedback_id),
        "feedback_id": str(feedback_id),
        "analysis_id": str(analysis_id),
        "user_id": str(current_user.id),
        "category": category,
        "market_title": market_title,
        "recommendation": recommendation,
        "actual_outcome": actual_outcome,
        "was_correct": was_correct,
        "evaluation_eligible": evaluation_eligible,
        "probability_yes": prob_yes,
        "contract_spec_hash": contract_spec.get("spec_hash"),
        "settled_price": settled_price,
        "user_notes": notes,
        "postmortem_analysis": postmortem_analysis,
        "visual_observations": None,
        "key_learnings": key_learnings,
        "discrepancy_classification": postmortem.discrepancy_classification,
        "brier_loss": postmortem.brier_loss,
        "has_settlement_screenshot": False,
        "created_at": now_dt.isoformat(),
    }
    try:
        await sqlite.record_feedback(feedback_doc)
    except sqlite3.IntegrityError:
        existing = await sqlite.get_feedback_by_analysis(str(analysis_id))
        if existing is not None:
            return _feedback_response_from_doc(existing)
        raise

    if evaluation_eligible:
        await sqlite.update_asset_calibration(
            symbol=symbol,
            category=category,
            was_correct=was_correct,
            brier_loss=postmortem.brier_loss,
            vol_multiplier_delta=postmortem.vol_multiplier_delta,
            momentum_weight_delta=postmortem.momentum_weight_delta,
            order_flow_weight_delta=postmortem.order_flow_weight_delta,
            uncertainty_margin_delta=postmortem.uncertainty_margin_delta,
            reason=postmortem.discrepancy_classification,
            was_trade=recommendation != "NO_TRADE",
        )

    logger.info(
        "Auto-settled analysis successfully",
        analysis_id=str(analysis_id),
        settled_price=settled_price,
        strike_price=strike_price,
        actual_outcome=actual_outcome,
        was_correct=was_correct,
        evaluation_eligible=evaluation_eligible,
    )

    return AnalysisFeedbackResponse(
        feedback_id=feedback_id,
        analysis_id=analysis_id,
        actual_outcome=actual_outcome,
        was_correct=was_correct,
        evaluation_eligible=evaluation_eligible,
        prediction_made=prediction_made,
        postmortem_analysis=postmortem_analysis,
        visual_observations=None,
        discrepancy_classification=postmortem.discrepancy_classification,
        key_learnings=key_learnings,
        created_at=now_dt,
    )
