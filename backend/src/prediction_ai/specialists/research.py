from datetime import UTC, datetime
from typing import Any

import httpx
import structlog

from prediction_ai.db.sqlite import get_sqlite_db
from prediction_ai.specialists.public_sports_sources import PublicSportsSourceClient
from prediction_ai.schemas.research import SportsEvidencePack

logger = structlog.get_logger(__name__)


class SpecialistResearchService:
    """Gathers specialist domain context, market intervals, patterns, and historical feedback."""

    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        self._client = http_client

    async def gather_context(
        self,
        category: str,
        asset: str | None,
        title: str,
        time_to_close_seconds: float | None = None,
        target_price: float | None = None,
        displayed_underlying_price: float | None = None,
    ) -> dict[str, Any]:
        """Gather domain-specific research context based on market category."""
        sqlite = get_sqlite_db()
        learnings = await sqlite.get_recent_learnings(category=category, limit=3)

        context: dict[str, Any] = {
            "category": category,
            "asset": asset or "General",
            "researched_at": datetime.now(UTC).isoformat(),
            "prior_learnings": [
                {
                    "lesson": item.get("key_learnings", []),
                    "postmortem": item.get("postmortem_analysis"),
                    "was_correct": item.get("was_correct"),
                }
                for item in learnings
            ],
        }

        if category == "crypto":
            context["crypto_research"] = await self._gather_crypto_research(
                asset,
                title,
                time_to_close_seconds,
                target_price=target_price,
                displayed_spot_price=displayed_underlying_price,
            )
        elif category == "sports":
            context["sports_research"] = await self._gather_sports_research(title)
        else:
            context["macro_research"] = self._gather_macro_research(title)

        return context

    async def _gather_crypto_research(
        self,
        asset: str | None,
        title: str,
        time_to_close: float | None,
        target_price: float | None = None,
        displayed_spot_price: float | None = None,
    ) -> dict[str, Any]:
        """Gather real crypto intelligence: OHLCV, order-book, derivatives signals,
        Fear & Greed, and a mathematically grounded log-normal P(YES)."""
        from datetime import timedelta

        from prediction_ai.specialists.crypto.research import gather_crypto_intelligence

        closes_at: datetime | None = None
        if time_to_close is not None:
            closes_at = datetime.now(UTC) + timedelta(seconds=time_to_close)

        return await gather_crypto_intelligence(
            title=title,
            underlying_symbol=asset,
            target_price=target_price,
            displayed_spot_price=displayed_spot_price,
            closes_at=closes_at,
            http_client=self._client,
        )

    async def _gather_sports_research(self, title: str) -> dict[str, Any]:
        subject = title.strip()[:255] or "Unknown sports proposition"
        cutoff_at = datetime.now(UTC)
        claims: list[dict[str, Any]] = []
        if self._client is not None:
            claims = await PublicSportsSourceClient(self._client).search(subject, cutoff_at)
        pack = SportsEvidencePack(
            proposition_type="unclassified_sports_market",
            cutoff_at=cutoff_at,
            subject=subject,
            claims=claims,
            current_status="unresolved",
            resolution_definition_verified=False,
            deadline_verified=False,
            quality_flags=[
                "PUBLIC_NEWS_AND_SOCIAL_SEARCH",
                "SPORTS_TARGET_NOT_CLASSIFIED",
                "RESOLUTION_DEFINITION_REQUIRED",
            ],
            recommendation_gate="abstain",
        )
        logger.warning(
            "Sports research collected public evidence but abstained pending target verification",
            proposition=subject,
            claim_count=len(claims),
        )
        return pack.model_dump(mode="json")

    def _gather_macro_research(self, title: str) -> dict[str, Any]:
        return {
            "discipline": "Macro / News Event",
            "research_indicators": {
                "source_credibility_weight": "official regulatory or verified news channel required",
                "headline_volatility_risk": "elevated around scheduled release window",
            },
        }
