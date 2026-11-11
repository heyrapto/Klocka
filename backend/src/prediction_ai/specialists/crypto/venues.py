"""Normalized multi-venue crypto market-data mesh.

Every fetcher in this module targets an official, public, read-only exchange or
aggregator API.  Venues are raced concurrently; the first valid response wins
and the losing requests are cancelled.  Spot prices additionally use a short
consensus window so that a single stale venue cannot set the anchor for the
whole forecast.

The mesh deliberately does not scrape exchange websites.  Exchange front-ends
are JavaScript applications protected by bot management, their DOM changes
without notice, and scraping them would be both fragile and outside the terms
of the public API programmes that already expose exactly the same data.
"""

from __future__ import annotations

import asyncio
import math
import random
import statistics
import time
from collections.abc import Callable, Coroutine, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from typing import Any

import httpx
import structlog

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Public endpoints (no auth required for public market data)
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
_DEFILLAMA = "https://coins.llama.fi"
_CRYPTOCOMPARE = "https://min-api.cryptocompare.com"
_PYTH_BENCHMARKS = "https://benchmarks.pyth.network"
_MEXC = "https://api.mexc.com"
_GATE = "https://api.gateio.ws"
_FEAR_GREED = "https://api.alternative.me/fng/"

_MAX_RESPONSE_BYTES = 2_000_000
_TRANSIENT_STATUS = frozenset({429, 500, 502, 503, 504})
_MAX_SPOT_OUTLIER_DEVIATION = 0.02

_Candle = dict[str, float]
_OrderBook = tuple[list[tuple[float, float]], list[tuple[float, float]]]

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

_INTERVAL_SECONDS: dict[str, int] = {"1m": 60, "15m": 900, "1h": 3600}
_HISTORY_WINDOW_MS = 300_000
_HISTORY_TOLERANCE_MS = 300_000


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SpotQuote:
    price: float
    venue: str
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class SpotSnapshot:
    price: float
    source: str
    quotes: tuple[SpotQuote, ...]
    dispersion_bps: float


@dataclass(frozen=True, slots=True)
class OhlcvSnapshot:
    candles: list[_Candle]
    venue: str
    interval: str


@dataclass(frozen=True, slots=True)
class BookSnapshot:
    bids: list[tuple[float, float]]
    asks: list[tuple[float, float]]
    venue: str


@dataclass(frozen=True, slots=True)
class DerivativesSnapshot:
    funding_rate_pct: float | None
    funding_source: str | None
    open_interest_base: float | None
    open_interest_source: str | None
    open_interest_change_pct: float | None
    open_interest_change_source: str | None


@dataclass(frozen=True, slots=True)
class FearGreedSnapshot:
    value: int
    classification: str
    source: str


@dataclass(frozen=True, slots=True)
class HistoricalQuote:
    price: float
    observed_at: datetime
    venue: str


@dataclass(frozen=True, slots=True)
class _HistoricalObservation:
    price: float
    observed_at_ms: int


# ---------------------------------------------------------------------------
# Venue registries
# ---------------------------------------------------------------------------

SpotFetcher = Callable[[httpx.AsyncClient, str], Coroutine[Any, Any, float | None]]
OhlcvFetcher = Callable[
    [httpx.AsyncClient, str, str, int], Coroutine[Any, Any, list[_Candle]]
]
DepthFetcher = Callable[
    [httpx.AsyncClient, str, int], Coroutine[Any, Any, _OrderBook | None]
]
FundingFetcher = Callable[[httpx.AsyncClient, str], Coroutine[Any, Any, float | None]]
OpenInterestFetcher = Callable[
    [httpx.AsyncClient, str], Coroutine[Any, Any, float | None]
]
OpenInterestChangeFetcher = Callable[
    [httpx.AsyncClient, str], Coroutine[Any, Any, float | None]
]
HistoricalFetcher = Callable[
    [httpx.AsyncClient, str, int], Coroutine[Any, Any, _HistoricalObservation | None]
]


@dataclass(frozen=True, slots=True)
class SpotVenue:
    key: str
    label: str
    fetch: SpotFetcher


@dataclass(frozen=True, slots=True)
class OhlcvVenue:
    key: str
    label: str
    fetch: OhlcvFetcher


@dataclass(frozen=True, slots=True)
class DepthVenue:
    key: str
    label: str
    fetch: DepthFetcher


@dataclass(frozen=True, slots=True)
class FundingVenue:
    key: str
    label: str
    fetch: FundingFetcher


@dataclass(frozen=True, slots=True)
class OpenInterestVenue:
    key: str
    label: str
    fetch: OpenInterestFetcher


@dataclass(frozen=True, slots=True)
class OpenInterestChangeVenue:
    key: str
    label: str
    fetch: OpenInterestChangeFetcher


@dataclass(frozen=True, slots=True)
class HistoricalVenue:
    key: str
    label: str
    fetch: HistoricalFetcher


# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------


def _to_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _to_positive_float(value: Any) -> float | None:
    result = _to_float(value)
    return result if result is not None and result > 0 else None


def _to_ms(value: Any) -> float | None:
    result = _to_float(value)
    if result is None or result <= 0:
        return None
    return result * 1000.0 if result < 1e11 else result


def _format_error(exc: Exception) -> str:
    detail = str(exc).strip()
    return f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__


def _is_dns_failure(exc: Exception) -> bool:
    return "DNS resolution failed" in str(exc)


