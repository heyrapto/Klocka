"""Crypto specialist research service with resilient multi-venue fallbacks.

Gathers live market data from a mesh of official public exchange and aggregator
APIs, validates the result across venues, and computes a mathematically grounded
probability via log-normal digital option pricing.

Data-gathering strategy
-----------------------
Every data group (spot, 15m candles, 1h candles, order book, derivatives, fear
and greed) runs concurrently.  Within each group the venues are also raced
concurrently.  A full-outage therefore costs roughly one group budget rather
than one timeout per venue in a sequential waterfall.

Spot resolution order
---------------------
1. Cross-venue live spot mesh (short settle window + median + outlier rejection).
2. Most recent closed 15m candle close.
3. Most recent closed 1h candle close.
4. Screenshot point-in-time spot extracted by vision.
5. ``DATA_UNAVAILABLE`` (never a fabricated price).

The live path uses official public REST APIs.  Exchange websites are not
scraped: their front-ends are JavaScript applications protected by bot
management, their DOM changes without notice, and the same data is already
published through documented public API programmes.
"""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from typing import Any, cast

import httpx
import structlog

from prediction_ai.core.config import Settings, get_settings
from prediction_ai.integrations.network import build_resilient_client
from prediction_ai.specialists.crypto.features import (
    build_features,
    interpret_funding_rate,
    realized_vol_annualized,
)
from prediction_ai.specialists.crypto.venues import (
    BookSnapshot,
    DerivativesSnapshot,
    FearGreedSnapshot,
    FuturesPremiumSnapshot,
    OhlcvSnapshot,
    SpotSnapshot,
    fetch_derivatives_snapshot,
    fetch_fear_and_greed,
    fetch_futures_premium,
    fetch_ohlcv_1m_snapshot,
    fetch_ohlcv_snapshot,
    fetch_order_book_snapshot,
    fetch_spot_snapshot,
)

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Symbol maps
# ---------------------------------------------------------------------------

_SYMBOL_MAP: dict[str, str] = {
    "btc": "BTC", "bitcoin": "BTC",
    "eth": "ETH", "ethereum": "ETH",
    "sol": "SOL", "solana": "SOL",
    "xrp": "XRP", "ripple": "XRP",
    "bnb": "BNB",
    "doge": "DOGE", "dogecoin": "DOGE",
    "ada": "ADA", "cardano": "ADA",
    "avax": "AVAX", "avalanche": "AVAX",
    "matic": "MATIC", "polygon": "MATIC", "pol": "MATIC",
    "link": "LINK", "chainlink": "LINK",
    "dot": "DOT", "polkadot": "DOT",
    "ltc": "LTC", "litecoin": "LTC",
    "near": "NEAR",
    "arb": "ARB", "arbitrum": "ARB",
    "sui": "SUI",
    "apt": "APT",
    "pepe": "PEPE",
}

_ASSET_BASE_VOL: dict[str, float] = {
    "BTC": 0.55,
    "ETH": 0.65,
    "SOL": 0.85,
    "XRP": 0.90,
    "DOGE": 1.10,
    "AVAX": 0.95,
    "LINK": 0.80,
    "SUI": 1.05,
}

_SPOT_DISPERSION_WARNING_BPS = 50.0
_ORDER_BOOK_MAX_DEVIATION = 0.02


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _infer_symbol(title: str, hint: str | None = None) -> str:
    """Extract the most likely trading symbol from a market title."""
    text = (title + " " + (hint or "")).lower()
    for keyword, sym in _SYMBOL_MAP.items():
        if keyword in text:
            return sym
    match = re.search(r"\b([A-Z]{2,6})\b(?:/USD|USDT|USD)?", title)
    if match and match.group(1) in set(_SYMBOL_MAP.values()):
        return match.group(1)
    return "BTC"


_BELOW_TOKENS = (
    "below",
    "less than",
    "under",
    "lower than",
    "sub-",
    "drop to",
    "drops to",
    "down to",
)


def _contains_token(text: str, tokens: tuple[str, ...]) -> bool:
    """Return True when any token appears as a standalone phrase in ``text``."""
    for token in tokens:
        if re.search(rf"(?<![a-z]){re.escape(token)}(?![a-z])", text):
            return True
    return False


