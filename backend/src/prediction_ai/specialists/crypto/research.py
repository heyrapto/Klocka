"""Crypto specialist research service with resilient multi-venue fallbacks.

Gathers real-time market data across a cascade of premier crypto exchanges
(Binance Global, Binance US, OKX, Bybit, Coinbase, Kraken, CoinGecko) and gracefully
anchors to screenshot point-in-time spot prices if live venue APIs are unreachable.
Computes mathematically grounded probabilities via log-normal digital option math.
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
# Public exchange API endpoints (no auth required)
# ---------------------------------------------------------------------------

_BINANCE_SPOT = "https://api.binance.com"
_BINANCE_FUTURES = "https://fapi.binance.com"
_BINANCE_US = "https://api.binance.us"
_OKX = "https://www.okx.com"
_BYBIT = "https://api.bybit.com"
_COINBASE_API = "https://api.coinbase.com"
_COINBASE_EXCHANGE = "https://api.exchange.coinbase.com"
_KRAKEN = "https://api.kraken.com"
_COINGECKO = "https://api.coingecko.com"
_FEAR_GREED = "https://api.alternative.me/fng"

_TIMEOUT = httpx.Timeout(6.0, connect=3.0)

# Map common names to standardized ticker symbols
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

_COINGECKO_MAP: dict[str, str] = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "XRP": "ripple",
    "BNB": "binancecoin",
    "DOGE": "dogecoin",
    "ADA": "cardano",
    "AVAX": "avalanche-2",
    "MATIC": "polygon-ecosystem-token",
    "LINK": "chainlink",
    "NEAR": "near",
    "SUI": "sui",
}

_KRAKEN_PAIR_MAP: dict[str, str] = {
    "BTC": "XBTUSD",
    "ETH": "ETHUSD",
    "SOL": "SOLUSD",
    "XRP": "XRPUSD",
    "DOGE": "XDGUSD",
    "ADA": "ADAUSD",
    "AVAX": "AVAXUSD",
    "LINK": "LINKUSD",
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


def _infer_symbol(title: str, hint: str | None = None) -> str:
    """Extract the most likely trading symbol from a market title."""
    text = (title + " " + (hint or "")).lower()
    for keyword, sym in _SYMBOL_MAP.items():
        if keyword in text:
            return sym
    match = re.search(r"\b([A-Z]{2,6})\b(?:/USD|USDT|USD)?", title)
    if match and match.group(1) in {v for v in _SYMBOL_MAP.values()}:
        return match.group(1)
    return "BTC"


def _parse_strike_and_direction(title: str) -> tuple[float | None, str]:
    """
    Extract the strike price and comparison direction from a market title.

    Returns:
        (strike_price, direction) where direction is "above", "below", or "unknown"
    """
    title_norm = title.replace(",", "").replace("$", "").lower()

    direction = "above"
    if any(w in title_norm for w in ["below", "less than", "under", "sub-", "drop to", "down"]):
        direction = "below"
    elif any(w in title_norm for w in ["above", "greater than", "over", "reach", "hit", "surpass", "up"]):
        direction = "above"

    patterns = [
        r"(?:above|below|reach|hit|over|under|at|to)\s+(\d+(?:\.\d+)?)\s*k\b",
        r"\b(\d+(?:\.\d+)?)\s*k\b",
        r"(?:above|below|reach|hit|over|under|at|to)\s+(\d{2,7}(?:\.\d+)?)",
        r"\b(\d{4,7}(?:\.\d+)?)\b",
    ]

    for pat in patterns:
        m = re.search(pat, title_norm)
        if m:
            val_str = m.group(1)
            try:
                val = float(val_str)
                if "k" in pat:
                    val *= 1000.0
                if val > 0:
                    return val, direction
            except ValueError:
                continue

    return None, direction


def _format_error(e: Exception) -> str:
    err_str = str(e).strip()
    return f"{type(e).__name__}: {err_str}" if err_str else type(e).__name__


async def _safe_get(
    client: httpx.AsyncClient,
    url: str,
    params: dict | None = None,
    label: str = "",
) -> dict | list | None:
    """GET with error swallowing and descriptive logging."""
    try:
        resp = await client.get(url, params=params, timeout=_TIMEOUT)
        if resp.status_code == 200:
            return resp.json()
        logger.warning(
            "Crypto data fetch returned non-200",
            url=url,
            status=resp.status_code,
            label=label,
        )
    except Exception as e:
        logger.warning(
            "Crypto data fetch failed",
            url=url,
            label=label,
            error=_format_error(e),
        )
    return None


# ---------------------------------------------------------------------------
# Multi-Venue Resilient Fetchers
# ---------------------------------------------------------------------------

async def _fetch_spot_with_fallbacks(
    client: httpx.AsyncClient,
    symbol: str,
    displayed_spot_price: float | None = None,
) -> tuple[float, str]:
    """
    Fetch spot price across exchange cascade with point-in-time screenshot fallback.
    Returns (spot_price, source_name).
    """
    pair_usdt = f"{symbol}USDT"

    # 1. Binance Global Spot
    data = await _safe_get(client, f"{_BINANCE_SPOT}/api/v3/ticker/price", params={"symbol": pair_usdt}, label=f"spot_binance_{symbol}")
    if isinstance(data, dict) and "price" in data:
        try:
            return float(data["price"]), "Binance Global"
        except (ValueError, TypeError):
            pass

    # 2. Binance US Spot
    data = await _safe_get(client, f"{_BINANCE_US}/api/v3/ticker/price", params={"symbol": pair_usdt}, label=f"spot_binanceus_{symbol}")
    if isinstance(data, dict) and "price" in data:
        try:
            return float(data["price"]), "Binance US"
        except (ValueError, TypeError):
            pass

    # 3. OKX Spot
    data = await _safe_get(client, f"{_OKX}/api/v5/market/ticker", params={"instId": f"{symbol}-USDT"}, label=f"spot_okx_{symbol}")
    if isinstance(data, dict) and data.get("data"):
        try:
            return float(data["data"][0]["last"]), "OKX"
        except (IndexError, KeyError, ValueError, TypeError):
            pass

    # 4. Bybit Spot
    data = await _safe_get(client, f"{_BYBIT}/v5/market/tickers", params={"category": "spot", "symbol": pair_usdt}, label=f"spot_bybit_{symbol}")
    if isinstance(data, dict):
        try:
            items = data.get("result", {}).get("list", [])
            if items:
                return float(items[0]["lastPrice"]), "Bybit"
        except (IndexError, KeyError, ValueError, TypeError):
            pass

    # 5. Coinbase Spot
    data = await _safe_get(client, f"{_COINBASE_API}/v2/prices/{symbol}-USD/spot", label=f"spot_coinbase_{symbol}")
    if isinstance(data, dict):
        try:
            amt = data.get("data", {}).get("amount")
            if amt:
                return float(amt), "Coinbase"
        except (ValueError, TypeError):
            pass

    # 6. Kraken Spot
    kraken_pair = _KRAKEN_PAIR_MAP.get(symbol, f"{symbol}USD")
    data = await _safe_get(client, f"{_KRAKEN}/0/public/Ticker", params={"pair": kraken_pair}, label=f"spot_kraken_{symbol}")
    if isinstance(data, dict) and data.get("result"):
        try:
            pair_data = next(iter(data["result"].values()))
            return float(pair_data["c"][0]), "Kraken"
        except (KeyError, IndexError, ValueError, TypeError):
            pass

    # 7. CoinGecko Spot
    cg_id = _COINGECKO_MAP.get(symbol)
    if cg_id:
        data = await _safe_get(client, f"{_COINGECKO}/api/v3/simple/price", params={"ids": cg_id, "vs_currencies": "usd"}, label=f"spot_coingecko_{symbol}")
        if isinstance(data, dict) and cg_id in data:
            try:
                return float(data[cg_id]["usd"]), "CoinGecko"
            except (KeyError, ValueError, TypeError):
                pass

    # 8. Point-in-Time Screenshot Grounding
    if displayed_spot_price is not None and displayed_spot_price > 0:
        logger.info(
            "Live exchange APIs unreachable; anchored to screenshot point-in-time spot",
            symbol=symbol,
            spot=displayed_spot_price,
        )
        return displayed_spot_price, "Screenshot Point-in-Time Spot"

    return 0.0, "Unknown"


async def _fetch_ohlcv_resilient(
    client: httpx.AsyncClient,
    symbol: str,
    interval: str,
    limit: int = 96,
) -> list[dict[str, float]]:
    """Fetch OHLCV candles from Binance Global with Binance US, OKX, and Bybit fallbacks."""
    pair_usdt = f"{symbol}USDT"

    # Venue 1: Binance Global
    data = await _safe_get(
        client,
        f"{_BINANCE_SPOT}/api/v3/klines",
        params={"symbol": pair_usdt, "interval": interval, "limit": limit},
        label=f"ohlcv_{interval}_binance_{symbol}",
    )
    if isinstance(data, list) and data:
        candles = []
        for row in data:
            try:
                candles.append({
                    "o": float(row[1]), "h": float(row[2]), "l": float(row[3]), "c": float(row[4]), "v": float(row[5]),
                })
            except (IndexError, ValueError, TypeError):
                continue
        if candles:
            return candles

    # Venue 2: Binance US
    data = await _safe_get(
        client,
        f"{_BINANCE_US}/api/v3/klines",
        params={"symbol": pair_usdt, "interval": interval, "limit": limit},
        label=f"ohlcv_{interval}_binanceus_{symbol}",
    )
    if isinstance(data, list) and data:
        candles = []
        for row in data:
            try:
                candles.append({
                    "o": float(row[1]), "h": float(row[2]), "l": float(row[3]), "c": float(row[4]), "v": float(row[5]),
                })
            except (IndexError, ValueError, TypeError):
                continue
        if candles:
            return candles

    # Venue 3: OKX Candles
    okx_bar = "15m" if interval == "15m" else "1H"
    data = await _safe_get(
        client,
        f"{_OKX}/api/v5/market/candles",
        params={"instId": f"{symbol}-USDT", "bar": okx_bar, "limit": str(limit)},
        label=f"ohlcv_{interval}_okx_{symbol}",
    )
    if isinstance(data, dict) and data.get("data"):
        candles = []
        # OKX returns reverse chronological order: [ts, o, h, l, c, vol, ...]
        for row in reversed(data["data"]):
            try:
                candles.append({
                    "o": float(row[1]), "h": float(row[2]), "l": float(row[3]), "c": float(row[4]), "v": float(row[5]),
                })
            except (IndexError, ValueError, TypeError):
                continue
        if candles:
            return candles

    # Venue 4: Bybit Klines
    bybit_interval = "15" if interval == "15m" else "60"
    data = await _safe_get(
        client,
        f"{_BYBIT}/v5/market/kline",
        params={"category": "spot", "symbol": pair_usdt, "interval": bybit_interval, "limit": limit},
        label=f"ohlcv_{interval}_bybit_{symbol}",
    )
    if isinstance(data, dict):
        raw_list = data.get("result", {}).get("list", [])
        if raw_list:
            candles = []
            for row in reversed(raw_list):
                try:
                    candles.append({
                        "o": float(row[1]), "h": float(row[2]), "l": float(row[3]), "c": float(row[4]), "v": float(row[5]),
                    })
                except (IndexError, ValueError, TypeError):
                    continue
            if candles:
                return candles

    return []


async def _fetch_order_book_resilient(
    client: httpx.AsyncClient,
    symbol: str,
    limit: int = 20,
) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    """Fetch order book from Binance Global with Binance US, OKX, and Bybit fallbacks."""
    pair_usdt = f"{symbol}USDT"

    # Venue 1: Binance Global
    data = await _safe_get(client, f"{_BINANCE_SPOT}/api/v3/depth", params={"symbol": pair_usdt, "limit": limit}, label=f"depth_binance_{symbol}")
    if isinstance(data, dict) and "bids" in data:
        try:
            return [(float(p), float(q)) for p, q in data.get("bids", [])], [(float(p), float(q)) for p, q in data.get("asks", [])]
        except (ValueError, TypeError):
            pass

    # Venue 2: Binance US
    data = await _safe_get(client, f"{_BINANCE_US}/api/v3/depth", params={"symbol": pair_usdt, "limit": limit}, label=f"depth_binanceus_{symbol}")
    if isinstance(data, dict) and "bids" in data:
        try:
            return [(float(p), float(q)) for p, q in data.get("bids", [])], [(float(p), float(q)) for p, q in data.get("asks", [])]
        except (ValueError, TypeError):
            pass

    # Venue 3: OKX Books
    data = await _safe_get(client, f"{_OKX}/api/v5/market/books", params={"instId": f"{symbol}-USDT", "sz": str(limit)}, label=f"depth_okx_{symbol}")
    if isinstance(data, dict) and data.get("data"):
        try:
            book = data["data"][0]
            bids = [(float(row[0]), float(row[1])) for row in book.get("bids", [])]
            asks = [(float(row[0]), float(row[1])) for row in book.get("asks", [])]
            return bids, asks
        except (IndexError, KeyError, ValueError, TypeError):
            pass

    # Venue 4: Bybit Order Book
    data = await _safe_get(client, f"{_BYBIT}/v5/market/orderbook", params={"category": "spot", "symbol": pair_usdt, "limit": limit}, label=f"depth_bybit_{symbol}")
    if isinstance(data, dict):
        try:
            res = data.get("result", {})
            bids = [(float(p), float(q)) for p, q in res.get("b", [])]
            asks = [(float(p), float(q)) for p, q in res.get("a", [])]
            if bids or asks:
                return bids, asks
        except (ValueError, TypeError):
            pass

    return [], []


async def _fetch_derivatives(
    client: httpx.AsyncClient, symbol: str
) -> tuple[float, float | None]:
    """Fetch perpetual funding rate and open interest from Binance Futures."""
    funding_rate = 0.0
    open_interest = None

    fdata = await _safe_get(client, f"{_BINANCE_FUTURES}/fapi/v1/fundingRate", params={"symbol": f"{symbol}USDT", "limit": 1}, label=f"funding_{symbol}")
    if isinstance(fdata, list) and fdata:
        try:
            funding_rate = float(fdata[0].get("fundingRate", 0)) * 100.0
        except (KeyError, ValueError, TypeError):
            pass

    oidata = await _safe_get(client, f"{_BINANCE_FUTURES}/fapi/v1/openInterest", params={"symbol": f"{symbol}USDT"}, label=f"oi_{symbol}")
    if isinstance(oidata, dict):
        try:
            open_interest = float(oidata.get("openInterest", 0))
        except (ValueError, TypeError):
            pass

    return funding_rate, open_interest


async def _fetch_fear_and_greed(client: httpx.AsyncClient) -> dict[str, Any]:
    """Fetch Fear & Greed index from alternative.me."""
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
# Main Public Research Interface
# ---------------------------------------------------------------------------

async def gather_crypto_intelligence(
    title: str,
    *,
    platform: str = "polymarket",
    closes_at: datetime | None = None,
    underlying_symbol: str | None = None,
    target_price: float | None = None,
    displayed_spot_price: float | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """
    Gather comprehensive crypto intelligence with resilient multi-venue fallbacks
    and point-in-time screenshot grounding.

    Args:
        title: Market title/question string.
        platform: "polymarket", "bayse", or "unknown".
        closes_at: Contract expiry datetime (UTC).
        underlying_symbol: Ticker override (e.g. "BTC").
        target_price: Strike price override.
        displayed_spot_price: Screenshot extracted point-in-time spot price.
        http_client: Shared httpx async client.
    """
    now = datetime.now(UTC)
    researched_at = now.isoformat()

    symbol = underlying_symbol or _infer_symbol(title)
    strike, direction = _parse_strike_and_direction(title)
    if target_price is not None:
        strike = target_price

    # Compute seconds to expiry
    seconds_to_expiry = 900.0  # default 15 minutes
    if closes_at:
        delta = (closes_at - now).total_seconds()
        if delta > 0:
            seconds_to_expiry = delta

    own_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=_TIMEOUT)

    try:
        # 1. Fetch spot price with multi-venue cascade + screenshot fallback
        spot, spot_source = await _fetch_spot_with_fallbacks(
            client=client,
            symbol=symbol,
            displayed_spot_price=displayed_spot_price,
        )

        # 2. Concurrently fetch candles, order book, derivatives, and sentiment
        (
            ohlcv_15m,
            ohlcv_1h,
            (bids, asks),
            (funding_rate, oi_value),
            fear_greed,
        ) = await asyncio.gather(
            _fetch_ohlcv_resilient(client, symbol, "15m", limit=96),
            _fetch_ohlcv_resilient(client, symbol, "1h", limit=48),
            _fetch_order_book_resilient(client, symbol, limit=20),
            _fetch_derivatives(client, symbol),
            _fetch_fear_and_greed(client),
            return_exceptions=False,
        )

        # 3. Retrieve persistent SQLite calibration multiplier
        vol_multiplier = 1.0
        try:
            from prediction_ai.db.sqlite import get_sqlite_db
            sqlite = get_sqlite_db()
            cal = await sqlite.get_asset_calibration(symbol)
            vol_multiplier = float(cal.get("vol_multiplier", 1.0))
        except Exception:
            pass

        # If spot was unavailable from exchanges, fallback to candle close
        if spot <= 0 and ohlcv_15m:
            spot = ohlcv_15m[-1]["c"]
            spot_source = "15m Candle Close"
        elif spot <= 0 and ohlcv_1h:
            spot = ohlcv_1h[-1]["c"]
            spot_source = "1h Candle Close"

        # 4. Critical Data Check: If spot is still missing, we cannot compute option prob
        if spot <= 0:
            logger.warning("No spot price could be resolved across exchanges or screenshot", symbol=symbol)
            return {
                "status": "DATA_UNAVAILABLE",
                "symbol": symbol,
                "researched_at": researched_at,
                "adjusted_prob_yes": 0.5,
                "data_quality_flags": ["SPOT_PRICE_UNAVAILABLE"],
                "note": "All exchange feeds and screenshot OCR spot were unavailable. Prior 0.50 used.",
            }

        # 5. Strike check
        if strike is None or strike <= 0:
            closes_1h_prices = [c["c"] for c in ohlcv_1h]
            base_vol = _ASSET_BASE_VOL.get(symbol, 0.70)
            vol_24h = realized_vol_annualized(closes_1h_prices, 60) if closes_1h_prices else base_vol
            return {
                "status": "STRIKE_UNKNOWN",
                "symbol": symbol,
                "spot_price_usd": f"{spot:,.2f}",
                "spot_source": spot_source,
                "vol_24h_annualized": f"{vol_24h:.2%}",
                "funding_rate_pct_per_8h": f"{funding_rate:.4f}%",
                "funding_signal": interpret_funding_rate(funding_rate),
                "researched_at": researched_at,
                "adjusted_prob_yes": 0.5,
                "data_quality_flags": ["STRIKE_PRICE_NOT_DETECTED"],
                "note": "Could not detect strike price from contract proposition. Please confirm target price.",
            }

        # 6. Build quantitative market features
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
            oi_change_pct=0.0,
            price_change_24h_pct=0.0,
            high_24h=spot * 1.02,
            low_24h=spot * 0.98,
            vol_multiplier=vol_multiplier,
        )

        final_prob = features.adjusted_prob_yes
        if direction == "below":
            final_prob = 1.0 - final_prob
            features.key_signals.insert(0, f"Market asks for price BELOW {strike:,.2f} — probability inverted")

        context = features.to_context_dict()
        context.update({
            "direction": direction,
            "spot_source": spot_source,
            "final_adjusted_prob_yes": f"{final_prob:.4f}",
            "researched_at": researched_at,
            "data_sources": [spot_source, "Resilient Multi-Venue Mesh"],
            "vol_methodology": "EWMA / Calibrated Digital Option",
            "pricing_model": "Log-normal digital option (Black-Scholes, r=0)",
        })

        if spot_source == "Screenshot Point-in-Time Spot":
            context["screenshot_anchored"] = True
            context["data_quality_flags"] = ["ANCHORED_TO_SCREENSHOT_SPOT"]

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

        if oi_value is not None:
            context["open_interest_usd"] = f"${oi_value * spot:,.0f}"

        logger.info(
            "Resilient crypto intelligence successfully computed",
            symbol=symbol,
            spot=spot,
            spot_source=spot_source,
            strike=strike,
            direction=direction,
            seconds_to_expiry=int(seconds_to_expiry),
            base_prob=f"{features.base_prob_yes:.4f}",
            adjusted_prob=f"{final_prob:.4f}",
            confidence=features.signal_confidence,
        )

        return context
    finally:
        if own_client:
            await client.aclose()
