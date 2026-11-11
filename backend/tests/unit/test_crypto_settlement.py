from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from prediction_ai.specialists.crypto.settlement import (
    determine_binary_outcome,
    fetch_crypto_settlement_price,
)

_BLOCKED_HOSTS = {
    "api.binance.com",
    "api.binance.us",
    "www.okx.com",
    "api.bybit.com",
    "api.coinbase.com",
    "api.kraken.com",
    "www.bitstamp.net",
    "api.coingecko.com",
    "api.coinpaprika.com",
    "api.gateio.ws",
}


def _restricted_network(*, live_mexc_price: str | None = None) -> httpx.MockTransport:
    """Simulate a network where major venues are geo-blocked but MEXC is reachable."""

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host == "api.mexc.com":
            if request.url.path == "/api/v3/ticker/price":
                if live_mexc_price is None:
                    raise httpx.ConnectError("blocked", request=request)
                return httpx.Response(
                    200,
                    json={"symbol": "BTCUSDT", "price": live_mexc_price},
                )
            if request.url.path == "/api/v3/klines":
                target_ms = int(request.url.params["startTime"]) + 120_000
                return httpx.Response(
                    200,
                    json=[
                        [
                            target_ms - 60_000,
                            "64000",
                            "64500",
                            "63500",
                            "64000.25",
                            "1.0",
                            target_ms,
                            "0",
                            0,
                            "0",
                            "0",
                            "0",
                        ]
                    ],
                )
        if host in _BLOCKED_HOSTS:
            raise httpx.ConnectError("blocked", request=request)
        return httpx.Response(451, json={"error": "restricted"})

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_live_settlement_uses_reachable_fallback_venue() -> None:
    """Binance/Coinbase being blocked must not abort auto-settlement."""
    transport = _restricted_network(live_mexc_price="65000.5")

    async with httpx.AsyncClient(transport=transport, timeout=2.0) as client:
        price = await fetch_crypto_settlement_price("BTC", client=client)

    assert price == 65000.5


@pytest.mark.asyncio
async def test_historical_settlement_uses_reachable_kline_venue() -> None:
    """Expired markets must resolve from a historical candle, not the live price."""
    transport = _restricted_network(live_mexc_price="99999.0")
    target_time = datetime.now(UTC) - timedelta(minutes=7)

    async with httpx.AsyncClient(transport=transport, timeout=2.0) as client:
        price = await fetch_crypto_settlement_price(
            "BTC",
            target_time=target_time,
            client=client,
        )

    assert price == 64000.25


@pytest.mark.asyncio
async def test_settlement_returns_none_when_every_venue_is_blocked() -> None:
    """The caller turns a None result into an explicit 502 rather than faking a price."""
    transport = _restricted_network(live_mexc_price=None)

    async with httpx.AsyncClient(transport=transport, timeout=2.0) as client:
        price = await fetch_crypto_settlement_price("BTC", client=client)

    assert price is None


@pytest.mark.asyncio
async def test_historical_settlement_uses_defillama() -> None:
    """When CEX venues are blocked, DefiLlama historical endpoint resolves."""
    target_time = datetime.now(UTC) - timedelta(hours=2)
    target_sec = int(target_time.timestamp())

    def handler(request: httpx.Request) -> httpx.Response:
        if "coins.llama.fi" in str(request.url):
            return httpx.Response(
                200,
                json={
                    "coins": {
                        "coingecko:bitcoin": {
                            "price": 73500.5,
                            "symbol": "BTC",
                            "timestamp": target_sec,
                            "confidence": 0.99,
                        }
                    }
                },
            )
        return httpx.Response(451, json={"error": "blocked"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=2.0) as client:
        price = await fetch_crypto_settlement_price("BTC", target_time=target_time, client=client)

    assert price == 73500.5


@pytest.mark.asyncio
async def test_historical_settlement_uses_cryptocompare() -> None:
    """CryptoCompare minute candles resolve when other venues fail."""
    target_time = datetime.now(UTC) - timedelta(hours=3)
    target_sec = int(target_time.timestamp())

    def handler(request: httpx.Request) -> httpx.Response:
        if "cryptocompare.com" in str(request.url):
            return httpx.Response(
                200,
                json={
                    "Response": "Success",
                    "Data": {
                        "Data": [
                            {
                                "time": target_sec,
                                "open": 72000.0,
                                "high": 72100.0,
                                "low": 71950.0,
                                "close": 72050.25,
                                "volumeto": 100.0,
                            }
                        ]
                    },
                },
            )
        return httpx.Response(451, json={"error": "blocked"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=2.0) as client:
        price = await fetch_crypto_settlement_price("BTC", target_time=target_time, client=client)

    assert price == 72050.25


@pytest.mark.asyncio
async def test_historical_settlement_uses_pyth() -> None:
    """Pyth Network benchmarks TV history resolves when other venues fail."""
    target_time = datetime.now(UTC) - timedelta(hours=1)
    target_sec = int(target_time.timestamp())

    def handler(request: httpx.Request) -> httpx.Response:
        if "benchmarks.pyth.network" in str(request.url):
            return httpx.Response(
                200,
                json={
                    "s": "ok",
                    "t": [target_sec],
                    "c": [74120.75],
                    "o": [74100.0],
                    "h": [74150.0],
                    "l": [74090.0],
                    "v": [5.0],
                },
            )
        return httpx.Response(451, json={"error": "blocked"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=2.0) as client:
        price = await fetch_crypto_settlement_price("BTC", target_time=target_time, client=client)

    assert price == 74120.75


@pytest.mark.asyncio
async def test_historical_settlement_uses_coinbase_iso() -> None:
    """Coinbase Exchange candles resolve with ISO 8601 timestamps."""
    target_time = datetime.now(UTC) - timedelta(hours=1)
    target_sec = int(target_time.timestamp())

    def handler(request: httpx.Request) -> httpx.Response:
        if "api.exchange.coinbase.com" in str(request.url):
            # Verify ISO format was sent, not integer
            start_param = request.url.params.get("start", "")
            assert "T" in start_param and "Z" in start_param
            return httpx.Response(
                200,
                json=[
                    [target_sec - 60, 71500.0, 71600.0, 71450.0, 71550.8, 12.0],
                ],
            )
        return httpx.Response(451, json={"error": "blocked"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=2.0) as client:
        price = await fetch_crypto_settlement_price("BTC", target_time=target_time, client=client)

    assert price == 71550.8


def test_determine_binary_outcome_direction_and_labels() -> None:
    assert determine_binary_outcome(100.0, 101.0, "above", "UP", "DOWN") == "UP"
    assert determine_binary_outcome(100.0, 99.0, "above", "UP", "DOWN") == "DOWN"
    assert determine_binary_outcome(100.0, 99.0, "below", "UP", "DOWN") == "UP"
    assert determine_binary_outcome(100.0, 100.0, "below", "YES", "NO") == "NO"