def _parse_strike_and_direction(
    title: str,
    direction: str | None = None,
) -> tuple[float | None, str]:
    """Extract the strike price and comparison direction from a market title.

    Returns ``(strike_price, direction)`` where direction is ``"above"`` or
    ``"below"``.  ``direction`` (when supplied) is authoritative: the market's
    own outcome labels and contract spec take precedence over free-text title
    parsing.

    Titles such as ``"Bitcoin Up or Down - 15 minutes?"`` are orientation
    agnostic: the affirmative outcome is ``Up``, i.e. the settlement price is
    compared *above* the interval's opening strike.  Treating the trailing word
    ``"Down"`` as a below-threshold contract inverts the probability and was a
    production bug.
    """
    title_norm = title.replace(",", "").replace("$", "").lower()

    if direction in ("above", "below"):
        resolved_direction = direction
    elif "up or down" in title_norm or "higher or lower" in title_norm:
        resolved_direction = "above"
    elif _contains_token(title_norm, _BELOW_TOKENS):
        resolved_direction = "below"
    else:
        resolved_direction = "above"
    direction = resolved_direction

    patterns = [
        r"(?:above|below|reach|hit|over|under|at|to)\s+(\d+(?:\.\d+)?)\s*k\b",
        r"\b(\d+(?:\.\d+)?)\s*k\b",
        r"(?:above|below|reach|hit|over|under|at|to)\s+(\d{2,7}(?:\.\d+)?)",
        r"\b(\d{4,7}(?:\.\d+)?)\b",
    ]

    for pat in patterns:
        match = re.search(pat, title_norm)
        if not match:
            continue
        try:
            value = float(match.group(1))
        except ValueError:
            continue
        if "k" in pat:
            value *= 1000.0
        if value > 0:
            return value, direction

    return None, direction


async def _load_calibration(symbol: str) -> dict[str, Any]:
    """Load the learned asset calibration matrix from SQLite."""
    try:
        from prediction_ai.db.sqlite import get_sqlite_db  # noqa: PLC0415

        sqlite = get_sqlite_db()
        calibration = await sqlite.get_asset_calibration(symbol)
        if calibration:
            return calibration
    except Exception as exc:  # noqa: BLE001 - calibration is optional
        logger.warning(
            "Could not load asset calibration",
            symbol=symbol,
            error=type(exc).__name__,
            error_detail=str(exc) or None,
        )
    return {
        "symbol": symbol.upper(),
        "vol_multiplier": 1.0,
        "momentum_weight": 0.35,
        "order_flow_weight": 0.30,
        "uncertainty_margin": 0.02,
    }


async def _load_vol_multiplier(symbol: str) -> float:
    """Load the learned volatility calibration, defaulting to 1.0."""
    calibration = await _load_calibration(symbol)
    value = float(calibration.get("vol_multiplier", 1.0))
    if 0.25 <= value <= 4.0:
        return value
    return 1.0


def _merge_flags(*groups: list[str] | tuple[str, ...]) -> list[str]:
    seen: set[str] = set()
    merged: list[str] = []
    for group in groups:
        for flag in group:
            if flag and flag not in seen:
                seen.add(flag)
                merged.append(flag)
    return merged


def _candle_close_iso(candle: dict[str, float], interval_seconds: int) -> str:
    close_ms = int(candle["t"]) + interval_seconds * 1000
    return datetime.fromtimestamp(close_ms / 1000.0, tz=UTC).isoformat()


def _downgrade_confidence(confidence: str) -> str:
    if confidence == "high":
        return "medium"
    if confidence == "medium":
        return "low"
    return confidence


def _ohlcv_label(snapshot: OhlcvSnapshot | None) -> str | None:
    if snapshot is None or not snapshot.candles:
        return None
    return f"{snapshot.venue} ({len(snapshot.candles)} candles)"


def _usable_book(
    book: BookSnapshot | None,
    spot: float,
    flags: list[str],
) -> tuple[list[tuple[float, float]], list[tuple[float, float]], BookSnapshot | None]:
    """Reject a book whose midpoint diverged from the verified spot anchor."""
    if book is None or not book.bids or not book.asks:
        flags.append("ORDER_BOOK_UNAVAILABLE")
        return [], [], None

    mid = (book.bids[0][0] + book.asks[0][0]) / 2.0
    if spot > 0 and abs(mid - spot) / spot > _ORDER_BOOK_MAX_DEVIATION:
        flags.append("ORDER_BOOK_REJECTED_DIVERGENT_MID")
        logger.warning(
            "Rejected crypto order book whose midpoint diverged from spot",
            venue=book.venue,
            mid=mid,
            spot=spot,
            deviation_pct=round((mid - spot) / spot * 100.0, 3),
        )
        return [], [], None

    return book.bids, book.asks, book


