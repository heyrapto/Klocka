import asyncio
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
    EventEnvelope,
    MarketConfirmationRequest,
)
from prediction_ai.schemas.forecast import ForecastSummary, MarketSummary
from prediction_ai.schemas.market import NormalizedOutcome
from prediction_ai.storage.factory import create_storage
from prediction_ai.vision.images import validate_and_normalize_image

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/analyses", tags=["Analyses"])


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

    analysis_record = {
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
                yield format_sse_message(ev.get("event_type", "analysis.stage"), ev.get("payload", {}), last_seq)

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
    payload: AnalysisFeedbackRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
    sqlite: SQLiteStore = Depends(get_sqlite),
    settings: Settings = Depends(get_settings),
) -> AnalysisFeedbackResponse:
    """Submit post-settlement trade feedback for continuous learning and self-improvement."""
    doc = await sqlite.get_analysis(str(analysis_id))
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Analysis not found")

    forecast_doc = doc.get("forecast") or {}
    recommendation = forecast_doc.get("recommendation", "NO_TRADE")
    market_data = forecast_doc.get("market") or {}
    market_title = market_data.get("title") or "Unknown Market"
    category = market_data.get("category") or "crypto"

    # Evaluate whether prediction was correct
    if recommendation == "BUY_YES":
        was_correct = (payload.actual_outcome == "YES")
    elif recommendation == "BUY_NO":
        was_correct = (payload.actual_outcome == "NO")
    else:  # NO_TRADE (prudent abstention)
        was_correct = True

    prob_yes = float(forecast_doc.get("probability_yes", 0.5))
    prediction_made = f"{recommendation} (P(YES)={prob_yes:.2f})"

    if was_correct:
        postmortem_analysis = (
            f"Prediction successfully aligned with market resolution ({payload.actual_outcome}). "
            f"Confluence of order book momentum and specialist intervals validated."
        )
        key_learnings = [
            f"Validation successful on {category.upper()}: order book momentum and interval depth accurately indicated outcome.",
            "Preserve conservative Kelly sizing discipline and minimum net edge threshold.",
        ]
    else:
        postmortem_analysis = (
            f"Prediction discrepancy identified: recommended {recommendation} but market settled as {payload.actual_outcome}. "
            f"Likely driven by late interval volatility, tail news event, or order book liquidity evaporation."
        )
        key_learnings = [
            f"For future {category.upper()} intervals, increase penalty on thin spread liquidity near resolution.",
            "Expand uncertainty interval width when conflicting order flow or cross-venue price divergence appears.",
        ]

    if payload.notes:
        postmortem_analysis += f" User observation: {payload.notes}"

    feedback_id = uuid4()
    now_dt = datetime.now(UTC)
    feedback_doc = {
        "id": str(feedback_id),
        "feedback_id": str(feedback_id),
        "analysis_id": str(analysis_id),
        "user_id": str(current_user.id),
        "category": category,
        "market_title": market_title,
        "recommendation": recommendation,
        "actual_outcome": payload.actual_outcome,
        "was_correct": was_correct,
        "settled_price": float(payload.settled_price) if payload.settled_price is not None else None,
        "user_notes": payload.notes,
        "postmortem_analysis": postmortem_analysis,
        "key_learnings": key_learnings,
        "created_at": now_dt.isoformat(),
    }
    await sqlite.record_feedback(feedback_doc)

    logger.info(
        "Recorded postmortem feedback for continuous learning",
        analysis_id=str(analysis_id),
        was_correct=was_correct,
        recommendation=recommendation,
        actual_outcome=payload.actual_outcome,
    )

    return AnalysisFeedbackResponse(
        feedback_id=feedback_id,
        analysis_id=analysis_id,
        actual_outcome=payload.actual_outcome,
        was_correct=was_correct,
        prediction_made=prediction_made,
        postmortem_analysis=postmortem_analysis,
        key_learnings=key_learnings,
        created_at=now_dt,
    )