def _retry_after_seconds(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After")
    if not value:
        return None
    try:
        return max(0.0, min(float(value), 2.0))
    except ValueError:
        return None


async def _sleep_backoff(attempt: int) -> None:
    await asyncio.sleep(min(1.0, 0.15 * (2**attempt)) + random.uniform(0.0, 0.1))


async def _request_json(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict[str, Any] | None,
    venue: str,
    data_type: str,
    attempts: int = 2,
) -> Any | None:
    """GET JSON from one venue, retrying only transient failures."""

    headers = {"Accept": "application/json"}
    for attempt in range(max(1, attempts)):
        started = time.monotonic()
        try:
            response = await client.get(url, params=params, headers=headers)
        except httpx.HTTPError as exc:
            detail = _format_error(exc)
            retryable = not _is_dns_failure(exc) and attempt + 1 < attempts
            if retryable:
                await _sleep_backoff(attempt)
                continue
            logger.warning(
                "Crypto data fetch failed",
                venue=venue,
                data_type=data_type,
                url=url,
                host=httpx.URL(url).host,
                error=type(exc).__name__,
                error_detail=detail,
                attempt=attempt + 1,
                attempts=attempts,
                elapsed_ms=round((time.monotonic() - started) * 1000.0, 1),
            )
            return None

        elapsed_ms = round((time.monotonic() - started) * 1000.0, 1)
        if response.status_code == 200:
            if len(response.content) > _MAX_RESPONSE_BYTES:
                logger.warning(
                    "Crypto data fetch returned an oversized response",
                    venue=venue,
                    data_type=data_type,
                    url=url,
                    bytes=len(response.content),
                )
                return None
            try:
                return response.json()
            except ValueError as exc:
                logger.warning(
                    "Crypto data fetch returned invalid JSON",
                    venue=venue,
                    data_type=data_type,
                    url=url,
                    error=_format_error(exc),
                    elapsed_ms=elapsed_ms,
                )
                return None

        if response.status_code in _TRANSIENT_STATUS and attempt + 1 < attempts:
            delay = _retry_after_seconds(response)
            if delay is None:
                await _sleep_backoff(attempt)
            else:
                await asyncio.sleep(delay)
            continue

        logger.warning(
            "Crypto data fetch returned non-200",
            venue=venue,
            data_type=data_type,
            url=url,
            status=response.status_code,
            attempt=attempt + 1,
            attempts=attempts,
            elapsed_ms=elapsed_ms,
        )
        return None

    return None


# ---------------------------------------------------------------------------
# Candle normalization
# ---------------------------------------------------------------------------


def _make_candle(
    open_time: Any,
    open_price: Any,
    high: Any,
    low: Any,
    close: Any,
    volume: Any,
) -> _Candle | None:
    timestamp = _to_ms(open_time)
    parsed_open = _to_positive_float(open_price)
    parsed_high = _to_positive_float(high)
    parsed_low = _to_positive_float(low)
    parsed_close = _to_positive_float(close)
    if (
        timestamp is None
        or parsed_open is None
        or parsed_high is None
        or parsed_low is None
        or parsed_close is None
    ):
        return None
    if parsed_high < parsed_low:
        return None
    parsed_volume = _to_float(volume)
    return {
        "t": float(timestamp),
        "o": parsed_open,
        "h": parsed_high,
        "l": parsed_low,
        "c": parsed_close,
        "v": max(parsed_volume or 0.0, 0.0),
    }


def _clean_candles(
    candles: Sequence[_Candle],
    *,
    interval_seconds: int,
    min_count: int,
    limit: int,
) -> list[_Candle]:
    now_ms = datetime.now(UTC).timestamp() * 1000.0
    dedup: dict[int, _Candle] = {}
    for candle in candles:
        timestamp = int(candle["t"])
        if timestamp > 0:
            dedup[timestamp] = candle

    ordered = [dedup[timestamp] for timestamp in sorted(dedup)]
    closed = [
        candle
        for candle in ordered
        if candle["t"] + interval_seconds * 1000.0 <= now_ms + 5_000.0
    ]
    if len(closed) < min_count:
        return []
    return closed[-limit:]


def _interval_seconds(interval: str) -> int:
    try:
        return _INTERVAL_SECONDS[interval]
    except KeyError as exc:  # pragma: no cover - programmer error
        raise ValueError(f"Unsupported candle interval: {interval}") from exc


def _parse_binance_klines(
    raw: Any,
    *,
    interval: str,
    min_count: int,
    limit: int,
) -> list[_Candle]:
    if not isinstance(raw, list):
        return []
    candles: list[_Candle] = []
    for row in raw:
        if not isinstance(row, list) or len(row) < 6:
            continue
        candle = _make_candle(row[0], row[1], row[2], row[3], row[4], row[5])
        if candle is not None:
            candles.append(candle)
    return _clean_candles(
        candles,
        interval_seconds=_interval_seconds(interval),
        min_count=min_count,
        limit=limit,
    )


def _parse_okx_candles(
    raw: Any,
    *,
    interval: str,
    min_count: int,
    limit: int,
) -> list[_Candle]:
    if not isinstance(raw, dict):
        return []
    rows = raw.get("data")
    if not isinstance(rows, list):
        return []
    candles: list[_Candle] = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 6:
            continue
        if len(row) > 8 and str(row[8]) == "0":
            continue
        candle = _make_candle(row[0], row[1], row[2], row[3], row[4], row[5])
        if candle is not None:
            candles.append(candle)
    return _clean_candles(
        candles,
        interval_seconds=_interval_seconds(interval),
        min_count=min_count,
        limit=limit,
    )


def _parse_bybit_klines(
    raw: Any,
    *,
    interval: str,
    min_count: int,
    limit: int,
) -> list[_Candle]:
    if not isinstance(raw, dict):
        return []
    result = raw.get("result")
    rows = result.get("list") if isinstance(result, dict) else None
    if not isinstance(rows, list):
        return []
    candles: list[_Candle] = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 6:
            continue
        candle = _make_candle(row[0], row[1], row[2], row[3], row[4], row[5])
        if candle is not None:
            candles.append(candle)
    return _clean_candles(
        candles,
        interval_seconds=_interval_seconds(interval),
        min_count=min_count,
        limit=limit,
    )


def _parse_coinbase_candles(
    raw: Any,
    *,
    interval: str,
    min_count: int,
    limit: int,
) -> list[_Candle]:
    if not isinstance(raw, list):
        return []
    candles: list[_Candle] = []
    for row in raw:
        if not isinstance(row, list) or len(row) < 6:
            continue
        # Coinbase Exchange: [time, low, high, open, close, volume]
        candle = _make_candle(row[0], row[3], row[2], row[1], row[4], row[5])
        if candle is not None:
            candles.append(candle)
    return _clean_candles(
        candles,
        interval_seconds=_interval_seconds(interval),
        min_count=min_count,
        limit=limit,
    )


def _parse_kraken_ohlc(
    raw: Any,
    *,
    interval: str,
    min_count: int,
    limit: int,
) -> list[_Candle]:
    if not isinstance(raw, dict) or raw.get("error"):
        return []
    result = raw.get("result")
    if not isinstance(result, dict):
        return []
    rows = next((value for value in result.values() if isinstance(value, list)), None)
    if not isinstance(rows, list):
        return []
    candles: list[_Candle] = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 7:
            continue
        # Kraken: [time, open, high, low, close, vwap, volume, count]
        candle = _make_candle(row[0], row[1], row[2], row[3], row[4], row[6])
        if candle is not None:
            candles.append(candle)
    return _clean_candles(
        candles,
        interval_seconds=_interval_seconds(interval),
        min_count=min_count,
        limit=limit,
    )


def _parse_gate_candles(
    raw: Any,
    *,
    interval: str,
    min_count: int,
    limit: int,
) -> list[_Candle]:
    if not isinstance(raw, list):
        return []
    candles: list[_Candle] = []
    for row in raw:
        if not isinstance(row, list) or len(row) < 7:
            continue
        if len(row) > 7 and str(row[7]).lower() == "false":
            continue
        # Gate.io: [time, quote_volume, close, high, low, open, base_volume, closed]
        candle = _make_candle(row[0], row[5], row[3], row[4], row[2], row[6])
        if candle is not None:
            candles.append(candle)
    return _clean_candles(
        candles,
        interval_seconds=_interval_seconds(interval),
        min_count=min_count,
        limit=limit,
    )


# ---------------------------------------------------------------------------
# Order-book normalization
# ---------------------------------------------------------------------------


def _levels_to_pairs(raw: Any) -> list[tuple[float, float]]:
    if not isinstance(raw, list):
        return []
    result: list[tuple[float, float]] = []
    for row in raw:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        price = _to_positive_float(row[0])
        quantity = _to_positive_float(row[1])
        if price is not None and quantity is not None:
            result.append((price, quantity))
    return result


def _normalize_book(
    bids_raw: Any,
    asks_raw: Any,
    limit: int,
) -> _OrderBook | None:
    bids = sorted(_levels_to_pairs(bids_raw), key=lambda item: item[0], reverse=True)
    asks = sorted(_levels_to_pairs(asks_raw), key=lambda item: item[0])
    if not bids or not asks:
        return None
    if bids[0][0] > asks[0][0]:
        return None
    return bids[:limit], asks[:limit]


def _parse_binance_book(raw: Any, limit: int) -> _OrderBook | None:
    if not isinstance(raw, dict):
        return None
    return _normalize_book(raw.get("bids"), raw.get("asks"), limit)


def _parse_okx_book(raw: Any, limit: int) -> _OrderBook | None:
    if not isinstance(raw, dict):
        return None
    rows = raw.get("data")
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
        return None
    return _normalize_book(rows[0].get("bids"), rows[0].get("asks"), limit)


def _parse_bybit_book(raw: Any, limit: int) -> _OrderBook | None:
    if not isinstance(raw, dict):
        return None
    result = raw.get("result")
    if not isinstance(result, dict):
        return None
    return _normalize_book(result.get("b"), result.get("a"), limit)


def _parse_coinbase_book(raw: Any, limit: int) -> _OrderBook | None:
    if not isinstance(raw, dict):
        return None
    return _normalize_book(raw.get("bids"), raw.get("asks"), limit)


def _parse_kraken_book(raw: Any, limit: int) -> _OrderBook | None:
    if not isinstance(raw, dict) or raw.get("error"):
        return None
    result = raw.get("result")
    if not isinstance(result, dict):
        return None
    book = next((value for value in result.values() if isinstance(value, dict)), None)
    if not isinstance(book, dict):
        return None
    return _normalize_book(book.get("bids"), book.get("asks"), limit)


def _parse_gate_book(raw: Any, limit: int) -> _OrderBook | None:
    if not isinstance(raw, dict):
        return None
    return _normalize_book(raw.get("bids"), raw.get("asks"), limit)


# ---------------------------------------------------------------------------
# Symbol helpers
# ---------------------------------------------------------------------------


def _binance_pair(symbol: str) -> str:
    return f"{symbol}USDT"


def _okx_pair(symbol: str) -> str:
    return f"{symbol}-USDT"


def _okx_swap(symbol: str) -> str:
    return f"{symbol}-USDT-SWAP"


def _bybit_pair(symbol: str) -> str:
    return f"{symbol}USDT"


def _coinbase_pair(symbol: str) -> str:
    return f"{symbol}-USD"


def _mexc_pair(symbol: str) -> str:
    return f"{symbol}USDT"


def _gate_pair(symbol: str) -> str:
    return f"{symbol}_USDT"


def _kraken_pair(symbol: str) -> str:
    return _KRAKEN_PAIR_MAP.get(symbol, f"{symbol}USD")


def _coingecko_id(symbol: str) -> str | None:
    return _COINGECKO_MAP.get(symbol)


# ---------------------------------------------------------------------------
# Spot fetchers
# ---------------------------------------------------------------------------


async def _spot_binance(client: httpx.AsyncClient, symbol: str) -> float | None:
    data = await _request_json(
        client,
        f"{_BINANCE_SPOT}/api/v3/ticker/price",
        params={"symbol": _binance_pair(symbol)},
        venue="Binance Global",
        data_type="spot",
    )
    if isinstance(data, dict):
        return _to_positive_float(data.get("price"))
    return None


async def _spot_binance_us(client: httpx.AsyncClient, symbol: str) -> float | None:
    data = await _request_json(
        client,
        f"{_BINANCE_US}/api/v3/ticker/price",
        params={"symbol": _binance_pair(symbol)},
        venue="Binance US",
        data_type="spot",
    )
    if isinstance(data, dict):
        return _to_positive_float(data.get("price"))
    return None


async def _spot_okx(client: httpx.AsyncClient, symbol: str) -> float | None:
    data = await _request_json(
        client,
        f"{_OKX}/api/v5/market/ticker",
        params={"instId": _okx_pair(symbol)},
        venue="OKX",
        data_type="spot",
    )
    if isinstance(data, dict):
        rows = data.get("data")
        if isinstance(rows, list) and rows and isinstance(rows[0], dict):
            return _to_positive_float(rows[0].get("last"))
    return None


async def _spot_bybit(client: httpx.AsyncClient, symbol: str) -> float | None:
    data = await _request_json(
        client,
        f"{_BYBIT}/v5/market/tickers",
        params={"category": "spot", "symbol": _bybit_pair(symbol)},
        venue="Bybit",
        data_type="spot",
    )
    if isinstance(data, dict):
        result = data.get("result")
        rows = result.get("list") if isinstance(result, dict) else None
        if isinstance(rows, list) and rows and isinstance(rows[0], dict):
            return _to_positive_float(rows[0].get("lastPrice"))
    return None


async def _spot_coinbase(client: httpx.AsyncClient, symbol: str) -> float | None:
    data = await _request_json(
        client,
        f"{_COINBASE_API}/v2/prices/{_coinbase_pair(symbol)}/spot",
        params=None,
        venue="Coinbase",
        data_type="spot",
    )
    if isinstance(data, dict):
        payload = data.get("data")
        if isinstance(payload, dict):
            return _to_positive_float(payload.get("amount"))
    return None


async def _spot_kraken(client: httpx.AsyncClient, symbol: str) -> float | None:
    data = await _request_json(
        client,
        f"{_KRAKEN}/0/public/Ticker",
        params={"pair": _kraken_pair(symbol)},
        venue="Kraken",
        data_type="spot",
    )
    if isinstance(data, dict):
        result = data.get("result")
        if isinstance(result, dict):
            ticker = next((value for value in result.values() if isinstance(value, dict)), None)
            if isinstance(ticker, dict):
                close = ticker.get("c")
                if isinstance(close, list) and close:
                    return _to_positive_float(close[0])
    return None


async def _spot_coingecko(client: httpx.AsyncClient, symbol: str) -> float | None:
    coin_id = _coingecko_id(symbol)
    if coin_id is None:
        return None
    data = await _request_json(
        client,
        f"{_COINGECKO}/api/v3/simple/price",
        params={"ids": coin_id, "vs_currencies": "usd", "include_last_updated_at": "true"},
        venue="CoinGecko",
        data_type="spot",
    )
    if isinstance(data, dict):
        payload = data.get(coin_id)
        if isinstance(payload, dict):
            return _to_positive_float(payload.get("usd"))
    return None


async def _spot_defillama(client: httpx.AsyncClient, symbol: str) -> float | None:
    coin_id = _coingecko_id(symbol)
    if coin_id is None:
        return None
    key = f"coingecko:{coin_id}"
    data = await _request_json(
        client,
        f"{_DEFILLAMA}/prices/current/{key}",
        params=None,
        venue="DefiLlama",
        data_type="spot",
    )
    if isinstance(data, dict):
        coins = data.get("coins")
        if isinstance(coins, dict):
            payload = coins.get(key)
            if isinstance(payload, dict):
                confidence = _to_float(payload.get("confidence"))
                if confidence is not None and confidence < 0.9:
                    return None
                return _to_positive_float(payload.get("price"))
    return None


async def _spot_mexc(client: httpx.AsyncClient, symbol: str) -> float | None:
    data = await _request_json(
        client,
        f"{_MEXC}/api/v3/ticker/price",
        params={"symbol": _mexc_pair(symbol)},
        venue="MEXC",
        data_type="spot",
    )
    if isinstance(data, dict):
        return _to_positive_float(data.get("price"))
    return None


async def _spot_gate(client: httpx.AsyncClient, symbol: str) -> float | None:
    data = await _request_json(
        client,
        f"{_GATE}/api/v4/spot/tickers",
        params={"currency_pair": _gate_pair(symbol)},
        venue="Gate.io",
        data_type="spot",
    )
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return _to_positive_float(data[0].get("last"))
    return None


# ---------------------------------------------------------------------------
# OHLCV fetchers
# ---------------------------------------------------------------------------


async def _ohlcv_binance(
    client: httpx.AsyncClient, symbol: str, interval: str, limit: int
) -> list[_Candle]:
    raw = await _request_json(
        client,
        f"{_BINANCE_SPOT}/api/v3/klines",
        params={"symbol": _binance_pair(symbol), "interval": interval, "limit": limit},
        venue="Binance Global",
        data_type=f"ohlcv_{interval}",
    )
    return _parse_binance_klines(raw, interval=interval, min_count=24, limit=limit)


async def _ohlcv_binance_us(
    client: httpx.AsyncClient, symbol: str, interval: str, limit: int
) -> list[_Candle]:
    raw = await _request_json(
        client,
        f"{_BINANCE_US}/api/v3/klines",
        params={"symbol": _binance_pair(symbol), "interval": interval, "limit": limit},
        venue="Binance US",
        data_type=f"ohlcv_{interval}",
    )
    return _parse_binance_klines(raw, interval=interval, min_count=24, limit=limit)


async def _ohlcv_okx(
    client: httpx.AsyncClient, symbol: str, interval: str, limit: int
) -> list[_Candle]:
    bar = "15m" if interval == "15m" else "1H"
    raw = await _request_json(
        client,
        f"{_OKX}/api/v5/market/candles",
        params={"instId": _okx_pair(symbol), "bar": bar, "limit": str(limit)},
        venue="OKX",
        data_type=f"ohlcv_{interval}",
    )
    return _parse_okx_candles(raw, interval=interval, min_count=24, limit=limit)


async def _ohlcv_bybit(
    client: httpx.AsyncClient, symbol: str, interval: str, limit: int
) -> list[_Candle]:
    bybit_interval = "15" if interval == "15m" else "60"
    raw = await _request_json(
        client,
        f"{_BYBIT}/v5/market/kline",
        params={
            "category": "spot",
            "symbol": _bybit_pair(symbol),
            "interval": bybit_interval,
            "limit": limit,
        },
        venue="Bybit",
        data_type=f"ohlcv_{interval}",
    )
    return _parse_bybit_klines(raw, interval=interval, min_count=24, limit=limit)


async def _ohlcv_coinbase(
    client: httpx.AsyncClient, symbol: str, interval: str, limit: int
) -> list[_Candle]:
    granularity = 900 if interval == "15m" else 3600
    raw = await _request_json(
        client,
        f"{_COINBASE_EXCHANGE}/products/{_coinbase_pair(symbol)}/candles",
        params={"granularity": granularity},
        venue="Coinbase Exchange",
        data_type=f"ohlcv_{interval}",
    )
    return _parse_coinbase_candles(raw, interval=interval, min_count=24, limit=limit)


async def _ohlcv_kraken(
    client: httpx.AsyncClient, symbol: str, interval: str, limit: int
) -> list[_Candle]:
    kraken_interval = 15 if interval == "15m" else 60
    raw = await _request_json(
        client,
        f"{_KRAKEN}/0/public/OHLC",
        params={"pair": _kraken_pair(symbol), "interval": kraken_interval},
        venue="Kraken",
        data_type=f"ohlcv_{interval}",
    )
    return _parse_kraken_ohlc(raw, interval=interval, min_count=24, limit=limit)


async def _ohlcv_mexc(
    client: httpx.AsyncClient, symbol: str, interval: str, limit: int
) -> list[_Candle]:
    mexc_interval = "60m" if interval == "1h" else interval
    raw = await _request_json(
        client,
        f"{_MEXC}/api/v3/klines",
        params={"symbol": _mexc_pair(symbol), "interval": mexc_interval, "limit": limit},
        venue="MEXC",
        data_type=f"ohlcv_{interval}",
    )
    return _parse_binance_klines(raw, interval=interval, min_count=24, limit=limit)


async def _ohlcv_gate(
    client: httpx.AsyncClient, symbol: str, interval: str, limit: int
) -> list[_Candle]:
    raw = await _request_json(
        client,
        f"{_GATE}/api/v4/spot/candlesticks",
        params={
            "currency_pair": _gate_pair(symbol),
            "interval": interval,
            "limit": limit,
        },
        venue="Gate.io",
        data_type=f"ohlcv_{interval}",
    )
    return _parse_gate_candles(raw, interval=interval, min_count=24, limit=limit)


# ---------------------------------------------------------------------------
# Depth fetchers
# ---------------------------------------------------------------------------


async def _depth_binance(
    client: httpx.AsyncClient, symbol: str, limit: int
) -> _OrderBook | None:
    raw = await _request_json(
        client,
        f"{_BINANCE_SPOT}/api/v3/depth",
        params={"symbol": _binance_pair(symbol), "limit": limit},
        venue="Binance Global",
        data_type="order_book",
    )
    return _parse_binance_book(raw, limit)


async def _depth_binance_us(
    client: httpx.AsyncClient, symbol: str, limit: int
) -> _OrderBook | None:
    raw = await _request_json(
        client,
        f"{_BINANCE_US}/api/v3/depth",
        params={"symbol": _binance_pair(symbol), "limit": limit},
        venue="Binance US",
        data_type="order_book",
    )
    return _parse_binance_book(raw, limit)


async def _depth_okx(
    client: httpx.AsyncClient, symbol: str, limit: int
) -> _OrderBook | None:
    raw = await _request_json(
        client,
        f"{_OKX}/api/v5/market/books",
        params={"instId": _okx_pair(symbol), "sz": str(limit)},
        venue="OKX",
        data_type="order_book",
    )
    return _parse_okx_book(raw, limit)


async def _depth_bybit(
    client: httpx.AsyncClient, symbol: str, limit: int
) -> _OrderBook | None:
    raw = await _request_json(
        client,
        f"{_BYBIT}/v5/market/orderbook",
        params={"category": "spot", "symbol": _bybit_pair(symbol), "limit": limit},
        venue="Bybit",
        data_type="order_book",
    )
    return _parse_bybit_book(raw, limit)


async def _depth_coinbase(
    client: httpx.AsyncClient, symbol: str, limit: int
) -> _OrderBook | None:
    raw = await _request_json(
        client,
        f"{_COINBASE_EXCHANGE}/products/{_coinbase_pair(symbol)}/book",
        params={"level": 2},
        venue="Coinbase Exchange",
        data_type="order_book",
    )
    return _parse_coinbase_book(raw, limit)


async def _depth_kraken(
    client: httpx.AsyncClient, symbol: str, limit: int
) -> _OrderBook | None:
    raw = await _request_json(
        client,
        f"{_KRAKEN}/0/public/Depth",
        params={"pair": _kraken_pair(symbol), "count": limit},
        venue="Kraken",
        data_type="order_book",
    )
    return _parse_kraken_book(raw, limit)


async def _depth_mexc(
    client: httpx.AsyncClient, symbol: str, limit: int
) -> _OrderBook | None:
    raw = await _request_json(
        client,
        f"{_MEXC}/api/v3/depth",
        params={"symbol": _mexc_pair(symbol), "limit": limit},
        venue="MEXC",
        data_type="order_book",
    )
    return _parse_binance_book(raw, limit)


async def _depth_gate(
    client: httpx.AsyncClient, symbol: str, limit: int
) -> _OrderBook | None:
    raw = await _request_json(
        client,
        f"{_GATE}/api/v4/spot/order_book",
        params={"currency_pair": _gate_pair(symbol), "limit": limit},
        venue="Gate.io",
        data_type="order_book",
    )
    return _parse_gate_book(raw, limit)


# ---------------------------------------------------------------------------
# Derivatives fetchers
# ---------------------------------------------------------------------------


async def _funding_binance(client: httpx.AsyncClient, symbol: str) -> float | None:
    pair = _binance_pair(symbol)
    current = await _request_json(
        client,
        f"{_BINANCE_FUTURES}/fapi/v1/premiumIndex",
        params={"symbol": pair},
        venue="Binance Futures",
        data_type="funding_rate",
    )
    if isinstance(current, dict):
        rate = _to_float(current.get("lastFundingRate"))
        if rate is not None:
            return rate * 100.0

    history = await _request_json(
        client,
        f"{_BINANCE_FUTURES}/fapi/v1/fundingRate",
        params={"symbol": pair, "limit": 1},
        venue="Binance Futures",
        data_type="funding_rate",
    )
    if isinstance(history, list) and history and isinstance(history[-1], dict):
        rate = _to_float(history[-1].get("fundingRate"))
        if rate is not None:
            return rate * 100.0
    return None


async def _funding_bybit(client: httpx.AsyncClient, symbol: str) -> float | None:
    data = await _request_json(
        client,
        f"{_BYBIT}/v5/market/tickers",
        params={"category": "linear", "symbol": _bybit_pair(symbol)},
        venue="Bybit",
        data_type="funding_rate",
    )
    if isinstance(data, dict):
        result = data.get("result")
        rows = result.get("list") if isinstance(result, dict) else None
        if isinstance(rows, list) and rows and isinstance(rows[0], dict):
            rate = _to_float(rows[0].get("fundingRate"))
            if rate is not None:
                return rate * 100.0
    return None


async def _funding_okx(client: httpx.AsyncClient, symbol: str) -> float | None:
    data = await _request_json(
        client,
        f"{_OKX}/api/v5/public/funding-rate",
        params={"instId": _okx_swap(symbol)},
        venue="OKX",
        data_type="funding_rate",
    )
    if isinstance(data, dict):
        rows = data.get("data")
        if isinstance(rows, list) and rows and isinstance(rows[0], dict):
            rate = _to_float(rows[0].get("fundingRate"))
            if rate is not None:
                return rate * 100.0
    return None


async def _funding_gate(client: httpx.AsyncClient, symbol: str) -> float | None:
    data = await _request_json(
        client,
        f"{_GATE}/api/v4/futures/usdt/contracts/{_gate_pair(symbol)}",
        params=None,
        venue="Gate.io",
        data_type="funding_rate",
    )
    if not isinstance(data, dict):
        return None
    rate = _to_float(data.get("funding_rate"))
    if rate is None:
        return None
    interval = _to_float(data.get("funding_interval")) or 28_800.0
    if interval <= 0:
        return rate * 100.0
    # Normalize every venue to the 8-hour convention used by the model.
    return rate * 100.0 * (28_800.0 / interval)


async def _funding_mexc(client: httpx.AsyncClient, symbol: str) -> float | None:
    data = await _request_json(
        client,
        f"{_MEXC}/api/v1/contract/ticker",
        params={"symbol": _gate_pair(symbol)},
        venue="MEXC",
        data_type="funding_rate",
    )
    if isinstance(data, dict):
        payload = data.get("data")
        if isinstance(payload, dict):
            rate = _to_float(payload.get("fundingRate"))
            if rate is not None:
                return rate * 100.0
    return None


async def _open_interest_binance(
    client: httpx.AsyncClient, symbol: str
) -> float | None:
    data = await _request_json(
        client,
        f"{_BINANCE_FUTURES}/fapi/v1/openInterest",
        params={"symbol": _binance_pair(symbol)},
        venue="Binance Futures",
        data_type="open_interest",
    )
    if isinstance(data, dict):
        return _to_positive_float(data.get("openInterest"))
    return None


async def _open_interest_bybit(
    client: httpx.AsyncClient, symbol: str
) -> float | None:
    data = await _request_json(
        client,
        f"{_BYBIT}/v5/market/tickers",
        params={"category": "linear", "symbol": _bybit_pair(symbol)},
        venue="Bybit",
        data_type="open_interest",
    )
    if isinstance(data, dict):
        result = data.get("result")
        rows = result.get("list") if isinstance(result, dict) else None
        if isinstance(rows, list) and rows and isinstance(rows[0], dict):
            row = rows[0]
            direct = _to_positive_float(row.get("openInterest"))
            if direct is not None:
                return direct
            value = _to_positive_float(row.get("openInterestValue"))
            price = _to_positive_float(row.get("lastPrice"))
            if value is not None and price is not None:
                return value / price
    return None


async def _open_interest_okx(client: httpx.AsyncClient, symbol: str) -> float | None:
    data = await _request_json(
        client,
        f"{_OKX}/api/v5/public/open-interest",
        params={"instId": _okx_swap(symbol)},
        venue="OKX",
        data_type="open_interest",
    )
    if isinstance(data, dict):
        rows = data.get("data")
        if isinstance(rows, list) and rows and isinstance(rows[0], dict):
            return _to_positive_float(rows[0].get("oiCcy"))
    return None


async def _open_interest_change_binance(
    client: httpx.AsyncClient, symbol: str
) -> float | None:
    data = await _request_json(
        client,
        f"{_BINANCE_FUTURES}/futures/data/openInterestHist",
        params={"symbol": _binance_pair(symbol), "period": "1h", "limit": 24},
        venue="Binance Futures",
        data_type="open_interest_change",
    )
    if not isinstance(data, list) or len(data) < 2:
        return None
    first = _to_positive_float(data[0].get("sumOpenInterest")) if isinstance(data[0], dict) else None
    last = _to_positive_float(data[-1].get("sumOpenInterest")) if isinstance(data[-1], dict) else None
    if first is None or last is None:
        return None
    return (last / first - 1.0) * 100.0


async def _open_interest_change_bybit(
    client: httpx.AsyncClient, symbol: str
) -> float | None:
    data = await _request_json(
        client,
        f"{_BYBIT}/v5/market/open-interest",
        params={
            "category": "linear",
            "symbol": _bybit_pair(symbol),
            "intervalTime": "1h",
            "limit": 24,
        },
        venue="Bybit",
        data_type="open_interest_change",
    )
    if not isinstance(data, dict):
        return None
    result = data.get("result")
    rows = result.get("list") if isinstance(result, dict) else None
    if not isinstance(rows, list) or len(rows) < 2:
        return None
    points: list[tuple[float, float]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        timestamp = _to_float(row.get("timestamp"))
        value = _to_positive_float(row.get("openInterest"))
        if timestamp is not None and value is not None:
            points.append((timestamp, value))
    if len(points) < 2:
        return None
    points.sort(key=lambda item: item[0])
    return (points[-1][1] / points[0][1] - 1.0) * 100.0


# ---------------------------------------------------------------------------
# Historical settlement fetchers
# ---------------------------------------------------------------------------


def _closest_candle_observation(
    candles: Sequence[_Candle],
    target_ms: int,
    *,
    tolerance_ms: int = _HISTORY_TOLERANCE_MS,
) -> _HistoricalObservation | None:
    """Return the candle close nearest to the target instant.

    Settlement is only allowed to use a candle whose close is temporally close
    to the requested resolution time.  Without this check a venue that silently
    ignores the requested time window could return an unrelated much-later
    price and flip the contract outcome.
    """

    best: tuple[int, int, float] | None = None
    for candle in candles:
        close_ms = int(candle["t"]) + 60_000
        distance = abs(close_ms - target_ms)
        if distance > tolerance_ms:
            continue
        candidate = (distance, close_ms, candle["c"])
        if best is None or candidate[0] < best[0]:
            best = candidate
    if best is None:
        return None
    return _HistoricalObservation(price=best[2], observed_at_ms=best[1])


def _closest_point_observation(
    points: Sequence[tuple[int, float]],
    target_ms: int,
    *,
    tolerance_ms: int = _HISTORY_TOLERANCE_MS,
) -> _HistoricalObservation | None:
    best: tuple[int, int, float] | None = None
    for timestamp_ms, price in points:
        distance = abs(timestamp_ms - target_ms)
        if distance > tolerance_ms or price <= 0:
            continue
        candidate = (distance, timestamp_ms, price)
        if best is None or candidate[0] < best[0]:
            best = candidate
    if best is None:
        return None
    return _HistoricalObservation(price=best[2], observed_at_ms=best[1])


async def _history_binance_style(
    client: httpx.AsyncClient,
    base_url: str,
    pair: str,
    target_ms: int,
    *,
    venue: str,
) -> _HistoricalObservation | None:
    raw = await _request_json(
        client,
        f"{base_url}/api/v3/klines",
        params={
            "symbol": pair,
            "interval": "1m",
            "startTime": target_ms - _HISTORY_WINDOW_MS,
            "endTime": target_ms + _HISTORY_WINDOW_MS,
            "limit": 5,
        },
        venue=venue,
        data_type="historical_spot",
    )
    candles = _parse_binance_klines(raw, interval="1m", min_count=1, limit=5)
    return _closest_candle_observation(candles, target_ms)


async def _history_binance(
    client: httpx.AsyncClient, symbol: str, target_ms: int
) -> _HistoricalObservation | None:
    return await _history_binance_style(
        client,
        _BINANCE_SPOT,
        _binance_pair(symbol),
        target_ms,
        venue="Binance Global",
    )


async def _history_binance_us(
    client: httpx.AsyncClient, symbol: str, target_ms: int
) -> _HistoricalObservation | None:
    return await _history_binance_style(
        client,
        _BINANCE_US,
        _binance_pair(symbol),
        target_ms,
        venue="Binance US",
    )


async def _history_mexc(
    client: httpx.AsyncClient, symbol: str, target_ms: int
) -> _HistoricalObservation | None:
    return await _history_binance_style(
        client,
        _MEXC,
        _mexc_pair(symbol),
        target_ms,
        venue="MEXC",
    )


async def _history_gate(
    client: httpx.AsyncClient, symbol: str, target_ms: int
) -> _HistoricalObservation | None:
    target_seconds = target_ms // 1000
    raw = await _request_json(
        client,
        f"{_GATE}/api/v4/spot/candlesticks",
        params={
            "currency_pair": _gate_pair(symbol),
            "from": target_seconds - _HISTORY_WINDOW_MS // 1000,
            "to": target_seconds + _HISTORY_WINDOW_MS // 1000,
            "interval": "1m",
        },
        venue="Gate.io",
        data_type="historical_spot",
    )
    candles = _parse_gate_candles(raw, interval="1m", min_count=1, limit=5)
    return _closest_candle_observation(candles, target_ms)


async def _history_bitstamp(
    client: httpx.AsyncClient, symbol: str, target_ms: int
) -> _HistoricalObservation | None:
    target_seconds = target_ms // 1000
    raw = await _request_json(
        client,
        f"https://www.bitstamp.net/api/v2/ohlc/{symbol.lower()}usd/",
        params={
            "step": 60,
            "limit": 5,
            "start": target_seconds - _HISTORY_WINDOW_MS // 1000,
            "end": target_seconds + _HISTORY_WINDOW_MS // 1000,
        },
        venue="Bitstamp",
        data_type="historical_spot",
    )
    if not isinstance(raw, dict):
        return None
    payload = raw.get("data")
    rows = payload.get("ohlc") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return None
    candles: list[_Candle] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        candle = _make_candle(
            row.get("timestamp"),
            row.get("open"),
            row.get("high"),
            row.get("low"),
            row.get("close"),
            row.get("volume"),
        )
        if candle is not None:
            candles.append(candle)
    return _closest_candle_observation(candles, target_ms)


async def _history_coinbase(
    client: httpx.AsyncClient, symbol: str, target_ms: int
) -> _HistoricalObservation | None:
    target_seconds = target_ms // 1000
    start_dt = datetime.fromtimestamp(target_seconds - _HISTORY_WINDOW_MS // 1000, tz=UTC)
    end_dt = datetime.fromtimestamp(target_seconds + _HISTORY_WINDOW_MS // 1000, tz=UTC)
    raw = await _request_json(
        client,
        f"{_COINBASE_EXCHANGE}/products/{_coinbase_pair(symbol)}/candles",
        params={
            "granularity": 60,
            "start": start_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end": end_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        venue="Coinbase Exchange",
        data_type="historical_spot",
    )
    candles = _parse_coinbase_candles(raw, interval="1m", min_count=1, limit=5)
    return _closest_candle_observation(candles, target_ms)


async def _history_defillama(
    client: httpx.AsyncClient, symbol: str, target_ms: int
) -> _HistoricalObservation | None:
    coin_id = _coingecko_id(symbol)
    if coin_id is None:
        return None
    target_seconds = target_ms // 1000
    key = f"coingecko:{coin_id}"
    raw = await _request_json(
        client,
        f"{_DEFILLAMA}/prices/historical/{target_seconds}/{key}",
        params={"searchWidth": 600},
        venue="DefiLlama",
        data_type="historical_spot",
    )
    if not isinstance(raw, dict):
        return None
    coins = raw.get("coins")
    if not isinstance(coins, dict):
        return None
    payload = coins.get(key)
    if not isinstance(payload, dict):
        return None
    price = _to_positive_float(payload.get("price"))
    observed_ts = _to_float(payload.get("timestamp"))
    if price is None or observed_ts is None:
        return None
    observed_ms = int(observed_ts * 1000)
    if abs(observed_ms - target_ms) > _HISTORY_TOLERANCE_MS:
        return None
    return _HistoricalObservation(price=price, observed_at_ms=observed_ms)


async def _history_cryptocompare(
    client: httpx.AsyncClient, symbol: str, target_ms: int
) -> _HistoricalObservation | None:
    target_seconds = target_ms // 1000
    raw = await _request_json(
        client,
        f"{_CRYPTOCOMPARE}/data/v2/histominute",
        params={
            "fsym": symbol.upper(),
            "tsym": "USD",
            "limit": 5,
            "toTs": target_seconds + 60,
        },
        venue="CryptoCompare",
        data_type="historical_spot",
    )
    if not isinstance(raw, dict) or raw.get("Response") != "Success":
        return None
    data = raw.get("Data")
    if not isinstance(data, dict):
        return None
    rows = data.get("Data")
    if not isinstance(rows, list):
        return None
    candles: list[_Candle] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        candle = _make_candle(
            row.get("time"),
            row.get("open"),
            row.get("high"),
            row.get("low"),
            row.get("close"),
            row.get("volumeto"),
        )
        if candle is not None:
            candles.append(candle)
    return _closest_candle_observation(candles, target_ms)


async def _history_pyth(
    client: httpx.AsyncClient, symbol: str, target_ms: int
) -> _HistoricalObservation | None:
    target_seconds = target_ms // 1000
    raw = await _request_json(
        client,
        f"{_PYTH_BENCHMARKS}/v1/shims/tradingview/history",
        params={
            "symbol": f"Crypto.{symbol.upper()}/USD",
            "resolution": "1",
            "from": target_seconds - 180,
            "to": target_seconds + 180,
        },
        venue="Pyth Network",
        data_type="historical_spot",
    )
    if not isinstance(raw, dict) or raw.get("s") != "ok":
        return None
    timestamps = raw.get("t")
    closes = raw.get("c")
    if not isinstance(timestamps, list) or not isinstance(closes, list):
        return None
    points: list[tuple[int, float]] = []
    for t_val, c_val in zip(timestamps, closes, strict=False):
        t_ms = _to_ms(t_val)
        c_price = _to_positive_float(c_val)
        if t_ms is not None and c_price is not None:
            points.append((int(t_ms), c_price))
    return _closest_point_observation(points, target_ms)


async def _history_bybit(
    client: httpx.AsyncClient, symbol: str, target_ms: int
) -> _HistoricalObservation | None:
    raw = await _request_json(
        client,
        f"{_BYBIT}/v5/market/kline",
        params={
            "category": "spot",
            "symbol": _bybit_pair(symbol),
            "interval": "1",
            "start": target_ms - _HISTORY_WINDOW_MS,
            "end": target_ms + _HISTORY_WINDOW_MS,
            "limit": 5,
        },
        venue="Bybit",
        data_type="historical_spot",
    )
    candles = _parse_bybit_klines(raw, interval="1m", min_count=1, limit=5)
    return _closest_candle_observation(candles, target_ms)


async def _history_okx(
    client: httpx.AsyncClient, symbol: str, target_ms: int
) -> _HistoricalObservation | None:
    raw = await _request_json(
        client,
        f"{_OKX}/api/v5/market/history-candles",
        params={
            "instId": _okx_pair(symbol),
            "before": str(target_ms - _HISTORY_WINDOW_MS),
            "after": str(target_ms + _HISTORY_WINDOW_MS),
            "bar": "1m",
            "limit": "5",
        },
        venue="OKX",
        data_type="historical_spot",
    )
    if not isinstance(raw, dict) or not raw.get("data"):
        raw = await _request_json(
            client,
            f"{_OKX}/api/v5/market/candles",
            params={
                "instId": _okx_pair(symbol),
                "before": str(target_ms - _HISTORY_WINDOW_MS),
                "after": str(target_ms + _HISTORY_WINDOW_MS),
                "bar": "1m",
                "limit": "5",
            },
            venue="OKX",
            data_type="historical_spot",
        )
    candles = _parse_okx_candles(raw, interval="1m", min_count=1, limit=5)
    return _closest_candle_observation(candles, target_ms)


async def _history_kraken(
    client: httpx.AsyncClient, symbol: str, target_ms: int
) -> _HistoricalObservation | None:
    target_seconds = target_ms // 1000
    raw = await _request_json(
        client,
        f"{_KRAKEN}/0/public/OHLC",
        params={
            "pair": _kraken_pair(symbol),
            "interval": 1,
            "since": target_seconds - _HISTORY_WINDOW_MS // 1000,
        },
        venue="Kraken",
        data_type="historical_spot",
    )
    candles = _parse_kraken_ohlc(raw, interval="1m", min_count=1, limit=720)
    return _closest_candle_observation(candles, target_ms)


async def _history_coingecko(
    client: httpx.AsyncClient, symbol: str, target_ms: int
) -> _HistoricalObservation | None:
    coin_id = _coingecko_id(symbol)
    if coin_id is None:
        return None
    target_seconds = target_ms // 1000
    raw = await _request_json(
        client,
        f"{_COINGECKO}/api/v3/coins/{coin_id}/market_chart/range",
        params={
            "vs_currency": "usd",
            "from": target_seconds - 600,
            "to": target_seconds + 600,
        },
        venue="CoinGecko",
        data_type="historical_spot",
    )
    if not isinstance(raw, dict):
        return None
    prices = raw.get("prices")
    if not isinstance(prices, list):
        return None
    points: list[tuple[int, float]] = []
    for row in prices:
        if not isinstance(row, list) or len(row) < 2:
            continue
        timestamp = _to_float(row[0])
        price = _to_positive_float(row[1])
        if timestamp is None or price is None:
            continue
        points.append((int(timestamp), price))
    return _closest_point_observation(points, target_ms)


# ---------------------------------------------------------------------------
# Venue catalogues
# ---------------------------------------------------------------------------

SPOT_VENUES: tuple[SpotVenue, ...] = (
    SpotVenue("binance", "Binance Global", _spot_binance),
    SpotVenue("coinbase", "Coinbase", _spot_coinbase),
    SpotVenue("kraken", "Kraken", _spot_kraken),
    SpotVenue("okx", "OKX", _spot_okx),
    SpotVenue("bybit", "Bybit", _spot_bybit),
    SpotVenue("binance_us", "Binance US", _spot_binance_us),
    SpotVenue("mexc", "MEXC", _spot_mexc),
    SpotVenue("gate", "Gate.io", _spot_gate),
    SpotVenue("coingecko", "CoinGecko", _spot_coingecko),
    SpotVenue("defillama", "DefiLlama", _spot_defillama),
)

OHLCV_VENUES: tuple[OhlcvVenue, ...] = (
    OhlcvVenue("binance", "Binance Global", _ohlcv_binance),
    OhlcvVenue("coinbase", "Coinbase Exchange", _ohlcv_coinbase),
    OhlcvVenue("kraken", "Kraken", _ohlcv_kraken),
    OhlcvVenue("okx", "OKX", _ohlcv_okx),
    OhlcvVenue("bybit", "Bybit", _ohlcv_bybit),
    OhlcvVenue("binance_us", "Binance US", _ohlcv_binance_us),
    OhlcvVenue("mexc", "MEXC", _ohlcv_mexc),
    OhlcvVenue("gate", "Gate.io", _ohlcv_gate),
)

DEPTH_VENUES: tuple[DepthVenue, ...] = (
    DepthVenue("binance", "Binance Global", _depth_binance),
    DepthVenue("coinbase", "Coinbase Exchange", _depth_coinbase),
    DepthVenue("kraken", "Kraken", _depth_kraken),
    DepthVenue("okx", "OKX", _depth_okx),
    DepthVenue("bybit", "Bybit", _depth_bybit),
    DepthVenue("binance_us", "Binance US", _depth_binance_us),
    DepthVenue("mexc", "MEXC", _depth_mexc),
    DepthVenue("gate", "Gate.io", _depth_gate),
)

FUNDING_VENUES: tuple[FundingVenue, ...] = (
    FundingVenue("binance", "Binance Futures", _funding_binance),
    FundingVenue("bybit", "Bybit", _funding_bybit),
    FundingVenue("okx", "OKX", _funding_okx),
    FundingVenue("gate", "Gate.io", _funding_gate),
    FundingVenue("mexc", "MEXC", _funding_mexc),
)

OPEN_INTEREST_VENUES: tuple[OpenInterestVenue, ...] = (
    OpenInterestVenue("binance", "Binance Futures", _open_interest_binance),
    OpenInterestVenue("bybit", "Bybit", _open_interest_bybit),
    OpenInterestVenue("okx", "OKX", _open_interest_okx),
)

OPEN_INTEREST_CHANGE_VENUES: tuple[OpenInterestChangeVenue, ...] = (
    OpenInterestChangeVenue("binance", "Binance Futures", _open_interest_change_binance),
    OpenInterestChangeVenue("bybit", "Bybit", _open_interest_change_bybit),
)

HISTORY_VENUES: tuple[HistoricalVenue, ...] = (
    HistoricalVenue("defillama", "DefiLlama", _history_defillama),
    HistoricalVenue("cryptocompare", "CryptoCompare", _history_cryptocompare),
    HistoricalVenue("pyth", "Pyth Network", _history_pyth),
    HistoricalVenue("bybit", "Bybit", _history_bybit),
    HistoricalVenue("okx", "OKX", _history_okx),
    HistoricalVenue("binance", "Binance Global", _history_binance),
    HistoricalVenue("coinbase", "Coinbase Exchange", _history_coinbase),
    HistoricalVenue("kraken", "Kraken", _history_kraken),
    HistoricalVenue("mexc", "MEXC", _history_mexc),
    HistoricalVenue("gate", "Gate.io", _history_gate),
    HistoricalVenue("binance_us", "Binance US", _history_binance_us),
    HistoricalVenue("bitstamp", "Bitstamp", _history_bitstamp),
    HistoricalVenue("coingecko", "CoinGecko", _history_coingecko),
)


# ---------------------------------------------------------------------------
# Generic race helpers
# ---------------------------------------------------------------------------


async def _race_first[T](
    named: Sequence[tuple[str, Callable[[], Coroutine[Any, Any, T | None]]]],
    *,
    data_type: str,
    budget_seconds: float,
) -> tuple[str, T] | None:
    """Return the first truthy result, cancelling every slower venue."""

    if not named:
        return None

    started = time.monotonic()
    deadline = started + max(0.1, budget_seconds)
    tasks: dict[asyncio.Task[T | None], str] = {
        asyncio.create_task(factory()): label for label, factory in named
    }
    pending: set[asyncio.Task[T | None]] = set(tasks)

    try:
        while pending:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            done, pending = await asyncio.wait(
                pending,
                timeout=remaining,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                break
            for task in done:
                label = tasks[task]
                try:
                    value = task.result()
                except Exception as exc:
                    logger.warning(
                        "Crypto venue fetcher raised an exception",
                        data_type=data_type,
                        venue=label,
                        error=type(exc).__name__,
                        error_detail=str(exc) or None,
                    )
                    continue
                if value:
                    logger.info(
                        "Crypto market data venue selected",
                        data_type=data_type,
                        venue=label,
                        elapsed_ms=round((time.monotonic() - started) * 1000.0, 1),
                    )
                    return label, value

        logger.warning(
            "No crypto market data venue returned usable data",
            data_type=data_type,
            venues=[label for label, _ in named],
            budget_seconds=budget_seconds,
            elapsed_ms=round((time.monotonic() - started) * 1000.0, 1),
        )
        return None
    finally:
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)


# ---------------------------------------------------------------------------
# Public mesh
# ---------------------------------------------------------------------------


async def fetch_spot_snapshot(
    client: httpx.AsyncClient,
    symbol: str,
    *,
    budget_seconds: float = 8.0,
    settle_seconds: float = 2.0,
    venues: Sequence[SpotVenue] = SPOT_VENUES,
) -> SpotSnapshot | None:
    """Collect several spot prices and return a robust cross-venue median."""

    if not venues:
        return None

    started = time.monotonic()
    deadline = started + max(0.1, budget_seconds)
    tasks: dict[asyncio.Task[float | None], str] = {
        asyncio.create_task(venue.fetch(client, symbol)): venue.label
        for venue in venues
    }
    pending: set[asyncio.Task[float | None]] = set(tasks)
    quotes: list[SpotQuote] = []
    first_success_at: float | None = None

    try:
        while pending:
            now = time.monotonic()
            remaining = deadline - now
            if first_success_at is not None:
                remaining = min(
                    remaining,
                    first_success_at + settle_seconds - now,
                )
            if remaining <= 0:
                break
            done, pending = await asyncio.wait(
                pending,
                timeout=remaining,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                break
            observed_at = datetime.now(UTC)
            for task in done:
                label = tasks[task]
                try:
                    price = task.result()
                except Exception as exc:
                    logger.warning(
                        "Crypto spot venue raised an exception",
                        venue=label,
                        error=type(exc).__name__,
                        error_detail=str(exc) or None,
                    )
                    continue
                parsed = _to_positive_float(price)
                if parsed is None:
                    continue
                quotes.append(
                    SpotQuote(price=parsed, venue=label, observed_at=observed_at)
                )
            if quotes and first_success_at is None:
                first_success_at = time.monotonic()
            if (
                first_success_at is not None
                and time.monotonic() - first_success_at >= settle_seconds
            ):
                break
    finally:
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    if not quotes:
        logger.warning(
            "No crypto spot venue returned a usable price",
            symbol=symbol,
            venues=[venue.label for venue in venues],
            elapsed_ms=round((time.monotonic() - started) * 1000.0, 1),
        )
        return None

    if len(quotes) >= 3:
        initial_median = statistics.median(quote.price for quote in quotes)
        kept = [
            quote
            for quote in quotes
            if abs(quote.price - initial_median) / initial_median
            <= _MAX_SPOT_OUTLIER_DEVIATION
        ]
        for quote in quotes:
            if quote not in kept:
                logger.warning(
                    "Crypto spot quote rejected as cross-venue outlier",
                    venue=quote.venue,
                    price=quote.price,
                    median=initial_median,
                    deviation_pct=round(
                        (quote.price - initial_median) / initial_median * 100.0,
                        3,
                    ),
                )
        if len(kept) >= 2:
            quotes = kept

    prices = [quote.price for quote in quotes]
    median = statistics.median(prices)
    dispersion_bps = (
        (max(prices) - min(prices)) / median * 10_000.0 if len(prices) > 1 else 0.0
    )
    quotes.sort(key=lambda quote: quote.price)
    if len(quotes) == 1:
        source = quotes[0].venue
    else:
        source = f"Cross-venue median ({len(quotes)} venues)"

    logger.info(
        "Crypto spot consensus assembled",
        symbol=symbol,
        median=median,
        source=source,
        dispersion_bps=round(dispersion_bps, 2),
        venues=[quote.venue for quote in quotes],
        elapsed_ms=round((time.monotonic() - started) * 1000.0, 1),
    )
    return SpotSnapshot(
        price=median,
        source=source,
        quotes=tuple(quotes),
        dispersion_bps=dispersion_bps,
    )


async def fetch_ohlcv_snapshot(
    client: httpx.AsyncClient,
    symbol: str,
    interval: str,
    limit: int,
    *,
    min_candles: int = 24,
    budget_seconds: float = 8.0,
    venues: Sequence[OhlcvVenue] = OHLCV_VENUES,
) -> OhlcvSnapshot | None:
    """Return the first venue that supplies a sufficiently long candle series."""

    named = [
        (
            venue.label,
            partial(venue.fetch, client, symbol, interval, limit),
        )
        for venue in venues
    ]
    result = await _race_first(
        named,
        data_type=f"ohlcv_{interval}",
        budget_seconds=budget_seconds,
    )
    if result is None:
        logger.warning(
            "No venue returned a usable OHLCV series",
            symbol=symbol,
            interval=interval,
            min_candles=min_candles,
        )
        return None
    label, candles = result
    candles = _clean_candles(
        candles,
        interval_seconds=_interval_seconds(interval),
        min_count=min_candles,
        limit=limit,
    )
    if not candles:
        logger.warning(
            "Selected OHLCV series did not survive normalization",
            symbol=symbol,
            interval=interval,
            venue=label,
        )
        return None
    logger.info(
        "Crypto OHLCV series selected",
        symbol=symbol,
        interval=interval,
        venue=label,
        candle_count=len(candles),
    )
    return OhlcvSnapshot(candles=list(candles), venue=label, interval=interval)


async def fetch_order_book_snapshot(
    client: httpx.AsyncClient,
    symbol: str,
    limit: int = 20,
    *,
    reference_price: float | None = None,
    budget_seconds: float = 8.0,
    venues: Sequence[DepthVenue] = DEPTH_VENUES,
) -> BookSnapshot | None:
    """Return the first sane order book, optionally rejecting a stale mid."""

    named = [
        (venue.label, partial(venue.fetch, client, symbol, limit))
        for venue in venues
    ]
    result = await _race_first(
        named,
        data_type="order_book",
        budget_seconds=budget_seconds,
    )
    if result is None:
        logger.warning("No venue returned a usable order book", symbol=symbol)
        return None

    label, book = result
    bids, asks = book
    mid = (bids[0][0] + asks[0][0]) / 2.0
    if reference_price is not None and reference_price > 0:
        deviation = abs(mid - reference_price) / reference_price
        if deviation > _MAX_SPOT_OUTLIER_DEVIATION:
            logger.warning(
                "Crypto order book rejected because its midpoint diverged from spot",
                symbol=symbol,
                venue=label,
                mid=mid,
                reference_price=reference_price,
                deviation_pct=round(deviation * 100.0, 3),
            )
            return None

    logger.info(
        "Crypto order book selected",
        symbol=symbol,
        venue=label,
        bid_levels=len(bids),
        ask_levels=len(asks),
        spread_bps=round((asks[0][0] - bids[0][0]) / mid * 10_000.0, 2) if mid else 0.0,
    )
    return BookSnapshot(bids=bids, asks=asks, venue=label)


async def fetch_derivatives_snapshot(
    client: httpx.AsyncClient,
    symbol: str,
    *,
    budget_seconds: float = 8.0,
) -> DerivativesSnapshot | None:
    """Fetch funding, open interest, and 24h OI change independently."""

    funding_result, oi_result, change_result = await asyncio.gather(
        _race_first(
            [
                (venue.label, partial(venue.fetch, client, symbol))
                for venue in FUNDING_VENUES
            ],
            data_type="funding_rate",
            budget_seconds=budget_seconds,
        ),
        _race_first(
            [
                (venue.label, partial(venue.fetch, client, symbol))
                for venue in OPEN_INTEREST_VENUES
            ],
            data_type="open_interest",
            budget_seconds=budget_seconds,
        ),
        _race_first(
            [
                (venue.label, partial(venue.fetch, client, symbol))
                for venue in OPEN_INTEREST_CHANGE_VENUES
            ],
            data_type="open_interest_change",
            budget_seconds=budget_seconds,
        ),
    )

    if funding_result is None and oi_result is None and change_result is None:
        return None

    funding_source, funding_rate = funding_result or (None, None)
    oi_source, open_interest = oi_result or (None, None)
    change_source, oi_change = change_result or (None, None)

    logger.info(
        "Crypto derivatives snapshot assembled",
        symbol=symbol,
        funding_rate_pct=funding_rate,
        funding_source=funding_source,
        open_interest_base=open_interest,
        open_interest_source=oi_source,
        open_interest_change_pct=oi_change,
        open_interest_change_source=change_source,
    )
    return DerivativesSnapshot(
        funding_rate_pct=funding_rate,
        funding_source=funding_source,
        open_interest_base=open_interest,
        open_interest_source=oi_source,
        open_interest_change_pct=oi_change,
        open_interest_change_source=change_source,
    )


async def fetch_fear_and_greed(
    client: httpx.AsyncClient,
) -> FearGreedSnapshot | None:
    data = await _request_json(
        client,
        _FEAR_GREED,
        params={"limit": 1},
        venue="Alternative.me",
        data_type="fear_greed",
    )
    if not isinstance(data, dict):
        return None
    rows = data.get("data")
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
        return None
    raw_value = rows[0].get("value")
    if not isinstance(raw_value, (int, float, str)):
        return None
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        return None
    if value < 0 or value > 100:
        return None
    classification = str(rows[0].get("value_classification") or "Neutral")
    return FearGreedSnapshot(
        value=value,
        classification=classification,
        source="Alternative.me Fear & Greed",
    )


async def fetch_historical_spot(
    client: httpx.AsyncClient,
    symbol: str,
    target_time: datetime,
    *,
    budget_seconds: float = 8.0,
    venues: Sequence[HistoricalVenue] = HISTORY_VENUES,
) -> HistoricalQuote | None:
    """Return a timestamp-validated spot price near a historical instant.

    A historical settlement must never silently substitute a much-later live
    price.  Every candidate is therefore rejected unless its observed candle
    close or data point is within ``_HISTORY_TOLERANCE_MS`` of ``target_time``.
    """

    if target_time.tzinfo is None:
        target_time = target_time.replace(tzinfo=UTC)
    target_ms = int(target_time.astimezone(UTC).timestamp() * 1000)
    if target_ms > int(datetime.now(UTC).timestamp() * 1000):
        logger.warning(
            "Historical spot requested for a future instant",
            symbol=symbol,
            target_time=target_time.isoformat(),
        )
        return None

    named = [
        (
            venue.label,
            partial(venue.fetch, client, symbol, target_ms),
        )
        for venue in venues
    ]
    result = await _race_first(
        named,
        data_type="historical_spot",
        budget_seconds=budget_seconds,
    )
    if result is None:
        logger.warning(
            "No venue returned a timestamp-validated historical spot",
            symbol=symbol,
            target_time=target_time.isoformat(),
            tolerance_seconds=_HISTORY_TOLERANCE_MS // 1000,
        )
        return None

    label, observation = result
    observed_at = datetime.fromtimestamp(observation.observed_at_ms / 1000.0, tz=UTC)
    logger.info(
        "Historical crypto spot selected",
        symbol=symbol,
        venue=label,
        price=observation.price,
        observed_at=observed_at.isoformat(),
        target_time=target_time.isoformat(),
        distance_seconds=round(abs((observation.observed_at_ms - target_ms) / 1000.0), 1),
    )
    return HistoricalQuote(
        price=observation.price,
        observed_at=observed_at,
        venue=label,
    )
