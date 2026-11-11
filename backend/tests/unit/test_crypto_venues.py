from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from prediction_ai.specialists.crypto import venues


def _closed_timestamps(interval_seconds: int, count: int = 3) -> list[int]:
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    latest_open = now_ms - interval_seconds * 1000
    return [latest_open - index * interval_seconds * 1000 for index in range(count - 1, -1, -1)]


def _binance_row(open_ms: int, price: float) -> list[object]:
    return [
        open_ms,
        f"{price - 10}",
        f"{price + 20}",
        f"{price - 20}",
        f"{price}",
        "12.5",
        open_ms + 59_999,
        "0",
        1,
        "0",
        "0",
        "0",
    ]


def test_binance_style_candles_are_chronological_and_closed() -> None:
    timestamps = _closed_timestamps(900)
    raw = [_binance_row(open_ms, 100.0 + index) for index, open_ms in enumerate(timestamps)]

    candles = venues._parse_binance_klines(raw, interval="15m", min_count=3, limit=10)

    assert [int(candle["t"]) for candle in candles] == timestamps
    assert [candle["c"] for candle in candles] == [100.0, 101.0, 102.0]


def test_okx_parser_uses_row_order_and_drops_forming_candle() -> None:
    timestamps = _closed_timestamps(900)
    rows = [
        [str(open_ms), "10", "11", "9", "10.5", "1", "1", "1", "1"]
        for open_ms in timestamps
    ]
    rows.append([str(timestamps[-1] + 900_000), "10", "11", "9", "99", "1", "1", "1", "0"])
    raw = {"code": "0", "data": list(reversed(rows))}

    candles = venues._parse_okx_candles(raw, interval="15m", min_count=3, limit=10)

    assert [int(candle["t"]) for candle in candles] == timestamps
    assert [candle["c"] for candle in candles] == [10.5, 10.5, 10.5]


