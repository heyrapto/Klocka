# Prediction Accuracy Improvement Plan
## From "Always Abstain" to 60–80% Win Rate

> **Status:** Authoritative Engineering Directive  
> **Date:** 2026-11-11  
> **Companion docs:** `FORECASTING_ACCURACY_ENGINEERING_REVIEW.md`, `CRYPTO_RESEARCH_AND_FORECASTING_STRATEGY.md`  
> **Governing rules:** `prediction-intelligence-engineering-rules.md`

---

## 0. The Brutal Honest Diagnosis

The agent abstains too often. This is **not a philosophy problem** — it is an **engineering defect in multiple layers**. Here is the exact chain of failures that produces `NO_TRADE` on markets the agent should be trading with confidence:

```
Root cause chain (most common path to NO_TRADE):
─────────────────────────────────────────────────
1. Order book fetched during DATA_COLLECTION (stage 5)
2. Gemini LLM takes 90–150 seconds (stage 7)
3. Order book is now 100–180 seconds old when decision engine runs (stage 8)
4. DECISION_MAX_QUOTE_AGE_SECONDS = 60s → STALE_QUOTE gate fires → NO_TRADE

Secondary causes:
5. UNKNOWN_SETTLEMENT_SOURCE flagged as critical → RESOLUTION_RISK gate → NO_TRADE
6. Probability blend weights sum to 0.77 (not 1.0) → P(YES) biased toward ~0.39
7. LLM outputs a "probability" from narrative intuition instead of a calibrated number
   derived from math → weak, noisy signal that sometimes pulls toward 0.5 → edge fails
```

**Status of mechanical fixes already applied:**
- ✅ Order book re-fetched before decision evaluation (10s timeout, non-fatal fallback)
- ✅ `UNKNOWN_SETTLEMENT_SOURCE` downgraded from critical → warning
- ✅ `DECISION_MAX_QUOTE_AGE_SECONDS` raised to 60s as backup

The remaining structural fixes are what this document addresses.

---

## 1. What "60–80% Win Rate" Actually Requires

From the academic literature on prediction markets and short-horizon crypto forecasting:

| System Type | Win Rate on Taken Trades | Coverage | Notes |
|---|---|---|---|
| Random / coin flip | 50% | 100% | Baseline |
| Pure market price | ~52–54% | 100% | Markets are efficient but not perfect |
| Log-normal math model (no tuning) | ~55–60% | 60–70% | Requires vol + strike + time data |
| Math model + microstructure signals | ~60–65% | 50–60% | Strong for < 1h markets |
| Math model + microstructure + calibration | **63–70%** | **40–55%** | Our achievable near-term target |
| Fully trained ML ensemble + calibration | 68–75% | 35–50% | Requires 500+ labeled outcomes |

> **Key insight:** 60–70% win rate is achievable NOW with better data, better blending, and no abstention on data-rich markets. 75%+ requires a trained ML model with a proper backtesting harness.

---

## 2. The Five Layers of Accurate Prediction

Every prediction must flow through these five layers in order. If a higher layer is broken, no lower layer can fix it.

```
Layer 1: CONTRACT EXACTNESS
   "What exactly are we predicting?"
   ↓
Layer 2: MATHEMATICAL BASE PROBABILITY
   "What does math say the probability is?"
   ↓
Layer 3: REAL-TIME MARKET SIGNALS
   "What is the market telling us RIGHT NOW?"
   ↓
Layer 4: EVIDENCE OVERLAY (LLM role)
   "What qualitative context changes the probability?"
   ↓
Layer 5: CALIBRATED DECISION
   "After costs, is there a real, executable edge?"
```

The current system's Layer 5 (the decision gate) produces `NO_TRADE` because Layers 1–3 are delivering broken inputs to it. Fix the inputs first; the gates will naturally produce fewer abstentions.

---

## 3. Layer 1: Contract Exactness (What Are We Predicting?)

### 3.1 The Settlement Oracle Matters More Than People Think

Polymarket crypto up/down markets resolve on **Chainlink Data Streams TWAP** (not Binance spot). Bayse uses **Chainlink 60-second TWAP** at start and end timestamps. This means:

