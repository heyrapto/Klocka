"""Automated settlement resolver for crypto prediction market intervals.

Fetches closing spot price at resolution time from Binance or Coinbase and determines
whether the contract resolved YES or NO based on strike price and direction.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import structlog

logger = structlog.get_logger(__name__)

_BINANCE_SPOT = "https://api.binance.com"
_BINANCE_US = "https://api.binance.us"
_OKX = "https://www.okx.com"
_BYBIT = "https://api.bybit.com"
_COINBASE_API = "https://api.coinbase.com"
_TIMEOUT = httpx.Timeout(6.0, connect=3.0)

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


def normalize_crypto_symbol(symbol_or_name: str | None) -> str:
    """Normalize input asset string to a clean uppercase ticker (e.g. 'BTC')."""
    if not symbol_or_name:
        return "BTC"
    cleaned = symbol_or_name.strip().lower()
    return _SYMBOL_MAP.get(cleaned, symbol_or_name.strip().upper())


async def fetch_crypto_settlement_price(
    symbol: str,
    target_time: datetime | None = None,
    client: httpx.AsyncClient | None = None,
) -> float | None:
    """
    Fetch the spot price for a crypto asset around resolution time.

    Attempts:
    1. Binance 1-minute klines around target_time (if target_time is in the past).
    2. Binance live ticker price.
    3. Coinbase spot price fallback.
    """
    norm_symbol = normalize_crypto_symbol(symbol)
    pair = f"{norm_symbol}USDT"
    local_client = client or httpx.AsyncClient(timeout=_TIMEOUT)

    try:
        # 1. Historical 1m kline if target_time is specified
        if target_time is not None:
            now_dt = datetime.now(UTC)
            if target_time <= now_dt:
                target_ms = int(target_time.timestamp() * 1000)
                start_ms = target_ms - 60_000
                end_ms = target_ms + 60_000
                try:
                    resp = await local_client.get(
                        f"{_BINANCE_SPOT}/api/v3/klines",
                        params={
                            "symbol": pair,
                            "interval": "1m",
                            "startTime": start_ms,
                            "endTime": end_ms,
                            "limit": 5,
                        },
                    )
                    if resp.status_code == 200:
                        klines = resp.json()
                        if isinstance(klines, list) and klines:
                            # Kline structure: [open_time, open, high, low, close, volume, close_time, ...]
                            # Pick kline closest to target_ms
                            best_kline = min(
                                klines,
                                key=lambda k: abs(int(k[6]) - target_ms),
                            )
                            close_price = float(best_kline[4])
                            logger.info(
                                "Resolved historical settlement price via Binance 1m kline",
                                symbol=norm_symbol,
                                target_time=target_time.isoformat(),
                                settlement_price=close_price,
                            )
                            return close_price
                except Exception as ex:
                    logger.debug("Binance klines fetch failed, falling back to ticker", error=str(ex))

        # 2. Binance Global Spot live ticker
        try:
            resp = await local_client.get(
                f"{_BINANCE_SPOT}/api/v3/ticker/price",
                params={"symbol": pair},
            )
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, dict) and "price" in data:
                    price = float(data["price"])
                    logger.info("Resolved settlement price via Binance Global ticker", symbol=norm_symbol, price=price)
                    return price
        except Exception:
            pass

        # 3. Binance US live ticker
        try:
            resp = await local_client.get(
                f"{_BINANCE_US}/api/v3/ticker/price",
                params={"symbol": pair},
            )
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, dict) and "price" in data:
                    price = float(data["price"])
                    logger.info("Resolved settlement price via Binance US ticker", symbol=norm_symbol, price=price)
                    return price
        except Exception:
            pass

        # 4. OKX Spot ticker
        try:
            resp = await local_client.get(
                f"{_OKX}/api/v5/market/ticker",
                params={"instId": f"{norm_symbol}-USDT"},
            )
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, dict) and data.get("data"):
                    price = float(data["data"][0]["last"])
                    logger.info("Resolved settlement price via OKX ticker", symbol=norm_symbol, price=price)
                    return price
        except Exception:
            pass

        # 5. Bybit Spot ticker
        try:
            resp = await local_client.get(
                f"{_BYBIT}/v5/market/tickers",
                params={"category": "spot", "symbol": pair},
            )
            if resp.status_code == 200:
                data = resp.json()
                items = data.get("result", {}).get("list", [])
                if items:
                    price = float(items[0]["lastPrice"])
                    logger.info("Resolved settlement price via Bybit ticker", symbol=norm_symbol, price=price)
                    return price
        except Exception:
            pass

        # 6. Coinbase spot fallback
        try:
            resp = await local_client.get(
                f"{_COINBASE_API}/v2/prices/{norm_symbol}-USD/spot"
            )
            if resp.status_code == 200:
                data = resp.json()
                amount_str = data.get("data", {}).get("amount")
                if amount_str:
                    price = float(amount_str)
                    logger.info("Resolved settlement price via Coinbase spot", symbol=norm_symbol, price=price)
                    return price
        except Exception as ex:
            logger.warning("Coinbase spot fetch failed", error=str(ex))

        return None
    finally:
        if client is None:
            await local_client.aclose()


def determine_binary_outcome(
    strike_price: float,
    settled_price: float,
    direction: str = "above",
    label_positive: str = "YES",
    label_negative: str = "NO",
) -> str:
    """
    Determine whether the binary market resolved positive (YES/UP) or negative (NO/DOWN).

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