def test_coinbase_parser_maps_low_high_open_close_fields() -> None:
    timestamps = _closed_timestamps(900)
    raw = [
        [open_ms // 1000, "95", "115", "100", "110", "7"]
        for open_ms in reversed(timestamps)
    ]

    candles = venues._parse_coinbase_candles(raw, interval="15m", min_count=3, limit=10)

    assert [int(candle["t"] // 1000) for candle in candles] == [
        int(timestamp // 1000) for timestamp in timestamps
    ]
    assert candles[0] == {
        "t": float(timestamps[0] // 1000 * 1000),
        "o": 100.0,
        "h": 115.0,
        "l": 95.0,
        "c": 110.0,
        "v": 7.0,
    }


def test_kraken_parser_maps_ohlc_fields_and_volume() -> None:
    timestamps = _closed_timestamps(900)
    raw = {
        "error": [],
        "result": {
            "XXBTZUSD": [
                [int(open_ms / 1000), "100", "115", "95", "110", "105", "42", 7]
                for open_ms in timestamps
            ]
        },
    }

    candles = venues._parse_kraken_ohlc(raw, interval="15m", min_count=3, limit=10)

    assert [int(candle["t"] // 1000) for candle in candles] == [
        int(timestamp // 1000) for timestamp in timestamps
    ]
    assert candles[-1]["o"] == 100.0
    assert candles[-1]["c"] == 110.0
    assert candles[-1]["v"] == 42.0


def test_gate_parser_maps_quote_volume_close_high_low_open_fields() -> None:
    timestamps = _closed_timestamps(900)
    raw = [
        [int(open_ms / 1000), "999", "110", "115", "95", "100", "42", "true"]
        for open_ms in timestamps
    ]

    candles = venues._parse_gate_candles(raw, interval="15m", min_count=3, limit=10)

    assert [int(candle["t"] // 1000) for candle in candles] == [
        int(timestamp // 1000) for timestamp in timestamps
    ]
    assert candles[0]["c"] == 110.0
    assert candles[0]["h"] == 115.0
    assert candles[0]["l"] == 95.0
    assert candles[0]["o"] == 100.0
    assert candles[0]["v"] == 42.0


@pytest.mark.asyncio
async def test_spot_snapshot_uses_median_and_rejects_cross_venue_outlier() -> None:
    prices = {"Alpha": 100.0, "Beta": 100.2, "Gamma": 99.9, "Outlier": 120.0}

    def make_fetcher(label: str):
        async def fetch(client: httpx.AsyncClient, symbol: str) -> float:
            del client, symbol
            return prices[label]

        return fetch

    spot_venues = tuple(
        venues.SpotVenue(label.lower(), label, make_fetcher(label)) for label in prices
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200))) as client:
        snapshot = await venues.fetch_spot_snapshot(
            client,
            "BTC",
            budget_seconds=1.0,
            settle_seconds=0.1,
            venues=spot_venues,
        )

    assert snapshot is not None
    assert snapshot.price == pytest.approx(100.0)
    assert snapshot.source == "Cross-venue median (3 venues)"
    assert [quote.venue for quote in snapshot.quotes] == ["Gamma", "Alpha", "Beta"]
    assert snapshot.dispersion_bps == pytest.approx((100.2 - 99.9) / 100.0 * 10_000.0)


@pytest.mark.asyncio
async def test_derivatives_snapshot_preserves_source_and_value_ordering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def funding(client: httpx.AsyncClient, symbol: str) -> float:
        del client, symbol
        return 0.012

    async def open_interest(client: httpx.AsyncClient, symbol: str) -> float:
        del client, symbol
        return 45_000.0

    async def open_interest_change(client: httpx.AsyncClient, symbol: str) -> float:
        del client, symbol
        return 3.5

    monkeypatch.setattr(
        venues,
        "FUNDING_VENUES",
        (venues.FundingVenue("test", "Test Funding", funding),),
    )
    monkeypatch.setattr(
        venues,
        "OPEN_INTEREST_VENUES",
        (venues.OpenInterestVenue("test", "Test OI", open_interest),),
    )
    monkeypatch.setattr(
        venues,
        "OPEN_INTEREST_CHANGE_VENUES",
        (
            venues.OpenInterestChangeVenue(
                "test", "Test OI Change", open_interest_change
            ),
        ),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200))) as client:
        snapshot = await venues.fetch_derivatives_snapshot(
            client, "BTC", budget_seconds=1.0
        )

    assert snapshot is not None
    assert snapshot.funding_source == "Test Funding"
    assert snapshot.funding_rate_pct == pytest.approx(0.012)
    assert snapshot.open_interest_source == "Test OI"
    assert snapshot.open_interest_base == pytest.approx(45_000.0)
    assert snapshot.open_interest_change_source == "Test OI Change"
    assert snapshot.open_interest_change_pct == pytest.approx(3.5)


@pytest.mark.asyncio
async def test_fear_and_greed_uses_trailing_slash_endpoint() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "value": "73",
                        "value_classification": "Greed",
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        snapshot = await venues.fetch_fear_and_greed(client)

    assert snapshot is not None
    assert snapshot.value == 73
    assert snapshot.classification == "Greed"
    assert requests[0].url.path == "/fng/"
    assert requests[0].url.params["limit"] == "1"


@pytest.mark.asyncio
async def test_fetch_futures_premium_computes_basis() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "premiumIndex" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "symbol": "BTCUSDT",
                    "markPrice": "68500.00",
                    "indexPrice": "68400.00",
                    "lastFundingRate": "0.00010000",
                },
            )
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        snapshot = await venues.fetch_futures_premium(client, "BTC")

    assert snapshot is not None
    assert snapshot.futures_price == 68500.00
    assert snapshot.spot_price == 68400.00
    # Basis: (68500 - 68400) / 68400 * 10000 = 100 / 68400 * 10000 ≈ 14.6198 bps
    assert snapshot.premium_bps == pytest.approx(14.6198, abs=0.01)
    assert snapshot.venue == "Binance Futures"