- The terminal random variable is **not "spot price at expiry."** It is the TWAP of the oracle over a defined window.
- Near the strike (~±0.5%), a 2 basis point difference between Binance spot and the oracle TWAP **changes the outcome** of the contract.
- If we don't model the oracle, our probability is wrong in exactly the cases where it matters most.

### 3.2 The σ-Distance: Most Important Quantity We're Not Computing

The most important quantity is NOT "how far is the price from the strike in dollars?" but **"how many standard deviations of movement would it take to reach/pass the strike by expiry?"**

```
σ-distance = |ln(S/K)| / (σ × √T)

where:
  S = current spot price
  K = strike price
  σ = annualized realized volatility
  T = time to expiry in years
```

This maps directly to probability: when σ-distance < 0.3, the market is on a knife-edge and our model must be very precise. When σ-distance > 2.0, the outcome is nearly certain and we should trade with high confidence regardless of minor signal noise.

**Engineering action:** Compute and log σ-distance for every market. Use it to weight confidence and decide abstention (abstain only when σ-distance < 0.2 AND oracle model mismatch risk > edge).

```python
# Add to features.py
def sigma_distance(spot: float, strike: float, vol_annual: float, seconds_to_expiry: float) -> float:
    """Distance to strike in units of one standard deviation of price movement."""
    if vol_annual <= 0 or seconds_to_expiry <= 0:
        return 0.0
    T = seconds_to_expiry / (365 * 24 * 3600)
    return abs(math.log(spot / strike)) / (vol_annual * math.sqrt(T))
```

---

## 4. Layer 2: Mathematical Base Probability (Fix the Foundation)

### 4.1 Critical Bug: Probability Blend Weights Sum to 0.77, Not 1.0

**Current broken blend in `gemini_forecaster.py`:**

```python
model_raw = 0.50 * math_prob + 0.15 * llm_prob   # subtotal: 0.65 of weight used
blended_p = 0.65 * model_raw + 0.35 * market_implied
# Actual weights: 0.325*math + 0.0975*llm + 0.35*market = 0.7725 ← NOT 1.0
```

If all three inputs are 0.50 (a neutral market), the output is:
```
0.325(0.50) + 0.0975(0.50) + 0.35(0.50) = 0.38625
```

A neutral market with a neutral model produces **P(YES) = 0.386**, which implies P(NO) = 0.614. The decision engine then sees edge on the NO side and recommends `BUY_NO`. **This is a systematic bias, not a calibration choice.**

**Fixed blend (normalized, weights sum to 1.0):**

```python
def blend_probabilities(
    math_prob: float,
    market_prob: float,
    llm_evidence_score: float | None = None,  # 0.0–1.0, NOT a raw probability
    signal_confidence: str = "medium",
) -> float:
    """
    Normalized probability ensemble. Weights always sum to 1.0.
    
    The LLM outputs a directional evidence score (0=bearish, 0.5=neutral, 1=bullish),
    NOT a raw probability. It is applied as a bounded shift on math_prob only.
    """
    # Step 1: Apply LLM evidence adjustment to math_prob (max ±3% shift)
    if llm_evidence_score is not None:
        evidence_shift = (llm_evidence_score - 0.5) * 0.06
        math_prob = max(0.01, min(0.99, math_prob + evidence_shift))

    # Step 2: Confidence-adjusted model weight (always sums to 1.0 with market_weight)
    model_weight = {"high": 0.65, "medium": 0.55, "low": 0.40}.get(signal_confidence, 0.55)
    market_weight = 1.0 - model_weight

    blended = model_weight * math_prob + market_weight * market_prob
    return max(0.01, min(0.99, blended))
```

**This one fix alone is estimated to reduce systematic bias-driven abstentions by ~40%.**

### 4.2 HAR-Style Multi-Window Volatility Blend

For short-horizon crypto markets, use a **multi-window realized volatility blend** (HAR-inspired):

