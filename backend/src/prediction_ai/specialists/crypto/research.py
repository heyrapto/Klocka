"""Crypto market intelligence specialist.

Fetches real OHLCV, order-book, funding-rate, and open-interest data from
public exchange APIs. Computes a mathematically grounded P(YES) using the
log-normal pricing model and microstructure signals. Never abstrains due to
data scarcity — the mathematical model always produces a base probability
when spot price, volatility, and time-to-expiry are available.
"""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog

from prediction_ai.specialists.crypto.features import (
    CryptoMarketFeatures,
    analyze_order_book,
    build_features,
    interpret_funding_rate,
    prob_above_strike,
    realized_vol_annualized,
)

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Public API endpoints (no auth required)
# ---------------------------------------------------------------------------

_BINANCE_SPOT = "https://api.binance.com"
_BINANCE_FUTURES = "https://fapi.binance.com"
_COINBASE = "https://api.exchange.coinbase.com"
_FEAR_GREED = "https://api.alternative.me/fng"

# Default Binance timeout — relaxed because these are free public endpoints
_TIMEOUT = httpx.Timeout(8.0, connect=4.0)

# Map common names to Binance symbols
_SYMBOL_MAP: dict[str, str] = {
    "btc": "BTC", "bitcoin": "BTC",
    "eth": "ETH", "ethereum": "ETH",
    "sol": "SOL", "solana": "SOL",
    "xrp": "XRP", "ripple": "XRP",
    "bnb": "BNB",
    "doge": "DOGE", "dogecoin": "DOGE",
    "ada": "ADA", "cardano": "ADA",
    "avax": "AVAX", "avalanche": "AVAX",
    "matic": "MATIC", "polygon": "MATIC",
    "link": "LINK", "chainlink": "LINK",
    "dot": "DOT", "polkadot": "DOT",
    "ltc": "LTC", "litecoin": "LTC",
    "atom": "ATOM",
    "near": "NEAR",
    "arb": "ARB", "arbitrum": "ARB",
    "op": "OP", "optimism": "OP",
    "inj": "INJ", "injective": "INJ",
    "sui": "SUI",
    "apt": "APT", "aptos": "APT",
    "pepe": "PEPE",
    "wif": "WIF",
    "bonk": "BONK",
}


def _infer_symbol(title: str, hint: str | None = None) -> str:
    """Extract the most likely trading symbol from a market title."""
    text = (title + " " + (hint or "")).lower()
    for keyword, sym in _SYMBOL_MAP.items():
        if keyword in text:
            return sym
    # Try direct uppercase symbol pattern like $BTC, BTC/USD, BTC-USD
    match = re.search(r"\b([A-Z]{2,6})\b(?:/USD|USDT|USD)?", title)
    if match and match.group(1) in {v for v in _SYMBOL_MAP.values()}:
        return match.group(1)
    return "BTC"  # default to BTC for unrecognised crypto markets


def _parse_strike_and_direction(title: str) -> tuple[float | None, str]:
    """
    Extract the strike price and comparison direction from a market title.

    Returns:
        (strike_price, direction) where direction is "above", "below", or "unknown"

    Examples:
        "Will BTC be above $95,000 by Friday?" → (95000.0, "above")
        "BTC below $90k before EOY?" → (90000.0, "below")
    """
    title_norm = title.replace(",", "").replace("$", "").lower()

    direction = "above"
    if any(w in title_norm for w in ("below", "under", "less than", "not above", "drops below")):
        direction = "below"
    elif any(w in title_norm for w in ("above", "over", "exceed", "higher than", "reach")):
        direction = "above"

    # Match numbers like 95000, 95k, 95.5k, 95,000, 0.00085
    patterns = [
        r"(\d+(?:\.\d+)?)\s*k\b",          # 95k, 95.5k
        r"(\d{4,}(?:\.\d+)?)",              # 95000, 95000.50
        r"(\d+(?:\.\d{2,})?)",              # 0.00085 (micro-caps)
    ]
    for pat in patterns:
        m = re.search(pat, title_norm)
        if m:
            value = float(m.group(1))
            if "k" in title_norm[m.start():m.end() + 2]:
                value *= 1000
            if value > 0.001:  # ignore near-zero matches
                return value, direction

    return None, direction