def _resolve_spot(
    *,
    symbol: str,
    spot_snapshot: SpotSnapshot | None,
    ohlcv_15m: OhlcvSnapshot | None,
    ohlcv_1h: OhlcvSnapshot | None,
    displayed_spot_price: float | None,
    flags: list[str],
) -> tuple[float, str, str | None, bool, dict[str, float], float | None, int]:
    """Apply the documented spot fallback order.

    Returns ``(spot, source, observed_at, screenshot_anchored, quotes,
    dispersion_bps, venue_count)``.
    """
    if spot_snapshot is not None and spot_snapshot.price > 0:
        quotes = {quote.venue: quote.price for quote in spot_snapshot.quotes}
        observed_at = (
            min(quote.observed_at for quote in spot_snapshot.quotes).isoformat()
            if spot_snapshot.quotes
            else None
        )
        if len(spot_snapshot.quotes) < 3:
            flags.append("SPOT_CONSENSUS_THIN")
        if spot_snapshot.dispersion_bps > _SPOT_DISPERSION_WARNING_BPS:
            flags.append("SPOT_FEED_DISPERSION_HIGH")
        return (
            spot_snapshot.price,
            spot_snapshot.source,
            observed_at,
            False,
            quotes,
            spot_snapshot.dispersion_bps,
            len(spot_snapshot.quotes),
        )

    flags.append("LIVE_SPOT_UNAVAILABLE")

    if ohlcv_15m is not None and ohlcv_15m.candles:
        candle = ohlcv_15m.candles[-1]
        flags.append("SPOT_FROM_15M_CANDLE_CLOSE")
        return (
            candle["c"],
            f"15m candle close ({ohlcv_15m.venue})",
            _candle_close_iso(candle, 900),
            False,
            {},
            None,
            0,
        )

    if ohlcv_1h is not None and ohlcv_1h.candles:
        candle = ohlcv_1h.candles[-1]
        flags.append("SPOT_FROM_1H_CANDLE_CLOSE")
        return (
            candle["c"],
            f"1h candle close ({ohlcv_1h.venue})",
            _candle_close_iso(candle, 3600),
            False,
            {},
            None,
            0,
        )

    if displayed_spot_price is not None and displayed_spot_price > 0:
        flags.append("ANCHORED_TO_SCREENSHOT_SPOT")
        logger.warning(
            "Live exchange APIs unavailable; anchoring to screenshot spot",
            symbol=symbol,
            spot=displayed_spot_price,
        )
        return (
            displayed_spot_price,
            "Screenshot Point-in-Time Spot",
            None,
            True,
            {},
            None,
            0,
        )

    return 0.0, "Unavailable", None, False, {}, None, 0


def _derive_24h_stats(
    candles_1h: list[dict[str, float]],
    spot: float,
) -> tuple[float, float, float]:
    """Return ``(price_change_pct, high, low)`` from the last 24 hourly candles."""
    if not candles_1h:
        return 0.0, 0.0, 0.0

    window = candles_1h[-24:]
    high = max(candle["h"] for candle in window)
    low = min(candle["l"] for candle in window)
    if spot > 0:
        high = max(high, spot)
        low = min(low, spot)

    reference = candles_1h[-25]["c"] if len(candles_1h) >= 25 else candles_1h[0]["c"]
    change_pct = (spot / reference - 1.0) * 100.0 if reference > 0 and spot > 0 else 0.0
    return change_pct, high, low