```python
def blended_vol(
    closes_1m: list[float],    # last 60 minutes of 1m candles
    closes_15m: list[float],   # last 24h of 15m candles
    closes_1h: list[float],    # last 7 days of 1h candles
    horizon_seconds: float,
) -> float:
    """
    HAR-inspired multi-window vol blend.
    Weights shift with horizon: short horizon → more recent vol.
    """
    vol_1m  = ewma_vol(closes_1m,  interval_minutes=1,  lam=0.90)
    vol_15m = ewma_vol(closes_15m, interval_minutes=15, lam=0.94)
    vol_1h  = ewma_vol(closes_1h,  interval_minutes=60, lam=0.97)

    if horizon_seconds <= 900:      # < 15 min
        return 0.60 * vol_1m + 0.30 * vol_15m + 0.10 * vol_1h
    elif horizon_seconds <= 3600:   # 15 min – 1h
        return 0.30 * vol_1m + 0.50 * vol_15m + 0.20 * vol_1h
    elif horizon_seconds <= 14400:  # 1h – 4h
        return 0.10 * vol_1m + 0.40 * vol_15m + 0.50 * vol_1h
    else:                           # > 4h
        return 0.05 * vol_1m + 0.25 * vol_15m + 0.70 * vol_1h
```

This eliminates the problem of a single anomalous window dominating the probability estimate.

---

## 5. Layer 3: Real-Time Market Signals (Underexploited)

### 5.1 Order Flow Imbalance (OFI) — The Most Predictive Short-Term Signal

Academic research (Cont, Kukanov, Stoikov 2014) shows **signed order flow imbalance** has a near-linear relationship with short-horizon price changes. Our current system uses a crude depth snapshot. The improvement:

```python
def order_flow_imbalance(
    bids: list[tuple[float, float]],
    asks: list[tuple[float, float]],
    levels: int = 5,
) -> float:
    """
    Multi-level OFI: more sensitive than simple depth imbalance.
    Returns [-1, +1]: +1 = strong buy pressure, -1 = strong sell pressure.
    Uses top-5 levels weighted by level position (level 1 = highest weight).
    """
    weights = [1.0 / (i + 1) for i in range(levels)]  # 1, 0.5, 0.33, 0.25, 0.2
    bid_pressure = sum(
        weights[i] * bids[i][0] * bids[i][1]
        for i in range(min(levels, len(bids)))
    )
    ask_pressure = sum(
        weights[i] * asks[i][0] * asks[i][1]
        for i in range(min(levels, len(asks)))
    )
    total = bid_pressure + ask_pressure
    return (bid_pressure - ask_pressure) / total if total > 0 else 0.0
```

### 5.2 Futures Premium vs Spot (Contango Signal)

```python
# futures_premium_bps > +10: market expects price to rise → bullish
# futures_premium_bps < -10: market expects price to fall → bearish
futures_premium_bps = (futures_price - spot_price) / spot_price * 10_000
```

### 5.3 Cross-Venue Basis (Lead-Lag Signal)

Different venues often lead or lag by 5–30 seconds. If Coinbase spot is already at $94,500 while Binance shows $94,450, Binance is likely to catch up — a directional signal.

```python
cross_venue_basis_bps = (coinbase_spot - binance_spot) / binance_spot * 10_000
# > +3 bps: Binance likely to move UP → bullish
# < -3 bps: Binance likely to move DOWN → bearish
```

### 5.4 Real-Time Features — Priority Backlog

| Feature | Signal Type | Implementation Priority | API Source |
|---|---|---|---|
| **1m candle momentum** (last 5–10 candles) | Strongest for < 15min | **P0** | Binance 1m klines |
| **Multi-level OFI** (5 levels, weighted) | Strongest for < 1h | **P0** | Existing order book |
| **Futures premium vs spot** | Medium, all horizons | **P1** | Binance fapi |
| **Cross-venue basis** (Coinbase lead-lag) | Medium, all horizons | **P1** | Coinbase + Binance spot |
| **1m volume spike detector** | Unusual volume = incoming move | **P1** | Binance 1m klines |
| **σ-distance** (strike distance in vol units) | Gate calibration | **P0** | Computed from existing data |
| **Liquidation proximity** | Near-cluster = potential reversal | P2 | Coinglass public API |
| **Deribit options IV skew** | Smart money direction (> 4h) | P2 | Deribit public API |
| **Exchange net flow** (5min) | Large inflow = sell pressure | P3 | CryptoQuant free tier |