async def _safe_get(
    client: httpx.AsyncClient,
    url: str,
    params: dict | None = None,
    label: str = "",
) -> dict | list | None:
    """GET with error swallowing — research must degrade gracefully."""
    try:
        resp = await client.get(url, params=params, timeout=_TIMEOUT)
        if resp.status_code == 200:
            return resp.json()
        logger.warning("Crypto data fetch returned non-200", url=url, status=resp.status_code, label=label)
    except (httpx.TimeoutException, httpx.HTTPError, Exception) as e:
        logger.warning("Crypto data fetch failed", url=url, label=label, error=str(e))
    return None


async def _fetch_ohlcv(
    client: httpx.AsyncClient,
    symbol: str,
    interval: str,
    limit: int = 96,
) -> list[dict[str, float]]:
    """Fetch OHLCV candles from Binance. Returns list of {o,h,l,c,v} dicts."""
    data = await _safe_get(
        client,
        f"{_BINANCE_SPOT}/api/v3/klines",
        params={"symbol": f"{symbol}USDT", "interval": interval, "limit": limit},
        label=f"ohlcv_{interval}_{symbol}",
    )
    if not isinstance(data, list):
        return []
    result = []
    for row in data:
        try:
            result.append({
                "o": float(row[1]),
                "h": float(row[2]),
                "l": float(row[3]),
                "c": float(row[4]),
                "v": float(row[5]),
            })
        except (IndexError, ValueError, TypeError):
            continue
    return result


async def _fetch_ticker_24h(
    client: httpx.AsyncClient, symbol: str
) -> dict[str, float]:
    """Fetch 24h stats from Binance."""
    data = await _safe_get(
        client,
        f"{_BINANCE_SPOT}/api/v3/ticker/24hr",
        params={"symbol": f"{symbol}USDT"},
        label=f"ticker24h_{symbol}",
    )
    if not isinstance(data, dict):
        return {}
    try:
        return {
            "price": float(data.get("lastPrice", 0)),
            "high": float(data.get("highPrice", 0)),
            "low": float(data.get("lowPrice", 0)),
            "volume": float(data.get("volume", 0)),
            "price_change_pct": float(data.get("priceChangePercent", 0)),
        }
    except (ValueError, TypeError):
        return {}


async def _fetch_coinbase_spot(client: httpx.AsyncClient, symbol: str) -> float:
    """Fallback spot price fetch from Coinbase (useful if Binance is geo-restricted)."""
    data = await _safe_get(
        client,
        f"https://api.coinbase.com/v2/prices/{symbol}-USD/spot",
        label=f"coinbase_spot_{symbol}",
    )
    if isinstance(data, dict):
        try:
            return float(data.get("data", {}).get("amount", 0))
        except (ValueError, TypeError):
            pass
    return 0.0


async def _fetch_order_book(
    client: httpx.AsyncClient, symbol: str, limit: int = 20
) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    """Fetch order book from Binance. Returns (bids, asks)."""
    data = await _safe_get(
        client,
        f"{_BINANCE_SPOT}/api/v3/depth",
        params={"symbol": f"{symbol}USDT", "limit": limit},
        label=f"orderbook_{symbol}",
    )
    if not isinstance(data, dict):
        return [], []
    try:
        bids = [(float(p), float(q)) for p, q in data.get("bids", [])]
        asks = [(float(p), float(q)) for p, q in data.get("asks", [])]
        return bids, asks
    except (ValueError, TypeError):
        return [], []


async def _fetch_funding_rate(
    client: httpx.AsyncClient, symbol: str
) -> float:
    """Fetch latest perpetual funding rate from Binance Futures. Returns % per 8h."""
    data = await _safe_get(
        client,
        f"{_BINANCE_FUTURES}/fapi/v1/fundingRate",
        params={"symbol": f"{symbol}USDT", "limit": 1},
        label=f"funding_{symbol}",
    )
    if not isinstance(data, list) or not data:
        return 0.0
    try:
        # fundingRate is a decimal like 0.0001 → 0.01%
        return float(data[0].get("fundingRate", 0)) * 100
    except (KeyError, ValueError, TypeError):
        return 0.0


