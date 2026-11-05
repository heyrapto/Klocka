import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from prediction_ai.api.deps import get_current_user, get_db
from prediction_ai.auth.jwt import AuthenticatedUser
from prediction_ai.db.models.market import PredictionMarket
from prediction_ai.schemas.forecast import MarketSummary
from prediction_ai.schemas.market import NormalizedOutcome

router = APIRouter(prefix="/markets", tags=["Markets"])


@router.get("/{market_id}", response_model=MarketSummary)
async def get_market(
    market_id: uuid.UUID,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MarketSummary:
    """Fetch verified market contract specifications and settlement criteria."""
    market = await db.get(PredictionMarket, market_id)
    if market is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Market not found")

    raw_outcomes: list[Any] = market.outcomes if isinstance(market.outcomes, list) else []
    outcomes = [
        NormalizedOutcome(
            outcome_id=str(o.get("outcome_id", "tok_yes")),
            token_id=str(o.get("token_id", "tok_yes")),
            label=str(o.get("label", "Yes")),
        )
        for o in raw_outcomes
    ]

    return MarketSummary(
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