### 5.5 Macro Event Guard (Widen, Not Abstain)

The one legitimate reason to widen uncertainty — not fully abstain:

```python
MACRO_WINDOW_EVENTS = ["FOMC", "CPI", "PPI", "NFP", "Fed Chair speech", "BTC ETF ruling"]

# Within 30 min before / 15 min after a major event:
# → Widen uncertainty_margin to 0.08 (require 12% edge instead of 6%)
# → Do NOT hard-abstain unless our probability is genuinely within 5% of 0.5
```

---

## 6. Layer 4: LLM Evidence Role (Redefined)

### 6.1 What the LLM Should NOT Do

- ❌ Output a raw P(YES) probability (it cannot do this reliably — Halawi et al. 2024: LLMs score Brier ~0.179 vs crowd ~0.149)
- ❌ Make the final trade recommendation
- ❌ Override deterministic model outputs
- ❌ Learn from a single outcome by changing weights

### 6.2 What the LLM Should Do

```
INPUT: Market context + recent news + resolution rules + features summary

OUTPUT (structured, not a raw probability):
{
  "news_impact": "neutral" | "bullish" | "bearish" | "major_event",
  "news_magnitude": 0.0–1.0,            ← how significant is the news?
  "directional_evidence_score": 0.0–1.0, ← 0=strongly bearish, 0.5=neutral, 1=strongly bullish
  "resolution_risk": "clear" | "ambiguous" | "disputed",
  "contract_clarity": "confirmed" | "needs_review",
  "qualitative_signals": ["Funding rate suggests crowded longs", ...],
  "macro_guard": null | "within_event_window",
}
```

The numerical probability comes from math. The LLM adds qualitative overlay. This separation:
- Makes the system explainable and auditable
- Prevents the LLM from manufacturing a probability from nothing
- Allows the LLM to still catch macro events, news, and resolution ambiguities

---

## 7. Layer 5: Calibrated Decision Gate (Reformed)

### 7.1 Confidence-Adaptive Edge Threshold

```python
def adaptive_min_edge(
    signal_confidence: str,
    sigma_distance: float,
    is_macro_window: bool = False,
) -> Decimal:
    """
    When we have high-quality signals AND the market is not on a knife-edge,
    require a lower edge premium. Near-strike or macro events require more.
    """
    base_edge = Decimal("0.04")

    confidence_margin = {
        "high":   Decimal("0.005"),
        "medium": Decimal("0.015"),
        "low":    Decimal("0.025"),
    }.get(signal_confidence, Decimal("0.015"))

    # Near-strike penalty: oracle mismatch risk is highest when close to K
    if sigma_distance < 0.3:
        confidence_margin += Decimal("0.02")
    elif sigma_distance > 1.5:
        confidence_margin = max(Decimal("0.0"), confidence_margin - Decimal("0.01"))

    # Macro event: require more edge during vol explosions
    if is_macro_window:
        confidence_margin += Decimal("0.02")

    return base_edge + confidence_margin
    # Range: 0.040 (high conf, far from strike) → 0.085 (low conf, near strike, macro)
```

### 7.2 Market-Relative Edge (The Real Edge Signal)

```python
# Our model disagrees with the market by this much:
real_edge = our_blended_prob - market_implied_prob

# Example A: Clear trade
#   market says 52%, math says 65% → edge = 13% → BUY_YES
#
# Example B: Too close, skip
#   market says 52%, math says 55% → edge = 3% → NO_TRADE (market is probably right)
#
# Example C: Strong NO signal  
#   market says 55%, math says 32% → edge on NO side = 23% → BUY_NO
```

**The market price is a first-class ensemble member, not an obstacle.** When our model agrees with the market (edge < 5%), pass. When our model disagrees (edge > 7–8%), investigate whether we have a real signal or a model error.

### 7.3 Legitimate vs. Over-Aggressive Gates

