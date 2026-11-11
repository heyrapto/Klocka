"""Crypto market intelligence: mathematical probability engine + real data fetching.

This module computes a calibrated P(YES) for binary crypto price-bracket markets
using log-normal pricing theory, real OHLCV data, order-book microstructure,
funding rates, and open interest. It never abstains due to data scarcity —
the log-normal model always provides a base probability when spot + vol + time
are available, which is virtually always.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

# ---------------------------------------------------------------------------
# Normal CDF — Abramowitz & Stegun approximation (error < 7.5e-8)
# ---------------------------------------------------------------------------

def _norm_cdf(x: float) -> float:
    """Standard normal CDF via polynomial approximation."""
    t = 1.0 / (1.0 + 0.2316419 * abs(x))
    coeffs = (0.319381530, -0.356563782, 1.781477937, -1.821255978, 1.330274429)
    poly = sum(c * t ** (i + 1) for i, c in enumerate(coeffs))
    cdf = 1.0 - (1.0 / math.sqrt(2 * math.pi)) * math.exp(-(x ** 2) / 2) * poly
    return cdf if x >= 0 else 1.0 - cdf


# ---------------------------------------------------------------------------
# Log-normal probability — the mathematical core
# ---------------------------------------------------------------------------

def prob_above_strike(
    spot: float,
    strike: float,
    vol_annual: float,
    seconds_to_expiry: float,
) -> float:
    """
    Compute P(spot price > strike at expiry) under the log-normal model.

    Uses the digital option formula (Black-Scholes with zero risk-free rate):
        d2 = [ln(S/K) - 0.5·σ²·T] / (σ·√T)
        P(YES) = Φ(d2)

    Args:
        spot: current mid-market price (USD)
        strike: target price (USD)
        vol_annual: annualized realized volatility (e.g. 0.65 for 65%)
        seconds_to_expiry: seconds until market resolution

    Returns:
        float in [0.001, 0.999]
    """
    if spot <= 0 or strike <= 0 or vol_annual <= 0 or seconds_to_expiry <= 0:
        # Degenerate: return distance-based estimate
        ratio = spot / max(strike, 1e-9)
        return max(0.05, min(0.95, 0.5 + 0.5 * math.tanh(math.log(ratio) * 2)))

    T = seconds_to_expiry / (365 * 24 * 3600)  # convert to years
    sigma_sqrt_T = vol_annual * math.sqrt(T)

    if sigma_sqrt_T < 1e-9:
        # Essentially at expiry — deterministic based on current price
        return 0.98 if spot > strike else 0.02

    d2 = (math.log(spot / strike) - 0.5 * vol_annual ** 2 * T) / sigma_sqrt_T
    raw = _norm_cdf(d2)
    return max(0.001, min(0.999, raw))


def prob_below_strike(
    spot: float,
    strike: float,
    vol_annual: float,
    seconds_to_expiry: float,
) -> float:
    """P(price < strike at expiry) = 1 - P(price > strike)."""
    return 1.0 - prob_above_strike(spot, strike, vol_annual, seconds_to_expiry)


# ---------------------------------------------------------------------------
# Volatility calculation from OHLCV data
# ---------------------------------------------------------------------------

def realized_vol_annualized(closes: list[float], interval_minutes: int) -> float:
    """
    Calculate annualized realized volatility from OHLCV close prices.

    Args:
        closes: list of close prices in chronological order (most recent last)
        interval_minutes: candle interval (e.g. 15 for 15m candles)

    Returns:
        Annualized volatility as a decimal (0.65 = 65% annual vol)
        Returns 0.80 (conservative 80% annual vol) if fewer than 3 candles.
    """
    if len(closes) < 3:
        return 0.80  # conservative fallback — better to overestimate vol than under

    log_returns = [
        math.log(closes[i] / closes[i - 1])
        for i in range(1, len(closes))
        if closes[i - 1] > 0 and closes[i] > 0
    ]

    if len(log_returns) < 2:
        return 0.80

    n = len(log_returns)
    mean = sum(log_returns) / n
    variance = sum((r - mean) ** 2 for r in log_returns) / (n - 1)
    per_period_vol = math.sqrt(variance)

    # Annualize: periods per year = (60 / interval_minutes) * 24 * 365
    periods_per_year = (60 / interval_minutes) * 24 * 365
    annual_vol = per_period_vol * math.sqrt(periods_per_year)

    # Clamp to sensible range: 10% – 400% annualized
    return max(0.10, min(4.00, annual_vol))


def ewma_vol(closes: list[float], interval_minutes: int, lam: float = 0.94) -> float:
    """
    Exponentially weighted moving average volatility (EWMA/RiskMetrics).
    More responsive to recent moves than simple realized vol.
    Lambda = 0.94 is the RiskMetrics daily parameter; for intraday use 0.97.
    """
    if len(closes) < 3:
        return 0.80

    log_returns = [
        math.log(closes[i] / closes[i - 1])
        for i in range(1, len(closes))
        if closes[i - 1] > 0 and closes[i] > 0
    ]

    if len(log_returns) < 2:
        return 0.80

    # Initialize with simple variance
    mean = sum(log_returns) / len(log_returns)
    variance = sum((r - mean) ** 2 for r in log_returns) / (len(log_returns) - 1)

    # Apply EWMA
    for r in log_returns:
        variance = lam * variance + (1 - lam) * r ** 2

    per_period_vol = math.sqrt(variance)
    periods_per_year = (60 / interval_minutes) * 24 * 365
    return max(0.10, min(4.00, per_period_vol * math.sqrt(periods_per_year)))


# ---------------------------------------------------------------------------
# Technical indicators (computed from raw OHLCV)
# ---------------------------------------------------------------------------

def rsi(closes: list[float], period: int = 14) -> float | None:
    """Calculate RSI. Returns None if insufficient data."""
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, period + 1):
        delta = closes[-period + i] - closes[-period + i - 1]
        (gains if delta > 0 else losses).append(abs(delta))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1 + rs))


def ema(values: list[float], period: int) -> float | None:
    """Exponential moving average of the last `period` values."""
    if len(values) < period:
        return None
    k = 2.0 / (period + 1)
    result = sum(values[:period]) / period
    for v in values[period:]:
        result = v * k + result * (1 - k)
    return result


def vwap(highs: list[float], lows: list[float], closes: list[float], volumes: list[float]) -> float | None:
    """Volume-weighted average price."""
    if not (highs and lows and closes and volumes):
        return None
    typical_prices = [
        (high + low + close) / 3
        for high, low, close in zip(highs, lows, closes, strict=False)
    ]
    total_vol = sum(volumes)
    if total_vol <= 0:
        return None
    return (
        sum(tp * volume for tp, volume in zip(typical_prices, volumes, strict=False))
        / total_vol
    )


def bollinger_position(closes: list[float], period: int = 20, std_mult: float = 2.0) -> float | None:
    """
    Returns where current price sits within Bollinger Bands.
    0.0 = at lower band, 0.5 = at middle (SMA), 1.0 = at upper band.
    Values > 1.0 or < 0.0 indicate price outside the bands.
    """
    if len(closes) < period:
        return None
    window = closes[-period:]
    sma = sum(window) / period
    std = statistics.stdev(window)
    if std == 0:
        return 0.5
    upper = sma + std_mult * std
    lower = sma - std_mult * std
    current = closes[-1]
    band_width = upper - lower
    if band_width == 0:
        return 0.5
    return (current - lower) / band_width


# ---------------------------------------------------------------------------
# Order book microstructure
# ---------------------------------------------------------------------------

@dataclass
class OrderBookSignals:
    depth_imbalance: float          # (bid_depth - ask_depth) / (bid_depth + ask_depth); +1 = all bids
    spread_bps: float               # bid-ask spread in basis points
    bid_wall_distance_pct: float    # distance to nearest large bid wall (%)
    ask_wall_distance_pct: float    # distance to nearest large ask wall (%)
    pressure_signal: str            # "bullish", "bearish", "neutral"


def analyze_order_book(
    bids: list[tuple[float, float]],  # [(price, qty), ...]  sorted descending
    asks: list[tuple[float, float]],  # [(price, qty), ...]  sorted ascending
    spot: float,
    top_levels: int = 10,
) -> OrderBookSignals:
    """Analyze top-of-book depth and imbalance."""
    if not bids or not asks:
        return OrderBookSignals(0.0, 0.0, 0.0, 0.0, "neutral")

    bid_depth = sum(p * q for p, q in bids[:top_levels])
    ask_depth = sum(p * q for p, q in asks[:top_levels])
    total = bid_depth + ask_depth
    imbalance = (bid_depth - ask_depth) / total if total > 0 else 0.0

    best_bid = bids[0][0] if bids else spot
    best_ask = asks[0][0] if asks else spot
    mid = (best_bid + best_ask) / 2
    spread_bps = ((best_ask - best_bid) / mid * 10000) if mid > 0 else 0.0

    # Find largest bid/ask walls (concentrations of liquidity)
    bid_wall = max(bids[:20], key=lambda x: x[1], default=(spot, 0))
    ask_wall = min(asks[:20], key=lambda x: x[1], default=(spot, 0))
    # Actually want LARGEST qty for wall:
    bid_wall = max(bids[:20], key=lambda x: x[1]) if bids else (spot, 0)
    ask_wall = max(asks[:20], key=lambda x: x[1]) if asks else (spot, 0)

    bid_wall_pct = abs(spot - bid_wall[0]) / spot * 100 if spot > 0 else 0.0
    ask_wall_pct = abs(ask_wall[0] - spot) / spot * 100 if spot > 0 else 0.0

    if imbalance > 0.15:
        pressure = "bullish"
    elif imbalance < -0.15:
        pressure = "bearish"
    else:
        pressure = "neutral"

    return OrderBookSignals(imbalance, spread_bps, bid_wall_pct, ask_wall_pct, pressure)


# ---------------------------------------------------------------------------
# Funding rate interpretation
# ---------------------------------------------------------------------------

def interpret_funding_rate(rate_pct: float) -> dict[str, Any]:
    """
    Interpret a perpetual futures funding rate.

    Args:
        rate_pct: funding rate as percentage per 8h (e.g. 0.01 = 0.01%)

    Returns:
        dict with signal, bias, squeeze_risk, description
    """
    if rate_pct > 0.10:
        return {
            "signal": "extremely_crowded_longs",
            "bias": "bearish",
            "squeeze_risk": "high_short_squeeze_if_longs_liquidated",
            "directional_adjustment": -0.05,  # subtract from P(YES)
            "description": f"Funding +{rate_pct:.3f}%/8h: longs heavily overloaded; mean-reversion risk elevated",
        }
    if rate_pct > 0.03:
        return {
            "signal": "crowded_longs",
            "bias": "slightly_bearish",
            "squeeze_risk": "moderate",
            "directional_adjustment": -0.02,
            "description": f"Funding +{rate_pct:.3f}%/8h: longs slightly crowded; mild headwind",
        }
    if rate_pct < -0.05:
        return {
            "signal": "extremely_crowded_shorts",
            "bias": "bullish",
            "squeeze_risk": "high_long_squeeze_on_shorts",
            "directional_adjustment": +0.05,
            "description": f"Funding {rate_pct:.3f}%/8h: shorts heavily overloaded; upward squeeze risk elevated",
        }
    if rate_pct < -0.01:
        return {
            "signal": "crowded_shorts",
            "bias": "slightly_bullish",
            "squeeze_risk": "moderate",
            "directional_adjustment": +0.02,
            "description": f"Funding {rate_pct:.3f}%/8h: shorts slightly crowded; mild tailwind",
        }
    return {
        "signal": "balanced",
        "bias": "neutral",
        "squeeze_risk": "low",
        "directional_adjustment": 0.0,
        "description": f"Funding {rate_pct:.3f}%/8h: balanced market positioning",
    }


# ---------------------------------------------------------------------------
# Open interest signal
# ---------------------------------------------------------------------------

def interpret_oi_change(
    price_change_pct: float,
    oi_change_pct: float,
) -> dict[str, Any]:
    """
    OI + price co-movement interpretation.

    Four quadrants:
    - Price ↑, OI ↑ → new longs entering → bullish confirmation
    - Price ↑, OI ↓ → short covering → weaker signal, potential reversal
    - Price ↓, OI ↑ → new shorts entering → bearish confirmation
    - Price ↓, OI ↓ → long liquidation → potential exhaustion/bounce
    """
    if price_change_pct > 0.2 and oi_change_pct > 0.5:
        signal = "bullish_confirmation"
        adj = +0.03
    elif price_change_pct > 0.2 and oi_change_pct < -0.5:
        signal = "short_cover_rally_weak"
        adj = +0.01
    elif price_change_pct < -0.2 and oi_change_pct > 0.5:
        signal = "bearish_confirmation"
        adj = -0.03
    elif price_change_pct < -0.2 and oi_change_pct < -0.5:
        signal = "long_liquidation_exhaustion"
        adj = +0.02  # potential bounce
    else:
        signal = "neutral"
        adj = 0.0

    return {
        "signal": signal,
        "price_change_pct": price_change_pct,
        "oi_change_pct": oi_change_pct,
        "directional_adjustment": adj,
    }


# ---------------------------------------------------------------------------
# Combined feature extraction — the main public interface
# ---------------------------------------------------------------------------

@dataclass
class CryptoMarketFeatures:
    """All quantitative features for a crypto price-bracket market."""

    # Identification
    symbol: str
    spot_price: float
    strike_price: float
    seconds_to_expiry: float
    horizon_label: str  # e.g. "15m", "1h", "4h", "24h+"

    # Mathematical base probability
    vol_15m_annual: float
    vol_1h_annual: float
    vol_24h_annual: float
    primary_vol_annual: float          # the vol used for the main calc
    base_prob_yes: float               # log-normal P(price > strike)

    # Technical signals
    rsi_15m: float | None = None
    rsi_1h: float | None = None
    ema9_vs_ema21: str = "unavailable"  # "bullish", "bearish", "neutral"
    bollinger_position: float | None = None
    vwap_position: str = "unavailable"  # "above_vwap", "below_vwap"
    price_vs_24h_high_pct: float = 0.0
    price_vs_24h_low_pct: float = 0.0

    # Microstructure
    depth_imbalance: float = 0.0
    spread_bps: float = 0.0
    order_book_pressure: str = "neutral"

    # Derivatives signals
    funding_rate_pct: float = 0.0
    funding_signal: str = "balanced"
    funding_adjustment: float = 0.0
    oi_signal: str = "neutral"
    oi_adjustment: float = 0.0

    # Composite output
    adjusted_prob_yes: float = 0.0     # base_prob + all signal adjustments
    signal_confidence: str = "medium"  # "high", "medium", "low"
    key_signals: list[str] = field(default_factory=list)
    data_quality_flags: list[str] = field(default_factory=list)

    def to_context_dict(self) -> dict[str, Any]:
        """Serialise for inclusion in the Gemini forecasting prompt."""
        return {
            "symbol": self.symbol,
            "spot_price_usd": f"{self.spot_price:,.2f}",
            "strike_price_usd": f"{self.strike_price:,.2f}",
            "distance_to_strike_pct": f"{((self.strike_price - self.spot_price) / self.spot_price * 100):+.2f}%",
            "seconds_to_expiry": int(self.seconds_to_expiry),
            "horizon": self.horizon_label,
            "log_normal_prob_yes": f"{self.base_prob_yes:.4f}",
            "primary_annualized_vol": f"{self.primary_vol_annual:.2%}",
            "adjusted_prob_yes": f"{self.adjusted_prob_yes:.4f}",
            "rsi_15m": f"{self.rsi_15m:.1f}" if self.rsi_15m else "N/A",
            "rsi_1h": f"{self.rsi_1h:.1f}" if self.rsi_1h else "N/A",
            "ema_cross_signal": self.ema9_vs_ema21,
            "bollinger_position": f"{self.bollinger_position:.2f}" if self.bollinger_position else "N/A",
            "order_book_pressure": self.order_book_pressure,
            "depth_imbalance": f"{self.depth_imbalance:+.3f}",
            "funding_rate_pct_per_8h": f"{self.funding_rate_pct:.4f}%",
            "funding_signal": self.funding_signal,
            "oi_signal": self.oi_signal,
            "signal_confidence": self.signal_confidence,
            "key_signals": self.key_signals,
            "data_quality_flags": self.data_quality_flags,
            "methodology": "log_normal_pricing + microstructure + momentum",
        }


def build_features(
    symbol: str,
    spot_price: float,
    strike_price: float,
    seconds_to_expiry: float,
    ohlcv_15m: list[dict[str, float]],   # list of {o,h,l,c,v} dicts
    ohlcv_1h: list[dict[str, float]],
    bids: list[tuple[float, float]],
    asks: list[tuple[float, float]],
    funding_rate_pct: float = 0.0,
    oi_change_pct: float = 0.0,
    price_change_24h_pct: float = 0.0,
    high_24h: float = 0.0,
    low_24h: float = 0.0,
    vol_multiplier: float = 1.0,
) -> CryptoMarketFeatures:
    """
    Compute all quantitative features for a crypto price-bracket market.
    This is the main entry point called by the research specialist.
    """
    flags: list[str] = []
    key_signals: list[str] = []

    # Determine horizon label
    if seconds_to_expiry <= 1800:
        horizon = "≤30m"
    elif seconds_to_expiry <= 3600:
        horizon = "1h"
    elif seconds_to_expiry <= 14400:
        horizon = "4h"
    elif seconds_to_expiry <= 86400:
        horizon = "24h"
    else:
        horizon = "24h+"

    # --- Extract close price series ---
    closes_15m = [c["c"] for c in ohlcv_15m if c.get("c", 0) > 0]
    closes_1h = [c["c"] for c in ohlcv_1h if c.get("c", 0) > 0]

    if not closes_15m:
        flags.append("NO_15M_CANDLE_DATA")
    if not closes_1h:
        flags.append("NO_1H_CANDLE_DATA")

    # --- Volatility (use EWMA for responsiveness) ---
    vol_15m = ewma_vol(closes_15m, 15) if closes_15m else 0.80
    vol_1h = ewma_vol(closes_1h, 60) if closes_1h else 0.80
    vol_24h = realized_vol_annualized(closes_1h[-24:], 60) if len(closes_1h) >= 24 else vol_1h

    # Choose vol based on horizon and apply learned calibration multiplier
    if seconds_to_expiry <= 3600:
        primary_vol = vol_15m * vol_multiplier
    elif seconds_to_expiry <= 14400:
        primary_vol = vol_1h * vol_multiplier
    else:
        primary_vol = vol_24h * vol_multiplier

    # --- Log-normal base probability ---
    base_p = prob_above_strike(spot_price, strike_price, primary_vol, seconds_to_expiry)

    # --- Technical indicators ---
    rsi_15m_val = rsi(closes_15m, 14)
    rsi_1h_val = rsi(closes_1h, 14)

    ema9 = ema(closes_1h, 9)
    ema21 = ema(closes_1h, 21)
    if ema9 and ema21:
        ema_cross = "bullish" if ema9 > ema21 else ("bearish" if ema9 < ema21 else "neutral")
    else:
        ema_cross = "unavailable"

    boll_pos = bollinger_position(closes_1h, 20) if len(closes_1h) >= 20 else None

    highs = [c["h"] for c in ohlcv_1h if c.get("h")]
    lows  = [c["l"] for c in ohlcv_1h if c.get("l")]
    vols  = [c["v"] for c in ohlcv_1h if c.get("v")]
    vwap_val = vwap(highs, lows, closes_1h, vols) if (highs and lows and closes_1h and vols) else None
    vwap_pos = "above_vwap" if (vwap_val and spot_price > vwap_val) else ("below_vwap" if vwap_val else "unavailable")

    # 24h range position
    if high_24h > 0 and low_24h > 0 and high_24h > low_24h:
        pct_from_high = (spot_price - high_24h) / high_24h * 100
        pct_from_low  = (spot_price - low_24h) / low_24h * 100
    else:
        pct_from_high = 0.0
        pct_from_low = 0.0

    # --- Microstructure ---
    ob_signals = analyze_order_book(bids, asks, spot_price, top_levels=10)

    # --- Funding rate ---
    funding_info = interpret_funding_rate(funding_rate_pct)
    funding_adj = funding_info["directional_adjustment"]

    # --- OI signal ---
    oi_info = interpret_oi_change(price_change_24h_pct, oi_change_pct)
    oi_adj = oi_info["directional_adjustment"]

    # --- Assemble adjustments ---
    total_adj = 0.0

    # RSI adjustment (overbought/oversold)
    rsi_to_use = rsi_15m_val if seconds_to_expiry <= 3600 else rsi_1h_val
    if rsi_to_use is not None:
        if rsi_to_use > 75:
            total_adj -= 0.03
            key_signals.append(f"RSI {rsi_to_use:.0f} → overbought; slight downward pressure")
        elif rsi_to_use < 25:
            total_adj += 0.03
            key_signals.append(f"RSI {rsi_to_use:.0f} → oversold; potential bounce")
        elif rsi_to_use > 55:
            key_signals.append(f"RSI {rsi_to_use:.0f} → mild bullish momentum")
        elif rsi_to_use < 45:
            key_signals.append(f"RSI {rsi_to_use:.0f} → mild bearish momentum")

    # EMA cross
    if ema_cross == "bullish":
        total_adj += 0.02
        key_signals.append("EMA9 > EMA21: bullish momentum confirmed")
    elif ema_cross == "bearish":
        total_adj -= 0.02
        key_signals.append("EMA9 < EMA21: bearish momentum")

    # Bollinger
    if boll_pos is not None:
        if boll_pos > 0.90:
            total_adj -= 0.02
            key_signals.append(f"Near upper Bollinger band ({boll_pos:.2f}): mean-reversion risk")
        elif boll_pos < 0.10:
            total_adj += 0.02
            key_signals.append(f"Near lower Bollinger band ({boll_pos:.2f}): potential bounce")

    # Order book pressure (only for short horizon)
    if seconds_to_expiry <= 3600:
        ob_adj = ob_signals.depth_imbalance * 0.03
        total_adj += ob_adj
        if abs(ob_signals.depth_imbalance) > 0.2:
            key_signals.append(
                f"Order book imbalance {ob_signals.depth_imbalance:+.2f}: "
                f"{ob_signals.pressure_signal} short-term pressure"
            )

    # VWAP
    if vwap_pos == "above_vwap":
        total_adj += 0.01
    elif vwap_pos == "below_vwap":
        total_adj -= 0.01

    # Funding rate
    total_adj += funding_adj
    if abs(funding_adj) > 0.01:
        key_signals.append(funding_info["description"])

    # OI
    total_adj += oi_adj
    if abs(oi_adj) > 0.01:
        key_signals.append(f"OI signal: {oi_info['signal']}")

    # Clamp total adjustment to avoid overcorrection
    total_adj = max(-0.15, min(0.15, total_adj))

    adjusted_p = max(0.01, min(0.99, base_p + total_adj))

    # --- Signal confidence ---
    if len(flags) == 0 and closes_15m and closes_1h and bids and asks:
        confidence = "high"
    elif len(flags) <= 1:
        confidence = "medium"
    else:
        confidence = "low"
        flags.append("INCOMPLETE_DATA_USING_FALLBACKS")

    return CryptoMarketFeatures(
        symbol=symbol,
        spot_price=spot_price,
        strike_price=strike_price,
        seconds_to_expiry=seconds_to_expiry,
        horizon_label=horizon,
        vol_15m_annual=vol_15m,
        vol_1h_annual=vol_1h,
        vol_24h_annual=vol_24h,
        primary_vol_annual=primary_vol,
        base_prob_yes=base_p,
        rsi_15m=rsi_15m_val,
        rsi_1h=rsi_1h_val,
        ema9_vs_ema21=ema_cross,
        bollinger_position=boll_pos,
        vwap_position=vwap_pos,
        price_vs_24h_high_pct=pct_from_high,
        price_vs_24h_low_pct=pct_from_low,
        depth_imbalance=ob_signals.depth_imbalance,
        spread_bps=ob_signals.spread_bps,
        order_book_pressure=ob_signals.pressure_signal,
        funding_rate_pct=funding_rate_pct,
        funding_signal=funding_info["signal"],
        funding_adjustment=funding_adj,
        oi_signal=oi_info["signal"],
        oi_adjustment=oi_adj,
        adjusted_prob_yes=adjusted_p,
        signal_confidence=confidence,
        key_signals=key_signals[:8],  # cap for prompt brevity
        data_quality_flags=flags,
    )


# ---------------------------------------------------------------------------
# Legacy interface (backward-compatible with existing code)
# ---------------------------------------------------------------------------

def extract_btc_microstructure_features(
    prices: list[Decimal],
    bids: list[tuple[Decimal, Decimal]],
    asks: list[tuple[Decimal, Decimal]],
) -> dict[str, float]:
    """Legacy microstructure feature extractor (kept for backward compatibility)."""
    if len(prices) < 2:
        return {"return_1m": 0.0, "realized_volatility": 0.0, "depth_imbalance": 0.0}

    float_prices = [float(p) for p in prices]
    vol = realized_vol_annualized(float_prices, interval_minutes=1)

    float_bids = [(float(p), float(q)) for p, q in bids]
    float_asks = [(float(p), float(q)) for p, q in asks]
    ob = analyze_order_book(float_bids, float_asks, float_prices[-1])

    log_returns = [
        math.log(float_prices[i] / float_prices[i - 1])
        for i in range(1, len(float_prices))
        if float_prices[i - 1] > 0
    ]

    return {
        "return_latest": log_returns[-1] if log_returns else 0.0,
        "realized_volatility": vol,
        "depth_imbalance": ob.depth_imbalance,
    }
