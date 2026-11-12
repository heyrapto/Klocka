"""Historical and point-in-time market price capture.

Polymarket exposes a public CLOB price-history endpoint.  Bayse does not
publish a documented historical-price endpoint, so the platform-neutral path is
to persist every order-book snapshot the decision pipeline already observes.
That yields a genuine, non-fabricated price path for Bayse going forward and a
backfill path for Polymarket.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import structlog

from prediction_ai.core.config import Settings
from prediction_ai.integrations.http import ProviderHttpClient
from prediction_ai.schemas.market import (
    NormalizedMarket,
    NormalizedOrderBook,
    OutcomeQuote,
)
from prediction_ai.schemas.price_history import (
    MAX_HISTORY_POINTS,
    OutcomePriceHistory,
    PricePoint,
)

logger = structlog.get_logger(__name__)


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _epoch_datetime(value: Any) -> datetime | None:
    try:
        numeric = float(str(value))
    except (TypeError, ValueError):
        return None
    if numeric > 10_000_000_000:
        numeric /= 1000.0
    try:
        return datetime.fromtimestamp(numeric, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _downsample(points: list[PricePoint], maximum: int) -> list[PricePoint]:
    """Evenly reduce a long path while preserving both endpoints."""
    if maximum < 2 or len(points) <= maximum:
        return points[:maximum]
    step = (len(points) - 1) / (maximum - 1)
    indices = sorted({round(index * step) for index in range(maximum)})
    return [points[index] for index in indices]


class PolymarketPriceHistoryClient:
    """Read-only client for the public CLOB ``/prices-history`` endpoint."""

    def __init__(
        self,
        settings: Settings,
        clob_client: ProviderHttpClient,
    ) -> None:
        self._settings = settings
        self._clob = clob_client

    async def fetch_outcome_history(
        self,
        *,
        platform_market_id: str,
        outcome_id: str,
        token_id: str,
        label: str | None,
        start: datetime,
        end: datetime,
        fidelity_minutes: int | None = None,
    ) -> OutcomePriceHistory:
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("start and end must be timezone-aware")
        if end <= start:
            raise ValueError("end must be later than start")
        fidelity = (
            fidelity_minutes
            if fidelity_minutes is not None
            else self._settings.PRICE_HISTORY_DEFAULT_FIDELITY_MINUTES
        )
        if not 1 <= fidelity <= 1440:
            raise ValueError("fidelity_minutes must be between 1 and 1440")

        body = await self._clob.get_json(
            "/prices-history",
            params={
                "market": token_id,
                "startTs": int(start.timestamp()),
                "endTs": int(end.timestamp()),
                "fidelity": fidelity,
            },
        )
        raw_points = body.get("history") if isinstance(body, dict) else body
        if not isinstance(raw_points, list):
            logger.warning(
                "Unexpected price-history payload",
                platform_market_id=platform_market_id,
                token_id=token_id,
            )
            raw_points = []

        points: list[PricePoint] = []
        for raw in raw_points:
            if not isinstance(raw, dict):
                continue
            observed_at = _epoch_datetime(raw.get("t"))
            price = _decimal(raw.get("p"))
            if observed_at is None or price is None:
                continue
            try:
                points.append(
                    PricePoint(
                        observed_at=observed_at,
                        price=price,
                        kind="mid",
                        source="polymarket_clob_prices_history",
                    )
                )
            except ValueError:
                continue

        maximum = self._settings.PRICE_HISTORY_MAX_POINTS
        bounded = _downsample(points, min(maximum, MAX_HISTORY_POINTS))
        return OutcomePriceHistory(
            platform="polymarket",
            platform_market_id=platform_market_id,
            outcome_id=outcome_id,
            token_id=token_id,
            label=label,
            granularity_seconds=max(1, fidelity * 60),
            points=bounded,
        )


def quote_midpoint(quote: OutcomeQuote) -> Decimal | None:
    """Return the midpoint of an executable quote, never a synthetic anchor."""
    if quote.best_bid is not None and quote.best_ask is not None:
        midpoint = (quote.best_bid + quote.best_ask) / Decimal("2")
        return midpoint if Decimal("0") < midpoint < Decimal("1") else None
    if quote.mid_price is not None and Decimal("0") < quote.mid_price < Decimal("1"):
        return quote.mid_price
    if quote.best_bid is not None and Decimal("0") < quote.best_bid < Decimal("1"):
        return quote.best_bid
    if quote.best_ask is not None and Decimal("0") < quote.best_ask < Decimal("1"):
        return quote.best_ask
    return None


def order_book_to_price_histories(
    market: NormalizedMarket,
    book: NormalizedOrderBook,
) -> list[OutcomePriceHistory]:
    """Convert the observed decision-time book into persistable price points."""
    by_outcome = {quote.outcome_id: quote for quote in book.quotes}
    source = f"{book.platform}_clob_snapshot"
    histories: list[OutcomePriceHistory] = []
    for outcome in market.outcomes:
        quote = by_outcome.get(outcome.outcome_id)
        if quote is None:
            continue
        midpoint = quote_midpoint(quote)
        if midpoint is None:
            continue
        histories.append(
            OutcomePriceHistory(
                platform=book.platform,
                platform_market_id=book.platform_market_id,
                outcome_id=outcome.outcome_id,
                token_id=outcome.token_id,
                label=outcome.label,
                granularity_seconds=60,
                points=[
                    PricePoint(
                        observed_at=book.observed_at,
                        price=midpoint,
                        kind="mid",
                        source=source,  # type: ignore[arg-type]
                    )
                ],
            )
        )
    return histories


__all__ = [
    "PolymarketPriceHistoryClient",
    "order_book_to_price_histories",
    "quote_midpoint",
]