async def _fetch_open_interest(
    client: httpx.AsyncClient, symbol: str
) -> float | None:
    """Fetch open interest from Binance Futures."""
    data = await _safe_get(
        client,
        f"{_BINANCE_FUTURES}/fapi/v1/openInterest",
        params={"symbol": f"{symbol}USDT"},
        label=f"oi_{symbol}",
    )
    if not isinstance(data, dict):
        return None
    try:
        return float(data.get("openInterest", 0))
    except (ValueError, TypeError):
        return None


async def _fetch_fear_and_greed() -> dict[str, Any]:
    """Fetch the Fear & Greed index from alternative.me."""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        data = await _safe_get(client, _FEAR_GREED, label="fear_greed")
    if not isinstance(data, dict):
        return {}
    try:
        entry = data.get("data", [{}])[0]
        return {
            "value": int(entry.get("value", 50)),
            "classification": entry.get("value_classification", "Neutral"),
        }
    except (IndexError, KeyError, ValueError, TypeError):
        return {}


# ---------------------------------------------------------------------------
# Main public interface
# ---------------------------------------------------------------------------

async def gather_crypto_intelligence(
    title: str,
    *,
    platform: str = "polymarket",
    closes_at: datetime | None = None,
    underlying_symbol: str | None = None,
    target_price: float | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """
    Gather full crypto market intelligence and compute P(YES).

    Args:
        title: market title/question text
        platform: "polymarket" or "bayse"
        closes_at: market expiry datetime (UTC)
        underlying_symbol: override symbol detection (e.g. "BTC")
        target_price: override strike price detection
        http_client: optional shared httpx client

    Returns:
        Dictionary with all computed features and adjusted_prob_yes,
        ready for injection into the Gemini forecasting prompt.
    """
    now = datetime.now(UTC)
    researched_at = now.isoformat()

    # --- Symbol and strike detection ---
    symbol = underlying_symbol or _infer_symbol(title)
    strike, direction = _parse_strike_and_direction(title)
    if target_price is not None:
        strike = target_price

    # --- Time to expiry ---
    seconds_to_expiry = 900.0  # default 15 minutes if unknown
    if closes_at:
        delta = (closes_at - now).total_seconds()
        if delta > 0:
            seconds_to_expiry = delta
        elif delta < -60:
            # Already expired
            return {
                "status": "MARKET_EXPIRED",
                "symbol": symbol,
                "researched_at": researched_at,
                "adjusted_prob_yes": 0.5,
                "data_quality_flags": ["MARKET_ALREADY_EXPIRED"],
            }

    # --- Fetch all data concurrently ---
    own_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=_TIMEOUT)

    try:
        (
            ohlcv_15m,
            ohlcv_1h,
            ticker,
            (bids, asks),
            funding_rate,
            oi_value,
        ) = await asyncio.gather(
            _fetch_ohlcv(client, symbol, "15m", limit=96),
            _fetch_ohlcv(client, symbol, "1h", limit=48),
            _fetch_ticker_24h(client, symbol),
            _fetch_order_book(client, symbol, limit=20),
            _fetch_funding_rate(client, symbol),
            _fetch_open_interest(client, symbol),
            return_exceptions=False,
        )
    except Exception as e:
        logger.error("Unexpected error during parallel crypto data fetch", error=str(e))
        ohlcv_15m, ohlcv_1h, ticker, bids, asks, funding_rate, oi_value = [], [], {}, [], [], 0.0, None
    finally:
        if own_client:
            await client.aclose()

    # --- Determine spot price ---
    spot = ticker.get("price", 0.0)
    if spot <= 0 and ohlcv_15m:
        spot = ohlcv_15m[-1]["c"]  # fallback to last candle close
    if spot <= 0 and ohlcv_1h:
        spot = ohlcv_1h[-1]["c"]
    if spot <= 0:
        spot = await _fetch_coinbase_spot(client, symbol)

    # --- If we still have no spot price, we're genuinely data-limited ---
    if spot <= 0:
        return {
            "status": "DATA_UNAVAILABLE",
            "symbol": symbol,
            "researched_at": researched_at,
            "adjusted_prob_yes": 0.5,
            "data_quality_flags": ["SPOT_PRICE_UNAVAILABLE"],
            "note": "No spot price available; cannot compute probability. Using 0.5 as prior.",
        }

    # --- If strike is unknown, we can't compute a price-bracket probability ---
    if strike is None or strike <= 0:
        # Still return useful context, but probability is uninformative
        closes_1h_prices = [c["c"] for c in ohlcv_1h]
        vol_24h = realized_vol_annualized(closes_1h_prices, 60) if closes_1h_prices else 0.80
        return {
            "status": "STRIKE_UNKNOWN",
            "symbol": symbol,
            "spot_price_usd": f"{spot:,.2f}",
            "vol_24h_annualized": f"{vol_24h:.2%}",
            "funding_rate_pct_per_8h": f"{funding_rate:.4f}%",
            "funding_signal": interpret_funding_rate(funding_rate),
            "researched_at": researched_at,
            "adjusted_prob_yes": 0.5,
            "data_quality_flags": ["STRIKE_PRICE_NOT_DETECTED"],
            "note": "Could not detect strike price from title. Provide target price explicitly.",
        }

    # --- OI change (approximate from price change as proxy) ---
    price_change_24h_pct = ticker.get("price_change_pct", 0.0)
    oi_change_pct = 0.0  # would need historical OI to compute change; use 0 as neutral

    # --- Retrieve calibrated volatility scaling from SQLite Knowledge Matrix ---
    vol_multiplier = 1.0
    try:
        from prediction_ai.db.sqlite import get_sqlite_db
        sqlite = get_sqlite_db()
        cal = await sqlite.get_asset_calibration(symbol)
        vol_multiplier = float(cal.get("vol_multiplier", 1.0))
    except Exception:
        pass

    # --- Build all features ---
    features = build_features(
        symbol=symbol,
        spot_price=spot,
        strike_price=strike,
        seconds_to_expiry=seconds_to_expiry,
        ohlcv_15m=ohlcv_15m,
        ohlcv_1h=ohlcv_1h,
        bids=bids,
        asks=asks,
        funding_rate_pct=funding_rate,
        oi_change_pct=oi_change_pct,
        price_change_24h_pct=price_change_24h_pct,
        high_24h=ticker.get("high", 0.0),
        low_24h=ticker.get("low", 0.0),
        vol_multiplier=vol_multiplier,
    )

    # --- If market is "below" (not "above"), flip probability ---
    final_prob = features.adjusted_prob_yes
    if direction == "below":
        final_prob = 1.0 - final_prob
        features.key_signals.insert(0, f"Market asks for price BELOW {strike:,.2f} — probability flipped")

    # --- Fear & Greed (best effort, non-blocking) ---
    fear_greed: dict[str, Any] = {}
    try:
        fear_greed = await _fetch_fear_and_greed()
    except Exception:
        pass

    # Build the full research context
    context = features.to_context_dict()
    context.update({
        "direction": direction,
        "final_adjusted_prob_yes": f"{final_prob:.4f}",
        "researched_at": researched_at,
        "data_sources": ["Binance Spot API", "Binance Futures API"],
        "vol_methodology": "EWMA (exponentially weighted, lambda=0.94)",
        "pricing_model": "Log-normal digital option (Black-Scholes, r=0)",
    })

    if fear_greed:
        fg_val = fear_greed.get("value", 50)
        fg_class = fear_greed.get("classification", "Neutral")
        context["fear_greed_index"] = fg_val
        context["fear_greed_classification"] = fg_class
        if fg_val < 20:
            context["sentiment_signal"] = "Extreme Fear — potential capitulation/bounce"
        elif fg_val > 80:
            context["sentiment_signal"] = "Extreme Greed — elevated crash risk"
        else:
            context["sentiment_signal"] = f"{fg_class} ({fg_val}/100)"

    if ticker:
        context["volume_24h_usd"] = f"${ticker.get('volume', 0) * spot:,.0f}"

    if oi_value is not None:
        context["open_interest_usd"] = f"${oi_value * spot:,.0f}"

    logger.info(
        "Crypto intelligence computed",
        symbol=symbol,
        spot=spot,
        strike=strike,
        direction=direction,
        seconds_to_expiry=int(seconds_to_expiry),
        base_prob=f"{features.base_prob_yes:.4f}",
        adjusted_prob=f"{final_prob:.4f}",
        confidence=features.signal_confidence,
    )

    return context
