import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prediction_ai.api.deps import get_current_user, get_db, get_mongo, get_sqlite
from prediction_ai.auth.jwt import AuthenticatedUser
from prediction_ai.db.models.forecast import Forecast, ForecastEvidence
from prediction_ai.db.models.market import PredictionMarket
from prediction_ai.db.mongo import MongoDBStore
from prediction_ai.db.sqlite import SQLiteStore
from prediction_ai.schemas.contract import ContractSpec
from prediction_ai.schemas.forecast import EvidenceItem, ForecastResponse, MarketSummary
from prediction_ai.schemas.market import NormalizedOutcome

router = APIRouter(prefix="/forecasts", tags=["Forecasts"])

logger = structlog.get_logger(__name__)


def _stored_contract_spec(payload: Any, forecast_id: uuid.UUID) -> ContractSpec | None:
    """Parse a persisted contract spec, logging malformed snapshots explicitly."""
    if not isinstance(payload, dict) or not payload:
        return None
    try:
        return ContractSpec.model_validate(payload)
    except ValidationError as error:
        logger.warning(
            "Stored contract specification failed validation",
            forecast_id=str(forecast_id),
            error=str(error),
        )
        return None


@router.get("/{forecast_id}", response_model=ForecastResponse)
async def get_forecast(
    forecast_id: uuid.UUID,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession | None = Depends(get_db),
    sqlite: SQLiteStore = Depends(get_sqlite),
    mongo: MongoDBStore = Depends(get_mongo),
) -> ForecastResponse:
    """Fetch an immutable calibrated forecast snapshot with itemized documentary evidence."""
    # 1. Check SQLite store first
    f_doc = await sqlite.get_forecast(str(forecast_id))
    if f_doc is None:
        try:
            f_doc = await mongo.get_forecast(str(forecast_id))
        except Exception:
            f_doc = None

    if f_doc is not None:
        m_doc = f_doc.get("market") or {}
        market_summary = MarketSummary(
            id=uuid.uuid4(),
            platform=m_doc.get("platform", "polymarket"),
            platform_market_id=m_doc.get("platform_market_id", ""),
            slug=m_doc.get("slug", ""),
            url=m_doc.get("url"),
            title=m_doc.get("title", ""),
            category=m_doc.get("category", "crypto"),
            market_type=m_doc.get("market_type", "binary"),
            underlying_symbol=m_doc.get("underlying_symbol"),
            outcomes=[],
            resolves_at=datetime.fromisoformat(m_doc["resolves_at"]) if m_doc.get("resolves_at") else None,
            resolution_source=m_doc.get("resolution_source"),
            resolution_criteria=m_doc.get("resolution_criteria"),
            status=m_doc.get("status", "ACTIVE"),
        )
        evidence_items = [
            EvidenceItem(
                evidence_id=str(ev.get("evidence_id") or uuid.uuid4()),
                evidence_type=ev.get("evidence_type", "MICROSTRUCTURE"),
                stance=ev.get("stance", "NEUTRAL"),
                relevance_score=Decimal(str(ev.get("relevance_score", 0.5))),
                snippet=ev.get("snippet", ""),
                source_citation=ev.get("source_citation", ""),
                source_url=ev.get("source_url"),
                source_timestamp=datetime.fromisoformat(ev["source_timestamp"]) if ev.get("source_timestamp") else None,
            )
            for ev in f_doc.get("evidence", [])
        ]
        return ForecastResponse(
            id=forecast_id,
            analysis_id=uuid.UUID(f_doc.get("analysis_id", str(forecast_id))),
            market=market_summary,
            forecasted_at=datetime.fromisoformat(f_doc["forecasted_at"]) if f_doc.get("forecasted_at") else datetime.now(UTC),
            probability_yes=Decimal(str(f_doc.get("probability_yes", 0.5))),
            probability_no=Decimal(str(f_doc.get("probability_no", 0.5))),
            uncertainty_interval=[Decimal(str(x)) for x in f_doc.get("uncertainty_interval", [0.4, 0.6])],
            recommendation=f_doc.get("recommendation", "NO_TRADE"),
            decision_metrics=f_doc.get("decision_metrics", {}),
            data_quality=f_doc.get("data_quality", {}),
            model_version=f_doc.get("model_version", "v1"),
            calibration_version=f_doc.get("calibration_version", "v1"),
            prompt_version=f_doc.get("prompt_version", "v1"),
            decision_policy_version=f_doc.get("decision_policy_version", "v1"),
            explanation=f_doc.get("explanation", {}),
            evidence=evidence_items,
            input_snapshot=f_doc.get("input_snapshot", {}),
            contract_spec=_stored_contract_spec(f_doc.get("contract_spec"), forecast_id),
            feature_snapshot=f_doc.get("feature_snapshot") or {},
            model_components=f_doc.get("model_components") or {},
        )

    # 2. Check SQL database if available
    if db is not None:
        forecast = await db.get(Forecast, forecast_id)
        if forecast is not None and (forecast.user_id == current_user.id or current_user.tier == "service_role"):
            market = await db.get(PredictionMarket, forecast.market_id)
            if market is not None:
                raw_outcomes: list[Any] = market.outcomes if isinstance(market.outcomes, list) else []
                outcomes = [
                    NormalizedOutcome(
                        outcome_id=str(o.get("outcome_id", "tok_yes")),
                        token_id=str(o.get("token_id", "tok_yes")),
                        label=str(o.get("label", "Yes")),
                    )
                    for o in raw_outcomes
                ]

                market_summary = MarketSummary(
                    id=market.id,
                    platform=market.platform,
                    platform_market_id=market.platform_market_id,
                    slug=market.slug,
                    url=market.url,
                    title=market.title,
                    category=market.category,
                    market_type=market.market_type,
                    underlying_symbol=market.underlying_symbol,
                    outcomes=outcomes,
                    resolves_at=market.resolves_at,
                    resolution_source=market.resolution_source,
                    resolution_criteria=market.resolution_criteria,
                    status=market.status,
                )

                ev_stmt = select(ForecastEvidence).where(ForecastEvidence.forecast_id == forecast_id)
                ev_result = await db.execute(ev_stmt)
                evidence_rows = ev_result.scalars().all()

                evidence_items = [
                    EvidenceItem(
                        evidence_id=str(ev.id),
                        evidence_type=ev.evidence_type,
                        stance=ev.stance,
                        relevance_score=ev.relevance_score,
                        snippet=ev.snippet,
                        source_citation=ev.source_citation,
                        source_url=ev.source_url,
                        source_timestamp=ev.source_timestamp,
                    )
                    for ev in evidence_rows
                ]

                return ForecastResponse(
                    id=forecast.id,
                    analysis_id=forecast.analysis_id,
                    market=market_summary,
                    forecasted_at=forecast.forecasted_at,
                    probability_yes=forecast.probability_yes,
                    probability_no=forecast.probability_no,
                    uncertainty_interval=forecast.uncertainty_interval or [],
                    recommendation=forecast.recommendation,  # type: ignore[arg-type]
                    decision_metrics=forecast.decision_metrics,  # type: ignore[arg-type]
                    data_quality=forecast.data_quality,
                    model_version=forecast.model_version,
                    calibration_version=forecast.calibration_version,
                    prompt_version=forecast.prompt_version,
                    decision_policy_version=forecast.decision_policy_version,
                    explanation=forecast.explanation,
                    evidence=evidence_items,
                    input_snapshot=forecast.input_snapshot,
                )

    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Forecast not found")