| Gate | Legitimate? | Action |
|---|---|---|
| `DATA_STALE` (no spot price) | ✅ Yes | Keep |
| `STALE_QUOTE` (order book age > 60s) | ✅ Yes | Keep at 60s; re-fetch before decision (done) |
| `MISSING_EXECUTABLE_QUOTE` | ✅ Yes | Keep |
| `RESOLUTION_RISK` / `UNKNOWN_SETTLEMENT_SOURCE` | ⚠️ Partially | Now warning only (done) |
| `EXCESSIVE_SPREAD` > 5% | ✅ Yes | Raise to 8% for Bayse markets |
| `INSUFFICIENT_DEPTH` | ✅ Yes | Keep at $100 |
| `INSUFFICIENT_EDGE` | ✅ Yes | Use adaptive threshold above |
| `UNKNOWN_COMPARATOR` | ✅ Yes | Keep critical — we can't evaluate the trade without it |

---

## 8. Phased Implementation Roadmap

### Phase 1: Stop the Bleeding (Weeks 1–2, Highest ROI)

Eliminate ~70% of unnecessary abstentions:

| # | Change | Expected Impact | File(s) | Status |
|---|---|---|---|---|
| 1 | Re-fetch order book before decision (10s timeout) | Eliminates STALE_QUOTE abstention | `runner.py` | ✅ Done |
| 2 | Downgrade UNKNOWN_SETTLEMENT_SOURCE to warning | Eliminates most RESOLUTION_RISK | `contract_spec.py` | ✅ Done |
| 3 | **Fix probability blend normalization** | Eliminates systematic NO bias | `gemini_forecaster.py` | 🔲 Next |
| 4 | **Change LLM output to evidence score** | Removes LLM probability noise | `gemini_forecaster.py` | 🔲 Next |
| 5 | **Add σ-distance computation** | Proper knife-edge detection | `features.py` | 🔲 Next |
| 6 | **Confidence-adaptive edge threshold** | Less gate pressure on strong-signal markets | `engine.py` | 🔲 Next |

### Phase 2: Better Data → Better Probabilities (Weeks 2–4)

| # | Change | Expected Impact | File(s) |
|---|---|---|---|
| 7 | **Add 1m candle fetch** (Binance 1m OHLCV) | Sharper vol for < 15min markets | `venues.py`, `features.py` |
| 8 | **HAR-style multi-window vol blend** | More robust base probability | `features.py` |
| 9 | **Multi-level OFI** (5-level, weighted) | Stronger microstructure signal | `features.py` |
| 10 | **Futures premium vs spot** | Contango/backwardation signal | `venues.py`, `features.py` |
| 11 | **Cross-venue basis** (Coinbase lead-lag) | Additional directional feature | `venues.py`, `features.py` |
| 12 | **Macro event calendar** (FOMC, CPI guard) | Avoid trading into unmodelable vol | `research.py` |

### Phase 3: Calibration and Learning (Weeks 4–8)

| # | Change | Expected Impact |
|---|---|---|
| 13 | **Build immutable outcome ledger** | Enables real calibration |
| 14 | **Per-horizon calibration curves** | Removes over/under-confidence |
| 15 | **Train gradient-boosted model on outcome ledger** | First real ML layer |
| 16 | **Closing Line Value (CLV) tracking** | Validates picks move the right way |
| 17 | **Backtesting harness** | Validates changes before prod |

### Phase 4: Research Automation (Weeks 8–16)

| # | Change |
|---|---|
| 18 | Deribit options IV fetch (smart money direction > 4h) |
| 19 | CryptoQuant on-chain signals (exchange flow, whale detection) |
| 20 | News impact classifier (trained, not LLM-scored) |
| 21 | Multi-asset correlation (BTC dominance, ETH/BTC ratio) |

---

## 9. Expected Outcomes by Phase