def _build_data_sources(
    spot_source: str,
    ohlcv_15m: OhlcvSnapshot | None,
    ohlcv_1h: OhlcvSnapshot | None,
    book: BookSnapshot | None,
    derivatives: DerivativesSnapshot | None,
    fear_greed: FearGreedSnapshot | None,
    ohlcv_1m: OhlcvSnapshot | None = None,
    futures_premium: FuturesPremiumSnapshot | None = None,
) -> list[str]:
    sources = [spot_source, "Concurrent multi-venue mesh"]
    if ohlcv_1m is not None:
        sources.append(f"1m OHLCV: {ohlcv_1m.venue}")
    if ohlcv_15m is not None:
        sources.append(f"15m OHLCV: {ohlcv_15m.venue}")
    if ohlcv_1h is not None:
        sources.append(f"1h OHLCV: {ohlcv_1h.venue}")
    if book is not None:
        sources.append(f"Order book: {book.venue}")
    if derivatives is not None:
        if derivatives.funding_source:
            sources.append(f"Funding: {derivatives.funding_source}")
        if derivatives.open_interest_source:
            sources.append(f"Open interest: {derivatives.open_interest_source}")
    if futures_premium is not None:
        sources.append(f"Futures premium: {futures_premium.venue}")
    if fear_greed is not None:
        sources.append(fear_greed.source)
    return _merge_flags(sources)


def _apply_sentiment(
    context: dict[str, Any],
    fear_greed: FearGreedSnapshot | None,
) -> None:
    if fear_greed is None:
        return
    context["fear_greed_index"] = fear_greed.value
    context["fear_greed_classification"] = fear_greed.classification
    if fear_greed.value < 20:
        context["sentiment_signal"] = "Extreme Fear — potential capitulation/bounce"
    elif fear_greed.value > 80:
        context["sentiment_signal"] = "Extreme Greed — elevated crash risk"
    else:
        context["sentiment_signal"] = f"{fear_greed.classification} ({fear_greed.value}/100)"


def _apply_derivatives(
    context: dict[str, Any],
    derivatives: DerivativesSnapshot | None,
    spot: float,
    flags: list[str],
) -> None:
    if derivatives is None:
        flags.append("DERIVATIVES_UNAVAILABLE")
        return

    if derivatives.funding_rate_pct is None:
        flags.append("FUNDING_RATE_UNAVAILABLE")
    else:
        context["funding_source"] = derivatives.funding_source

    if derivatives.open_interest_base is None:
        flags.append("OPEN_INTEREST_UNAVAILABLE")
    else:
        context["open_interest_base"] = derivatives.open_interest_base
        context["open_interest_source"] = derivatives.open_interest_source
        context["open_interest_usd"] = f"${derivatives.open_interest_base * spot:,.0f}"

    if derivatives.open_interest_change_pct is None:
        flags.append("OPEN_INTEREST_CHANGE_UNAVAILABLE")
    else:
        context["open_interest_change_pct"] = f"{derivatives.open_interest_change_pct:+.3f}%"
        context["open_interest_change_source"] = derivatives.open_interest_change_source


# ---------------------------------------------------------------------------
# Main public research interface
# ---------------------------------------------------------------------------


