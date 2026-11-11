from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from prediction_ai.core.config import Settings
from prediction_ai.specialists.crypto import research
from prediction_ai.specialists.crypto.venues import (
    OhlcvSnapshot,
    SpotQuote,
    SpotSnapshot,
)


def _test_settings() -> Settings:
    return Settings(ENVIRONMENT="test")


def _candle(close: float, *, age_minutes: int = 20) -> dict[str, float]:
    opened = datetime.now(UTC) - timedelta(minutes=age_minutes)
    return {
        "t": opened.timestamp() * 1000.0,
        "o": close - 50.0,
        "h": close + 100.0,
        "l": close - 100.0,
        "c": close,
        "v": 10.0,
    }


async def _no_fetch(*args: Any, **kwargs: Any) -> None:
    del args, kwargs
    return None


async def _one_multiplier(symbol: str) -> float:
    del symbol
    return 1.0


@pytest.mark.asyncio
async def test_spot_falls_back_to_15m_candle_before_screenshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def ohlcv(
        client: httpx.AsyncClient,
        symbol: str,
        interval: str,
        limit: int,
        **kwargs: Any,
    ) -> OhlcvSnapshot | None:
        del client, symbol, limit, kwargs
        if interval == "15m":
            return OhlcvSnapshot(
                candles=[_candle(70_500.0)],
                venue="Test 15m venue",
                interval="15m",
            )
        return None

    monkeypatch.setattr(research, "fetch_spot_snapshot", _no_fetch)
    monkeypatch.setattr(research, "fetch_ohlcv_snapshot", ohlcv)
    monkeypatch.setattr(research, "fetch_order_book_snapshot", _no_fetch)
    monkeypatch.setattr(research, "fetch_derivatives_snapshot", _no_fetch)
    monkeypatch.setattr(research, "fetch_fear_and_greed", _no_fetch)
    monkeypatch.setattr(research, "_load_vol_multiplier", _one_multiplier)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200))
    ) as client:
        context = await research.gather_crypto_intelligence(
            "Will BTC be above 71000 in 15 minutes?",
            underlying_symbol="BTC",
            displayed_spot_price=99_999.0,
            http_client=client,
            settings=_test_settings(),
        )

    assert context["spot_source"] == "15m candle close (Test 15m venue)"
    assert context["spot_price_usd"] == "70,500.00"
    assert "SPOT_FROM_15M_CANDLE_CLOSE" in context["data_quality_flags"]
    assert "screenshot_anchored" not in context


@pytest.mark.asyncio
async def test_spot_uses_screenshot_only_after_live_and_candle_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(research, "fetch_spot_snapshot", _no_fetch)
    monkeypatch.setattr(research, "fetch_ohlcv_snapshot", _no_fetch)
    monkeypatch.setattr(research, "fetch_order_book_snapshot", _no_fetch)
    monkeypatch.setattr(research, "fetch_derivatives_snapshot", _no_fetch)
    monkeypatch.setattr(research, "fetch_fear_and_greed", _no_fetch)
    monkeypatch.setattr(research, "_load_vol_multiplier", _one_multiplier)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200))
    ) as client:
        context = await research.gather_crypto_intelligence(
            "Will BTC be above 74000 in 15 minutes?",
            underlying_symbol="BTC",
            displayed_spot_price=73_000.0,
            http_client=client,
            settings=_test_settings(),
        )

    assert context["spot_source"] == "Screenshot Point-in-Time Spot"
    assert context["screenshot_anchored"] is True
    assert context["signal_confidence"] == "low"
    assert "ANCHORED_TO_SCREENSHOT_SPOT" in context["data_quality_flags"]
    assert 0.01 <= float(context["final_adjusted_prob_yes"]) <= 0.99


@pytest.mark.asyncio
async def test_below_market_probability_is_inverted_for_the_forecaster(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def spot(
        client: httpx.AsyncClient, symbol: str, **kwargs: Any
    ) -> SpotSnapshot:
        del client, symbol, kwargs
        observed_at = datetime.now(UTC)
        return SpotSnapshot(
            price=73_000.0,
            source="Test venue",
            quotes=(SpotQuote(price=73_000.0, venue="Test venue", observed_at=observed_at),),
            dispersion_bps=0.0,
        )

    monkeypatch.setattr(research, "fetch_spot_snapshot", spot)
    monkeypatch.setattr(research, "fetch_ohlcv_snapshot", _no_fetch)
    monkeypatch.setattr(research, "fetch_order_book_snapshot", _no_fetch)
    monkeypatch.setattr(research, "fetch_derivatives_snapshot", _no_fetch)
    monkeypatch.setattr(research, "fetch_fear_and_greed", _no_fetch)
    monkeypatch.setattr(research, "_load_vol_multiplier", _one_multiplier)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200))
    ) as client:
        context = await research.gather_crypto_intelligence(
            "Will BTC be below 74000 in 15 minutes?",
            underlying_symbol="BTC",
            http_client=client,
            settings=_test_settings(),
        )

    raw_above = float(context["adjusted_prob_yes"])
    final_below = float(context["final_adjusted_prob_yes"])
    assert raw_above < 0.5
    assert final_below > 0.5
    assert final_below == pytest.approx(1.0 - raw_above, abs=1e-4)
    assert context["direction"] == "below"