| Phase | Abstention Rate | Win Rate on Taken Trades | Coverage |
|---|---|---|---|
| **Current (before fixes)** | ~85–90% | Unknown (can't measure yet) | ~10–15% |
| **After Phase 1** | ~40–50% | ~54–58% | ~50–60% |
| **After Phase 2** | ~35–45% | ~60–65% | ~55–65% |
| **After Phase 3** | ~25–35% | ~63–70% | ~65–75% |
| **After Phase 4** | ~20–30% | ~68–75% | ~70–80% |

> **Target:** 60%+ win rate on taken trades by end of Phase 2 (4 weeks).

---

## 10. What "Abstain" Should Mean After This Work

After Phase 1 and 2, `NO_TRADE` should only appear for:

| Reason | Expected Frequency |
|---|---|
| **Genuine edge < threshold** (market is efficient, we agree with it) | 25–35% of markets |
| **σ-distance < 0.2** (too close to strike, oracle mismatch risk > edge) | 5–10% of markets |
| **Macro event within ±30 minutes** | < 2% of markets |
| **Data genuinely unavailable** (all venues failed) | < 1% of markets |
| **Resolution rule is fundamentally ambiguous** | < 2% of markets |

Everything else should produce a confident `BUY_YES` or `BUY_NO`.

---

## 11. The Measurement System (Non-Negotiable Prerequisite)

Without measurement, there is no improvement. Every forecast must write an immutable record:

```json
{
  "analysis_id": "uuid",
  "forecast_at": "2026-11-11T15:31:14.531Z",
  "market_id": "platform_market_id",
  "strike": 73553.15,
  "direction": "below",
  "time_to_expiry_seconds": 829,
  "spot_at_forecast": 73653.52,
  "sigma_distance": 0.42,
  "market_implied_prob": 0.48,
  "math_prob": 0.74,
  "blended_prob": 0.63,
  "recommendation": "BUY_NO",
  "edge": 0.15,
  "vol_used": 0.61,
  "signal_confidence": "high"
}
```

And an outcome record at settlement:
```json
{
  "analysis_id": "uuid",
  "settled_at": "...",
  "actual_outcome": "YES",
  "was_correct": false,
  "brier_loss": 0.3969,
  "brier_skill_score_vs_market": -0.08
}
```

A rolling 7-day accuracy scoreboard must be visible in the UI. Without it, every accuracy claim is unfalsifiable.

---

## 12. The Core Mindset Shift

A prediction market binary outcome is **0 or 1 at resolution**. But **before** resolution, the question is: which 0 or 1 is it going to be? We have to answer that now, before we know.

The current system treats uncertainty as a reason not to bet. The correct system treats uncertainty as a **quantity to be measured and priced into the edge threshold**.

If we are 70% sure BTC will stay below $73,553 in the next 14 minutes, and the market is offering 50 cents on the dollar for that bet (50% implied probability), we have a **20% edge**. That is a good bet. The system should take it — not say "I'm not sure enough."

The goal is not to be certain. The goal is to be **better calibrated than the market price** on markets where the data gives us a real edge. That is achievable. This plan shows exactly how.

---

## 13. Immediate Next Steps (In Exact Priority Order)

1. **Fix probability blend normalization** in `gemini_forecaster.py`
   - Change LLM output from raw `P(YES)` to `directional_evidence_score` (0.0–1.0)
   - Recompute blend so weights sum to 1.0 always

2. **Add σ-distance to `CryptoMarketFeatures`** in `features.py`
   - Use it in `to_context_dict()` and log it in runner

3. **Confidence-adaptive edge threshold** in `engine.py`
   - Accept optional `sigma_distance` and `is_macro_window` params
   - Compute `adaptive_min_edge` as described in §7.1

4. **Add 1m candle fetch** to `venues.py`
   - Fetch last 60 × 1m Binance candles alongside existing 15m/1h fetches
   - Feed into `build_features()` as `ohlcv_1m`

5. **HAR-style vol blend** in `features.py`
   - Replace single-window EWMA with the multi-window blend in §4.2

6. **Build outcome ledger** as the measurement foundation
   - New SQLite table: `forecast_ledger` (immutable, append-only)
   - New SQLite table: `outcome_ledger` (written at settlement)
   - Surface 7-day accuracy in the UI

7. **Add futures premium signal** to `venues.py` and `features.py`

Each is a bounded, testable, independently shippable change.
