"""Automated settlement resolver for crypto prediction market intervals.

Resolves the spot price at contract expiry and determines whether the contract
resolved YES/NO (or UP/DOWN) from the strike price and direction.

Settlement rules
----------------
* If ``target_time`` is in the past, only a timestamp-validated historical
  candle is accepted.  A live price is never substituted for a historical
  settlement when the target is materially older than the current instant,
  because that would silently settle the contract at the wrong time.
* If the target is within a short grace window (or absent/future), the live
  cross-venue spot mesh is used.
* If every venue fails, ``None`` is returned so the API can answer with an
  explicit error and preserve the manual settlement workflow.  A price is never
  fabricated from the strike.

The exchange mesh lives in :mod:`prediction_ai.specialists.crypto.venues`; this
module only applies the settlement-specific time policy.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import structlog

from prediction_ai.core.config import Settings, get_settings
from prediction_ai.integrations.network import build_resilient_client
from prediction_ai.specialists.crypto.venues import (
    fetch_historical_spot,
    fetch_spot_snapshot,
)

logger = structlog.get_logger(__name__)

_SYMBOL_MAP: dict[str, str] = {
    "btc": "BTC",
    "bitcoin": "BTC",
    "eth": "ETH",
    "ethereum": "ETH",
    "sol": "SOL",
    "solana": "SOL",
    "xrp": "XRP",
    "ripple": "XRP",
    "bnb": "BNB",
    "doge": "DOGE",
    "dogecoin": "DOGE",
    "ada": "ADA",
    "cardano": "ADA",
    "avax": "AVAX",
    "avalanche": "AVAX",
    "link": "LINK",
    "chainlink": "LINK",
}

# A target this recent can reasonably use the current live price when the
# historical candle provider is unavailable.  Older targets must abstain.
_LIVE_FALLBACK_GRACE_SECONDS = 1800.0


def normalize_crypto_symbol(symbol_or_name: str | None) -> str:
    """Normalize an input asset string to a clean uppercase ticker (e.g. 'BTC')."""
    if not symbol_or_name:
        return "BTC"
    cleaned = symbol_or_name.strip().lower()
    return _SYMBOL_MAP.get(cleaned, symbol_or_name.strip().upper())


async def fetch_crypto_settlement_price(
    symbol: str,
    target_time: datetime | None = None,
    client: httpx.AsyncClient | None = None,
    settings: Settings | None = None,
) -> float | None:
    """Fetch a verified spot price for a crypto contract's resolution time.

    The same resilient DNS/DoH transport and multi-venue mesh used by research
    is used here, so settlement cannot fail merely because the system resolver
    blocks exchange domains.
    """
    norm_symbol = normalize_crypto_symbol(symbol)
    resolved_settings = settings or get_settings()
    budget = resolved_settings.MARKET_DATA_GROUP_BUDGET_SECONDS
    owns_client = client is None
    local_client = client or build_resilient_client(resolved_settings)

    try:
        if target_time is not None:
            if target_time.tzinfo is None:
                target_time = target_time.replace(tzinfo=UTC)
            target_time = target_time.astimezone(UTC)
            age_seconds = (datetime.now(UTC) - target_time).total_seconds()

            if age_seconds >= 0:
                historical = await fetch_historical_spot(
                    local_client,
                    norm_symbol,
                    target_time,
                    budget_seconds=budget,
                )
                if historical is not None:
                    logger.info(
                        "Resolved historical settlement price",
                        symbol=norm_symbol,
                        target_time=target_time.isoformat(),
                        settlement_price=historical.price,
                        observed_at=historical.observed_at.isoformat(),
                        source=historical.venue,
                    )
                    return historical.price

                if age_seconds > _LIVE_FALLBACK_GRACE_SECONDS:
                    logger.warning(
                        "Historical settlement price unavailable; refusing to substitute "
                        "a much-later live price",
                        symbol=norm_symbol,
                        target_time=target_time.isoformat(),
                        age_seconds=round(age_seconds, 1),
                        grace_seconds=_LIVE_FALLBACK_GRACE_SECONDS,
                    )
                    return None

                logger.warning(
                    "Historical settlement price unavailable; target is within the "
                    "live fallback grace window",
                    symbol=norm_symbol,
                    target_time=target_time.isoformat(),
                    age_seconds=round(age_seconds, 1),
                )

        spot = await fetch_spot_snapshot(
            local_client,
            norm_symbol,
            budget_seconds=budget,
        )
        if spot is not None and spot.price > 0:
            logger.info(
                "Resolved settlement price from live cross-venue spot mesh",
                symbol=norm_symbol,
                price=spot.price,
                source=spot.source,
                venues=[quote.venue for quote in spot.quotes],
                dispersion_bps=round(spot.dispersion_bps, 2),
            )
            return spot.price

        logger.warning(
            "All settlement price venues failed",
            symbol=norm_symbol,
            target_time=target_time.isoformat() if target_time else None,
        )
        return None
    finally:
        if owns_client:
            await local_client.aclose()


def determine_binary_outcome(
    strike_price: float,
    settled_price: float,
    direction: str = "above",
    label_positive: str = "YES",
    label_negative: str = "NO",
) -> str:
    """Determine whether a binary market resolved positive or negative.

    Args:
        strike_price: Contract target price K.
        settled_price: Spot price at expiry S_T.
        direction: 'above' (default) or 'below'.
        label_positive: Label for affirmative outcome (default 'YES' or 'UP').
        label_negative: Label for negative outcome (default 'NO' or 'DOWN').
    """
    is_below = direction.strip().lower() == "below"
    if is_below:
        return label_positive if settled_price < strike_price else label_negative
    return label_positive if settled_price >= strike_price else label_negative