@pytest.mark.asyncio
async def test_missing_spot_returns_data_unavailable_not_a_fabricated_price(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(research, "fetch_spot_snapshot", _no_fetch)
    monkeypatch.setattr(research, "fetch_ohlcv_snapshot", _no_fetch)
    monkeypatch.setattr(research, "fetch_order_book_snapshot", _no_fetch)
    monkeypatch.setattr(research, "fetch_derivatives_snapshot", _no_fetch)
    monkeypatch.setattr(research, "fetch_fear_and_greed", _no_fetch)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200))
    ) as client:
        context = await research.gather_crypto_intelligence(
            "Will BTC be above 74000 in 15 minutes?",
            underlying_symbol="BTC",
            http_client=client,
            settings=_test_settings(),
        )

    assert context["status"] == "DATA_UNAVAILABLE"
    assert context["spot_source"] == "Unavailable"
    assert context["adjusted_prob_yes"] is None
    assert context["final_adjusted_prob_yes"] is None
    assert "SPOT_PRICE_UNAVAILABLE" in context["data_quality_flags"]


def test_build_features_har_vol_blend_and_ofi() -> None:
    from prediction_ai.specialists.crypto.features import build_features

    ohlcv_1h = [_candle(100.0, age_minutes=60 * i) for i in range(30, 0, -1)]
    ohlcv_15m = [_candle(100.0, age_minutes=15 * i) for i in range(30, 0, -1)]
    ohlcv_1m = [_candle(100.0 + (i % 3), age_minutes=i) for i in range(25, 0, -1)]

    bids = [(99.0 - i * 0.1, 10.0 + i) for i in range(10)]
    asks = [(101.0 + i * 0.1, 5.0 + i) for i in range(10)]

    features = build_features(
        symbol="BTC",
        spot_price=100.0,
        strike_price=100.0,
        seconds_to_expiry=600.0,  # 10m -> ultra short (<15m)
        ohlcv_15m=ohlcv_15m,
        ohlcv_1h=ohlcv_1h,
        ohlcv_1m=ohlcv_1m,
        bids=bids,
        asks=asks,
        vol_multiplier=1.0,
    )

    assert features.vol_1m_annual > 0.0
    assert features.primary_vol_annual > 0.0
    # For ultra short, weights are 0.60*vol_1m + 0.30*vol_15m + 0.10*vol_1h
    expected_vol = 0.60 * features.vol_1m_annual + 0.30 * features.vol_15m_annual + 0.10 * features.vol_1h_annual
    assert features.primary_vol_annual == pytest.approx(expected_vol, abs=1e-4)
    ctx = features.to_context_dict()
    assert ctx["vol_1m_annual"] != "N/A"
    assert "sigma_distance" in ctx


def test_build_features_futures_premium_adjustment() -> None:
    from prediction_ai.specialists.crypto.features import build_features

    ohlcv_1h = [_candle(100.0, age_minutes=60 * i) for i in range(30, 0, -1)]
    ohlcv_15m = [_candle(100.0, age_minutes=15 * i) for i in range(30, 0, -1)]

    # Positive premium > 10 bps (contango)
    features_contango = build_features(
        symbol="BTC",
        spot_price=100.0,
        strike_price=100.0,
        seconds_to_expiry=1800.0,
        ohlcv_15m=ohlcv_15m,
        ohlcv_1h=ohlcv_1h,
        bids=[(99.0, 1.0)],
        asks=[(101.0, 1.0)],
        futures_premium_bps=15.0,
    )
    # Negative premium < -10 bps (backwardation)
    features_backwardation = build_features(
        symbol="BTC",
        spot_price=100.0,
        strike_price=100.0,
        seconds_to_expiry=1800.0,
        ohlcv_15m=ohlcv_15m,
        ohlcv_1h=ohlcv_1h,
        bids=[(99.0, 1.0)],
        asks=[(101.0, 1.0)],
        futures_premium_bps=-15.0,
    )

    assert features_contango.futures_premium_bps == 15.0
    assert features_backwardation.futures_premium_bps == -15.0
    assert features_contango.adjusted_prob_yes > features_backwardation.adjusted_prob_yes