async def gather_crypto_intelligence(
    title: str,
    *,
    platform: str = "polymarket",
    closes_at: datetime | None = None,
    underlying_symbol: str | None = None,
    target_price: float | None = None,
    displayed_spot_price: float | None = None,
    direction: str | None = None,
    http_client: httpx.AsyncClient | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Gather comprehensive crypto intelligence from the multi-venue mesh.

    All data groups run concurrently.  The shared HTTP client should come from
    :func:`prediction_ai.integrations.network.build_resilient_client` so that
    market-data DNS failures fall back to DNS-over-HTTPS instead of producing a
    misleading ``ConnectTimeout`` for every venue.
    """
    del platform  # retained for interface compatibility; venues are platform-neutral
    now = datetime.now(UTC)
    researched_at = now.isoformat()
    resolved_settings = settings or get_settings()
    budget = resolved_settings.MARKET_DATA_GROUP_BUDGET_SECONDS

    symbol = underlying_symbol or _infer_symbol(title)
    strike, direction = _parse_strike_and_direction(title, direction)
    if target_price is not None:
        strike = target_price

    seconds_to_expiry = 900.0
    if closes_at:
        delta = (closes_at - now).total_seconds()
        if delta > 0:
            seconds_to_expiry = delta

    own_client = http_client is None
    client = http_client or build_resilient_client(resolved_settings)

    try:
        (
            spot_snapshot,
            ohlcv_15m,
            ohlcv_1h,
            ohlcv_1m_snap,
            book_snapshot,
            derivatives,
            futures_premium_snap,
            fear_greed,
        ) = cast(
            tuple[
                SpotSnapshot | None,
                OhlcvSnapshot | None,
                OhlcvSnapshot | None,
                OhlcvSnapshot | None,
                BookSnapshot | None,
                DerivativesSnapshot | None,
                FuturesPremiumSnapshot | None,
                FearGreedSnapshot | None,
            ],
            await asyncio.gather(
                fetch_spot_snapshot(client, symbol, budget_seconds=budget),
                fetch_ohlcv_snapshot(client, symbol, "15m", 96, budget_seconds=budget),
                fetch_ohlcv_snapshot(client, symbol, "1h", 48, budget_seconds=budget),
                fetch_ohlcv_1m_snapshot(client, symbol),
                fetch_order_book_snapshot(client, symbol, 20, budget_seconds=budget),
                fetch_derivatives_snapshot(client, symbol, budget_seconds=budget),
                fetch_futures_premium(client, symbol),
                fetch_fear_and_greed(client),
            ),
        )

        candles_15m = list(ohlcv_15m.candles) if ohlcv_15m is not None else []
        candles_1h = list(ohlcv_1h.candles) if ohlcv_1h is not None else []
        candles_1m = list(ohlcv_1m_snap.candles) if ohlcv_1m_snap is not None else []
        flags: list[str] = []

        spot, spot_source, spot_observed_at, screenshot_anchored, spot_quotes, dispersion, venue_count = (
            _resolve_spot(
                symbol=symbol,
                spot_snapshot=spot_snapshot,
                ohlcv_15m=ohlcv_15m,
                ohlcv_1h=ohlcv_1h,
                displayed_spot_price=displayed_spot_price,
                flags=flags,
            )
        )

        if spot <= 0:
            logger.warning(
                "No spot price could be resolved across exchanges, candles, or screenshot",
                symbol=symbol,
            )
            return {
                "status": "DATA_UNAVAILABLE",
                "symbol": symbol,
                "strike": strike,
                "direction": direction,
                "spot_source": spot_source,
                "researched_at": researched_at,
                "adjusted_prob_yes": None,
                "final_adjusted_prob_yes": None,
                "data_quality_flags": _merge_flags(flags, ["SPOT_PRICE_UNAVAILABLE"]),
                "note": (
                    "All exchange feeds, fallback candles, and screenshot OCR spot were "
                    "unavailable. No probability prior is emitted; the analysis stops."
                ),
            }

        bids, asks, usable_book = _usable_book(book_snapshot, spot, flags)
        price_change_24h_pct, high_24h, low_24h = _derive_24h_stats(candles_1h, spot)

        funding_rate = (
            derivatives.funding_rate_pct
            if derivatives is not None and derivatives.funding_rate_pct is not None
            else 0.0
        )
        oi_change_pct = (
            derivatives.open_interest_change_pct
            if derivatives is not None and derivatives.open_interest_change_pct is not None
            else 0.0
        )

        if strike is None or strike <= 0:
            closes_1h_prices = [candle["c"] for candle in candles_1h]
            base_vol = _ASSET_BASE_VOL.get(symbol, 0.70)
            vol_24h = (
                realized_vol_annualized(closes_1h_prices, 60)
                if closes_1h_prices
                else base_vol
            )
            return {
                "status": "STRIKE_UNKNOWN",
                "symbol": symbol,
                "strike": None,
                "direction": direction,
                "spot_price_usd": f"{spot:,.2f}",
                "spot_source": spot_source,
                "spot_observed_at": spot_observed_at,
                "vol_24h_annualized": f"{vol_24h:.2%}",
                "funding_rate_pct_per_8h": f"{funding_rate:.4f}%",
                "funding_signal": interpret_funding_rate(funding_rate),
                "researched_at": researched_at,
                "adjusted_prob_yes": None,
                "final_adjusted_prob_yes": None,
                "data_quality_flags": _merge_flags(
                    flags, ["STRIKE_PRICE_NOT_DETECTED"]
                ),
                "note": (
                    "Could not detect strike price from contract proposition. "
                    "Please confirm target price."
                ),
            }

        calibration = await _load_calibration(symbol)
        vol_multiplier = float(calibration.get("vol_multiplier", 1.0))
        mom_weight = float(calibration.get("momentum_weight", 0.35))
        flow_weight = float(calibration.get("order_flow_weight", 0.30))
        futures_premium_bps = (
            futures_premium_snap.premium_bps
            if futures_premium_snap is not None
            else 0.0
        )
        features = build_features(
            symbol=symbol,
            spot_price=spot,
            strike_price=strike,
            seconds_to_expiry=seconds_to_expiry,
            ohlcv_15m=candles_15m,
            ohlcv_1h=candles_1h,
            bids=bids,
            asks=asks,
            funding_rate_pct=funding_rate,
            oi_change_pct=oi_change_pct,
            price_change_24h_pct=price_change_24h_pct,
            high_24h=high_24h,
            low_24h=low_24h,
            vol_multiplier=vol_multiplier,
            momentum_weight=mom_weight,
            order_flow_weight=flow_weight,
            ohlcv_1m=candles_1m,
            futures_premium_bps=futures_premium_bps,
        )

        flags = _merge_flags(flags, features.data_quality_flags)
        confidence = features.signal_confidence
        if screenshot_anchored:
            confidence = "low"
        elif "SPOT_FROM_15M_CANDLE_CLOSE" in flags or "SPOT_FROM_1H_CANDLE_CLOSE" in flags:
            confidence = _downgrade_confidence(confidence)

        final_prob = features.adjusted_prob_yes
        if direction == "below":
            final_prob = 1.0 - final_prob
            features.key_signals.insert(
                0,
                f"Market asks for price BELOW {strike:,.2f} — probability inverted",
            )

        context = features.to_context_dict()
        context.update(
            {
                "strike": strike,
                "direction": direction,
                "spot_source": spot_source,
                "spot_observed_at": spot_observed_at,
                "spot_quotes": spot_quotes,
                "spot_venue_count": venue_count,
                "spot_dispersion_bps": round(dispersion, 2) if dispersion is not None else None,
                "ohlcv_1m_source": _ohlcv_label(ohlcv_1m_snap),
                "ohlcv_15m_source": _ohlcv_label(ohlcv_15m),
                "ohlcv_1h_source": _ohlcv_label(ohlcv_1h),
                "futures_premium_bps": round(futures_premium_bps, 2) if futures_premium_snap is not None else None,
                "futures_mark_price": f"{futures_premium_snap.futures_price:,.2f}" if futures_premium_snap is not None else None,
                "order_book_source": usable_book.venue if usable_book is not None else None,
                "order_book_spread_bps": (
                    round(
                        (usable_book.asks[0][0] - usable_book.bids[0][0])
                        / ((usable_book.asks[0][0] + usable_book.bids[0][0]) / 2.0)
                        * 10_000.0,
                        2,
                    )
                    if usable_book is not None
                    else None
                ),
                "price_change_24h_pct": f"{price_change_24h_pct:+.3f}%",
                "high_24h_usd": f"{high_24h:,.2f}" if high_24h > 0 else None,
                "low_24h_usd": f"{low_24h:,.2f}" if low_24h > 0 else None,
                "final_adjusted_prob_yes": f"{final_prob:.4f}",
                "signal_confidence": confidence,
                "researched_at": researched_at,
                "data_sources": _build_data_sources(
                    spot_source,
                    ohlcv_15m,
                    ohlcv_1h,
                    usable_book,
                    derivatives,
                    fear_greed,
                    ohlcv_1m=ohlcv_1m_snap,
                    futures_premium=futures_premium_snap,
                ),
                "data_quality_flags": flags,
                "vol_methodology": "EWMA / Calibrated Digital Option",
                "pricing_model": "Log-normal digital option (Black-Scholes, r=0)",
                "learned_calibration": calibration,
            }
        )

        if screenshot_anchored:
            context["screenshot_anchored"] = True

        _apply_derivatives(context, derivatives, spot, flags)
        if fear_greed is None:
            flags.append("FEAR_GREED_UNAVAILABLE")
        _apply_sentiment(context, fear_greed)

        # Keep the final flag list authoritative after helpers append to it.
        context["data_quality_flags"] = _merge_flags(flags)
        context["signal_confidence"] = confidence

        logger.info(
            "Crypto intelligence computed from multi-venue mesh",
            symbol=symbol,
            spot=spot,
            spot_source=spot_source,
            spot_venues=venue_count,
            spot_dispersion_bps=round(dispersion, 2) if dispersion is not None else None,
            strike=strike,
            direction=direction,
            seconds_to_expiry=int(seconds_to_expiry),
            base_prob=f"{features.base_prob_yes:.4f}",
            final_prob=f"{final_prob:.4f}",
            confidence=confidence,
            data_quality_flags=flags,
        )
        return context
    finally:
        if own_client:
            await client.aclose()
