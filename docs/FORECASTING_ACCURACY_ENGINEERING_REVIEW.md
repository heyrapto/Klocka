# Forecasting Accuracy: Evidence Review, System Diagnosis, and Target Architecture

**Status:** Research memo. No code changes.
**Date:** 2026-11-10
**Scope:** How to improve real forecasting performance of the Prediction Intelligence Platform, with emphasis on 15m/1h crypto up/down markets.

---

## 0. Executive conclusion

The largest gains will **not** come from giving the LLM more autonomy, adding more agents, or inventing a more elaborate prompt. The evidence and the code audit point to four higher-leverage problems:

1. **The current probability is mathematically biased.** The crypto blend assigns only 77.25% total weight, so every probability is shrunk toward zero. A balanced market with a neutral model produces `P(YES) ≈ 0.386`, which can mechanically generate `BUY_NO`.
2. **The system cannot currently prove accuracy.** There is no immutable point-in-time snapshot, no replay/backtest harness, no market baseline comparison, and no authoritative outcome ledger. Reported "success" is not measurable.
3. **The system does not model the contract's actual settlement statistic.** It forecasts a point price, while the market resolves on a platform oracle, often a 60-second Chainlink TWAP with a specific comparator, timestamp, and tie rule. Near the strike, that mismatch can exceed the entire edge.
4. **The decision layer is not execution-aware.** Freshness, depth, slippage, fees, and resolution risk are not actually gated. A probability edge that cannot be executed is not an edge.

The correct redesign is therefore **not** "a smarter agent." It is a **contract-exact, point-in-time, calibrated forecasting system** in which:

- deterministic models own the numeric probability;
- the LLM is an extraction, research, and evidence layer;
- the market price is a first-class baseline and ensemble member;
- abstention is a measured, first-class outcome;
- every forecast is replayable and scored against the market.

**Recommended first move:** P0 correctness and measurement, before any new model. Specifically: fix the blend normalization, build the contract-resolution spec, build the immutable forecast/outcome ledger, and build the offline replay/evaluation harness. Only then add HAR volatility, order-flow imbalance, and per-horizon calibration.

---

## 1. What "higher success rate" must mean

A binary forecasting system can look good on "win rate" while losing money. If it wins 70% of trades but pays 0.80 per share, it loses. Conversely, a model that abstains on 95% of markets and trades only when it has a real edge can be highly successful with a modest hit rate.

The system needs a scorecard, not a single accuracy number.

### 1.1 Probability quality

| Metric | What it answers | Why it matters |
|---|---|---|
| **Brier score** | Mean squared error of probabilities | Strictly proper; punishes both wrong direction and false confidence |
| **Log loss** | Penalizes confident errors heavily | Strictly proper; useful for tail-risk detection |
| **Brier Skill Score (BSS)** | Improvement over a baseline | The only honest way to claim "better than the market" |
| **Calibration slope / intercept** | Are probabilities systematically too high/low? | Detects over/under-confidence |
| **ECE / reliability diagram** | Gap between predicted and realized frequency | Communicates calibration to users |
| **Sharpness** | How decisive the probabilities are | Calibration without sharpness is not useful |
| **AUC** | Ranking ability | Secondary; not a proper scoring rule |

The primary baseline must be the **market-implied probability at the forecast cutoff**, not a coin flip. A model that cannot beat the market's Brier score has no demonstrated forecasting edge.

### 1.2 Decision quality

| Metric | What it answers |
|---|---|
| **EV per trade, after fees/slippage** | Does the decision make money in expectation? |
| **Realized PnL / ROI** | Does it survive contact with execution? |
| **Closing Line Value (CLV)** | Did the price move toward our position after entry? |
| **Fill rate / slippage vs. quote** | Was the assumed price achievable? |
| **Abstention rate / coverage** | How often does the system decline to trade? |
| **Accuracy on covered set vs. all markets** | Is abstention selective or arbitrary? |
| **Max drawdown / turnover** | Is the strategy survivable? |

`NO_TRADE` must **not** be counted as "correct." It is a decision to not participate. It should be evaluated as:

- **Coverage:** fraction of markets where a trade was recommended.
- **Selective accuracy:** Brier/EV on the covered subset.
- **Opportunity cost:** what the abstained markets would have returned.
- **Avoided loss:** how much the abstention gate saved versus a naive always-trade policy.

### 1.3 The baseline ladder

Every model must be compared against increasingly strong baselines:

1. **Constant 0.50.**
2. **Market mid at cutoff.**
3. **Market mid + simple calibration** (e.g., isotonic on historical market prices).
4. **Log-normal / HAR volatility model.**
5. **Gradient-boosted feature model.**
6. **Calibrated ensemble of the above.**

If a model cannot beat baseline 2 out of sample, it should not be allowed to influence the final probability.

---

## 2. What the evidence actually says

### 2.1 The market is a strong baseline; shrink to it unless you have a real data advantage

Prediction markets are not perfectly efficient, but they are hard to beat. The most relevant result for this system is Halawi et al. (2024):

- An optimized LLM forecasting system achieved a **Brier score of ~0.179**.
- The human crowd achieved **~0.149**.
- An ensemble of the LLM system and the crowd achieved **~0.146**.
- A baseline GPT-4-style model was around **0.208**.

Two conclusions follow:

1. **An LLM alone is worse than the crowd.** Giving the LLM a large direct weight is not supported by the evidence.
2. **The LLM is useful as a complement.** A small, validated weight in an ensemble can slightly improve on the crowd. That is the correct role.

For 5-minute crypto markets, Gruener (2026) studied 14,808 contracts over 16 days and found stated probabilities calibrated to within about **1 percentage point**, with a realized-on-stated slope of **1.01** — but low sharpness. In other words: the market price is already a well-calibrated probability. A model that merely "re-estimates" the same public information is unlikely to add value. Any edge must come from:

- a better data source or timing,
- a better model of the exact settlement statistic,
- a better execution path,
- or a market segment where the crowd is demonstrably biased.

This is why the system must report **BSS vs. market**, not just Brier vs. 0.50.

### 2.2 Contract and oracle exactness dominate short-horizon crypto

Polymarket's crypto up/down markets resolve differently from ordinary prediction markets:

- **UMA** resolves general prediction markets.
- **Chainlink TWAP** resolves up/down crypto markets. Polymarket's docs state that up/down markets use Chainlink TWAP, with the outcome based on the final TWAP versus the price to beat.
- **Bayse** uses a **Chainlink 60-second TWAP** at the start and end timestamps, with an explicit "higher than or equal to" rule in its market text.

This has three engineering consequences:

1. **The terminal random variable is not "spot at expiry."** It is the oracle's TWAP over a specified window. The model must forecast that statistic.
2. **The strike is not an arbitrary number.** It is the price to beat, typically the start TWAP. The model must use the same source and timestamp.
3. **The comparator and tie rule matter.** `>=` vs `>` can decide a contract exactly at the strike. This must be a typed field, not inferred from a title.

A 2026 study by Dai, Jia, and Yu (Stanford/SMU) found that in Polymarket's 5-minute BTC contracts, manipulators pushed Binance spot in the final 10 seconds before settlement, extracting about **$8.2M** across 821 wallets. Binance's mid-price sat about **2.5 basis points** from the oracle resolution price. The effect was **much weaker in 15-minute contracts**. Polymarket subsequently moved to Chainlink TWAP settlement for these markets.

The lesson is not "the market is rigged." The lesson is:

- **5-minute markets are a latency and microstructure game**, not an LLM forecasting game.
- **15-minute and 1-hour markets** are more suitable for a research/forecasting system, but still require oracle-exact data.
- A generic spot feed is not a substitute for the oracle. At 5m, a 2.5 bp basis is a large fraction of the ~15 bp move; at 15m, it is smaller but still material near the strike.

### 2.3 Point-in-time data and measurement are the real bottleneck

The literature on proper scoring rules is unambiguous: Brier and log loss are strictly proper, meaning they are minimized by reporting the true probability. But they are only meaningful if:

- the forecast was made **before** the outcome;
- the inputs were available **at that timestamp**;
- the evaluation does not use later data;
- the outcome comes from an **authoritative** source, not a user's recollection.

The platform's own technical plan already specifies immutable forecast snapshots and anti-leakage rules. The live system does not implement them. Until it does, any accuracy claim is unfalsifiable.

This is not a glamorous finding, but it is the highest-value one. A mediocre model with a clean measurement loop will improve. A sophisticated model with a leaky or missing measurement loop will silently overfit and produce confident nonsense.

### 2.4 Volatility and order flow are the most defensible feature families

For short-horizon crypto, two feature families have strong, replicated support:

**Realized volatility / HAR.** Corsi's HAR model (2009) captures the long memory of realized volatility with a simple autoregression on daily, weekly, and monthly components. It is a strong, interpretable baseline. Crypto-specific work consistently finds HAR-type models effective, with extensions (HAR-J, leverage, sentiment) improving further. For this system, the immediate upgrade over a single EWMA is:

- realized variance at multiple horizons (e.g., 1m, 5m, 15m, 1h, 1d);
- a HAR-style combination;
- range-based estimators (Parkinson, Garman-Klass) for robustness;
- a volatility regime feature.

**Order-flow imbalance (OFI).** Cont, Kukanov, and Stoikov (2014) show a near-linear relationship between order-flow imbalance and short-horizon price changes, with a slope inversely proportional to market depth. This is directly relevant to 15m crypto. The system currently computes a crude top-10 depth imbalance from a snapshot, but does not use signed trade flow, queue dynamics, or multi-level depth. Those are the features that actually predict short-horizon moves.

Other features worth testing, in order of expected value:

- cross-venue basis and lead-lag (Binance vs. Coinbase vs. OKX vs. Bybit);
- distance to strike in **volatility units**, not dollars;
- time-of-day / session effects;
- funding rate and open interest changes (as regime/positioning features, not hand-tuned directional adjustments);
- recent realized skew/kurtosis;
- liquidity and spread regime.

Features that are likely theater unless rigorously validated:

- hand-tuned RSI thresholds (`>75` subtract 0.03, etc.);
- fixed funding-rate adjustments;
- "support/resistance" levels from an LLM;
- sentiment scores without a historical base rate;
- LLM-generated probability adjustments.

### 2.5 LLMs are evidence engines, not probability engines

The evidence supports a narrow role for LLMs:

**Good uses:**

- extract structured fields from screenshots (market title, asset, strike, expiry, resolution text);
- retrieve and summarize documents, news, and official rules;
- classify evidence stance (supports YES / supports NO / neutral) with citations;
- detect ambiguity or missing resolution terms;
- generate hypotheses and postmortem narratives for human review;
- translate a contract's rules into a structured spec for validation.

**Bad uses:**

- produce the final `P(YES)` from intuition;
- adjust probabilities by fixed amounts based on narrative;
- override deterministic model outputs;
- learn from a single outcome by changing weights;
- act autonomously on retrieved text without tool-level validation.

Even a strong LLM with retrieval underperforms the crowd on Brier score. The correct architecture is:

```
LLM extracts evidence and structure
        ↓
deterministic model maps evidence/features to probability
        ↓
calibration layer maps raw score to calibrated probability
        ↓
decision layer applies costs and abstention gates
```

If an LLM output is used numerically, it should be one input to a calibrated ensemble, with a weight learned out of sample — and the weight should be allowed to be zero.

### 2.6 Calibration is conditional, not global

A single global calibration curve is a compromise. The 2026 "Decomposing Crowd Wisdom" study finds that **domain-by-horizon interactions** are the largest component of calibration variation (reported at 26.0%). Crypto 15m, crypto 1h, sports, and news have different reliability curves. The system should calibrate:

- per domain (crypto, sports, news, finance);
- per horizon (5m, 15m, 1h, 4h, daily);
- per asset where sample size allows;
- per liquidity/volatility regime where sample size allows.

With small samples, use **hierarchical Bayesian pooling**: partial pooling toward the domain/horizon mean, rather than independent noisy curves. This is more stable than fitting a separate isotonic curve for every asset.

### 2.7 Execution realism converts probability into results

The decision engine currently computes EV from top-of-book asks and hardcoded costs. Real execution differs:

- the ask you see may not be the ask you get for your size;
- fees vary by platform and market;
- latency means the quote moves before your order lands;
- thin books mean the marginal price is much worse than the top level;
- resolution risk means the contract may not pay according to your model.

A probability edge is only actionable if:

```
EV_at_size = P(win) × payout − VWAP_ask(size) − fees − slippage − gas > threshold
```

and the **lower confidence bound** on `P(win)` still clears the cost. Otherwise, `NO_TRADE`.

---

## 3. Current system diagnosis

The audit found several issues that invalidate any current accuracy claim. They are ordered by severity.

### 3.1 P0 — correctness bugs

#### 3.1.1 The crypto blend loses 22.75% of probability mass

`backend/src/prediction_ai/forecasting/gemini_forecaster.py`:

```python
model_raw = 0.50 * math_prob + 0.15 * llm_prob
blended_p = 0.65 * model_raw + 0.35 * market_implied
```

Expanding:

```
blended_p = 0.325 * math_prob + 0.0975 * llm_prob + 0.35 * market_implied
```

The weights sum to **0.7725**, not 1.0. If all three inputs are 0.50, the output is:

```
0.325(0.50) + 0.0975(0.50) + 0.35(0.50) = 0.38625
```

So a neutral market with a neutral model produces `P(YES) = 0.386`, which implies `P(NO) = 0.614`. With a 0.50 ask on NO and ~2.5% costs, the decision engine sees a ~10% edge and recommends `BUY_NO`. This is not a calibration choice; it is a systematic bias toward NO. It also explains why the system may appear to "work" in some bearish regimes while being wrong in expectation.

**Fix direction:** normalize the weights, or replace fixed weights with an out-of-sample logit ensemble. Do not simply rescale by 1/0.7725 without validating the resulting calibration.

#### 3.1.2 The market matcher can select the wrong contract

`backend/src/prediction_ai/markets/matcher.py` scores:

- 40% title similarity,
- 30% asset match,
- 30% expiry on the same UTC calendar day.

For 15m markets, "same day" is far too coarse. Multiple strikes, intervals, and directions can share a day. The matcher ignores:

- resolution source and rules,
- token IDs / condition IDs,
- exact start/end timestamps,
- strike/price-to-beat,
- direction (`above` vs. `below`),
- platform-specific market IDs.

A wrong-market forecast is worse than no forecast. **Fix direction:** resolve by platform market ID or slug where available; otherwise require exact contract fields (asset, strike, direction, start, end, resolution source) and return `NEEDS_REVIEW` if ambiguous.

#### 3.1.3 The settlement model does not use the platform oracle

`backend/src/prediction_ai/specialists/crypto/settlement.py` resolves historical outcomes from a generic exchange candle near the target timestamp, not from the platform's oracle. This is a reasonable fallback for research, but it is not the contract's settlement rule.

If the contract resolves on a 60-second Chainlink TWAP, a Binance 1-minute candle is a proxy. The proxy can differ by basis points, and near the strike that changes the outcome. The system must:

- use the platform's authoritative resolution record when available;
- model the exact oracle statistic (TWAP window, source, timestamp, comparator);
- when using a proxy, label it as a proxy and widen uncertainty / abstain near the strike.

#### 3.1.4 The decision layer's freshness and liquidity gates are dead

`backend/src/prediction_ai/decision/engine.py` accepts `is_data_fresh=True` by default, and the runner never passes a real value. So the `DATA_STALE` gate never fires. There is also no depth gate, no quote-age gate, no resolution-risk gate, and no market-status gate.

`calculate_effective_costs` hardcodes a **1% taker fee** and **15 bps slippage** for every platform and market. Those assumptions are not validated against the actual fee schedule or the order book.

#### 3.1.5 Missing asks default to 0.50, creating phantom edges

In `backend/src/prediction_ai/orchestration/runner.py`, `ask_yes` and `ask_no` start at `0.50`. If the order book lacks a side, the decision engine may compare a real probability against a fabricated 0.50 ask. This can create a phantom edge and a trade recommendation on a market that cannot be executed at that price. Missing quotes should produce `NO_TRADE` / `DATA_UNAVAILABLE`, not a default.

### 3.2 P0 — measurement gaps

#### 3.2.1 No immutable point-in-time snapshot

The forecast document stores the normalized market, specialist context, and evidence, but not a reproducible snapshot of:

- the raw order book (all levels used),
- source timestamps and ingest latencies,
- the exact feature vector,
- the model inputs and outputs before blending,
- the calibration version's parameters,
- the resolution spec.

Without this, a forecast cannot be replayed or audited. The documented `input_snapshot` from `ForecastResult` is not persisted by the pipeline.

#### 3.2.2 No replay/backtest harness

There is no offline evaluation path that takes historical market snapshots, runs the forecast pipeline as of each timestamp, and scores the result. This means:

- model changes cannot be compared;
- calibration cannot be validated;
- leakage cannot be detected;
- the "success rate" cannot be computed.

#### 3.2.3 No authoritative outcome ledger

Outcomes are entered through user feedback, not resolved from the platform. The feedback endpoint can be called multiple times for the same analysis; each call increments `asset_calibration.sample_count` and applies deltas. There is no unique constraint on `analysis_id`, no settlement verification, and no separation between "user says it resolved" and "platform resolved it."

#### 3.2.4 `NO_TRADE` is always marked correct

In `backend/src/prediction_ai/learning/postmortem_agent.py`:

```python
elif recommendation == "NO_TRADE":
    was_correct = True
```

This makes abstention look perfect and contaminates the calibration sample. `NO_TRADE` should be excluded from directional accuracy and evaluated separately (coverage, opportunity cost, avoided loss).

### 3.3 P1 — model and data gaps

#### 3.3.1 The "calibration" is a label, not a fitted calibration

`calibration_version="bayesian_shrinkage_v2"` is a fixed string. There is no isotonic/Platt calibration fitted on out-of-sample data, no reliability curve, and no Brier decomposition. The `asset_calibration` table stores a `brier_score` EMA and weight deltas, but only `vol_multiplier` is actually read by the crypto specialist. `momentum_weight`, `order_flow_weight`, and `uncertainty_margin` are written but not used.

#### 3.3.2 Volatility is a single EWMA, not HAR

`backend/src/prediction_ai/specialists/crypto/features.py` uses EWMA volatility at 15m/1h and a simple realized vol for 24h. There is no HAR decomposition, no range-based estimator, and no volatility regime. For 15m horizons, the choice of volatility estimator is one of the largest drivers of the log-normal probability.

#### 3.3.3 Order flow is a snapshot imbalance, not signed flow

`analyze_order_book` computes top-10 depth imbalance from a single REST snapshot. It does not use:

- signed trade flow (aggressor-side volume),
- order additions/cancellations,
- queue position,
- multi-level depth beyond 10,
- cross-venue lead-lag.

This is the difference between a weak feature and the Cont-style OFI feature that has empirical support.

#### 3.3.4 The log-normal model ignores the oracle's averaging window

The model computes `P(S_T > K)` for a point terminal price. If the contract resolves on a 60-second TWAP, the terminal statistic is an average. For a 15m contract, the variance reduction is modest (roughly 4% in variance, 2% in sigma), but for a 5m contract it is material (roughly 13% in variance, 7% in sigma). More importantly, the model must use the same price source and strike definition as the oracle. A point-price model with a generic spot feed is not contract-exact.

#### 3.3.5 No cross-venue basis model

The live smoke test showed 8 venues with a BTC median around $73,892.99 and **14.45 bps dispersion**. At 50% annualized volatility, a 15m move has a standard deviation of about **26.7 bps**; a 5m move about **15.4 bps**. So cross-venue dispersion is roughly **54% of a 15m sigma** and **94% of a 5m sigma**. Using the wrong venue, or a stale quote, can dominate the signal. The system needs:

- a consensus spot with outlier rejection;
- per-venue basis relative to the oracle;
- source latency and staleness flags;
- a basis-corrected proxy when the oracle feed is unavailable.

### 3.4 P1 — learning-loop gaps

#### 3.4.1 Per-observation LLM deltas can drift

The postmortem agent asks the LLM for `vol_multiplier_delta`, `momentum_weight_delta`, `order_flow_weight_delta`, and `uncertainty_margin_delta`, then applies them to a persistent calibration row. A single outcome can move the model. This is not calibration; it is noise chasing. It also creates a feedback loop where the LLM's narrative about one market changes the behavior on the next.

**Fix direction:** batch refit on a schedule (weekly/monthly), with a frozen validation set and champion/challenger promotion. The LLM can propose hypotheses, but it cannot directly mutate production weights.

#### 3.4.2 The calibration sample is contaminated

Because `NO_TRADE` is always correct and feedback can be submitted multiple times, the `asset_calibration` sample is not a clean set of directional forecasts. The Brier EMA mixes abstentions, duplicates, and user-entered outcomes. This makes the calibration signal uninterpretable.

#### 3.4.3 There is no model registry or versioned artifact

`model_version` and `prompt_version` are strings in the forecast document, but there is no registry with artifact hashes, training windows, validation scores, or rollback versions. The documented `model_versions` table is not implemented in the live SQLite store.

### 3.5 P2 — operational gaps

- Three divergent persistence layers: SQLite is the live pipeline store, Postgres/Alembic is documented but not the source of truth, and Mongo is a mirror. This creates schema drift and ambiguous ownership.
- Request-ID middleware exists but is not registered; structured logs may not carry `request_id`/`trace_id` consistently.
- No circuit breaker around external providers beyond the new network layer.
- No data-quality dashboard for stale quotes, venue outages, or oracle basis.
- No cost/latency telemetry per stage in a queryable form.

---

## 4. Target system design

The redesign keeps the modular monolith but changes the contracts between layers.

### 4.1 Architecture

```
Screenshot
   ↓
[1] Contract Resolution Service
   ↓  typed ContractSpec + ambiguity flags
[2] Point-in-Time Data Plane
   ↓  raw quotes, trades, candles, oracle proxy, source timestamps
[3] Feature Builder
   ↓  versioned feature vector + as-of timestamp
[4] Forecasters
   ├─ Market baseline
   ├─ Log-normal / HAR model
   ├─ Gradient-boosted model
   └─ LLM evidence extractor (no direct probability)
   ↓
[5] Calibrator + Ensemble
   ↓  P_final + uncertainty + calibration version
[6] Decision / Execution Realism
   ↓  EV at size, gates, NO_TRADE reasons
[7] Immutable Forecast Ledger
   ↓
[8] Outcome Resolver (platform-authoritative)
   ↓
[9] Evaluator / Champion-Challenger
   ↓
   promotion or rollback
```

The LLM sits beside the forecasters, not above them. It produces structured evidence; the deterministic stack produces the number.

### 4.2 Contract Resolution Service

Every market must resolve to a typed `ContractSpec`, not a free-text title.

```text
ContractSpec
  platform: polymarket | bayse
  market_id, condition_id, token_ids, slug
  question
  outcomes: [YES, NO] or [UP, DOWN]
  asset: BTC | ETH | SOL | ...
  direction: above | below | up | down
  strike:
    value
    source: start_twap | fixed_price | platform_field
    timestamp
  settlement:
    source: chainlink_twap | uma | platform_api
    feed_id / contract / chain
    timestamp
    twap_window_seconds
    comparator: gte | gt | lte | lt
    tie_rule
    rounding / decimals
  dispute_window
  ambiguity_flags: []
  provenance: {url, fetched_at, raw_hash}
```

Hard rules:

- If `settlement.source` or `strike.source` is unknown, return `NEEDS_REVIEW`, not a forecast.
- If the title and the resolution text disagree, trust the resolution text and flag the title.
- If the platform exposes token IDs, match on them before any fuzzy title matching.
- If multiple markets match, return `AMBIGUOUS_MARKET`, not the highest score.

This layer alone will prevent a large class of wrong-market and wrong-direction errors.

### 4.3 Point-in-Time Data Plane

Store append-only records with both **event time** and **ingest time**.

Minimum raw entities:

- `quote_event`: venue, symbol, bid, ask, bid_size, ask_size, event_time, ingest_time, sequence, source_latency_ms.
- `trade_event`: venue, symbol, price, size, aggressor_side, event_time, ingest_time.
- `candle_event`: venue, symbol, interval, OHLCV, open_time, close_time, is_closed.
- `oracle_observation`: source, feed_id, value, timestamp, is_proxy, basis_bps.
- `market_snapshot`: platform, market_id, best_bid, best_ask, depth levels, last_trade, status, event_time.
- `resolution_spec`: the `ContractSpec` used at forecast time.
- `feature_vector`: version, as_of, values, quality flags, source IDs.
- `forecast`: immutable, with hashes of the inputs above.
- `outcome`: authoritative resolution, with source and timestamp.
- `evaluation`: metric values, baseline, sample window, code version.

Storage recommendation:

- **Transactional/serving:** one store only. If the MVP stays on SQLite, add the append-only tables there and treat it as the source of truth. If scaling, move to Postgres and keep SQLite for local development only.
- **Analytical/replay:** Parquet files plus DuckDB for offline evaluation. Do not build a third OLTP store.

Anti-leakage rules:

- A forecast at time `t` may only use records with `event_time <= t` and `ingest_time <= t`.
- Label windows that overlap the forecast cutoff are purged from training.
- Evaluation uses a frozen, time-ordered test set; no random splits.
- Every feature has an `as_of` timestamp; joins are as-of, not nearest-neighbor without a bound.

### 4.4 Forecasters

#### Baseline 0: market prior

The market mid at the forecast cutoff. This is the benchmark. It must be stored even if the model disagrees.

#### Baseline 1: contract-exact log-normal / HAR

For a point-terminal contract:

```
d2 = (ln(S/K) − 0.5 σ² T) / (σ √T)
P(YES) = Φ(d2)
```

For a TWAP-terminal contract, model the average over the settlement window. For a 60-second window ending at `T`, conditional on the price at `T−60`, the average log price has variance `σ² × 60/3`. Relative to a point terminal, the variance over a 15m horizon is about 4% lower; over a 5m horizon, about 13% lower. The exact implementation should follow the platform's rule, including whether the strike is the start TWAP.

Volatility:

```
σ²_t = c + β_d RV_d + β_w RV_w + β_m RV_m + regime terms
```

Use realized variance from 1m/5m returns, range-based estimators for robustness, and a HAR combination. Fit on a rolling window, evaluate walk-forward.

#### Baseline 2: gradient-boosted model

Features:

- `log(S/K) / (σ√T)` (distance in sigma),
- `T` and `sqrt(T)`,
- HAR volatility components and regime,
- signed order-flow imbalance over 1m/5m/15m,
- depth imbalance and depth at multiple levels,
- cross-venue basis and lead-lag,
- funding/OI changes,
- time of day / session,
- recent realized skew/kurtosis,
- spread/liquidity regime,
- platform-specific effects.

Use LightGBM or XGBoost with monotonic constraints where economically justified. Calibrate out-of-sample. Do not let the model see the outcome or any post-cutoff data.

#### LLM evidence layer

The LLM may produce:

- extracted contract fields,
- evidence items with citations and stance,
- ambiguity flags,
- a narrative explanation.

It may **not** produce the final probability. If its evidence is used numerically, it must be converted into structured features and validated against historical base rates. For example, "SEC approval announced" is an event feature; the model learns the historical probability of a price move conditional on that event, rather than the LLM guessing a number.

### 4.5 Calibration and ensemble

#### Logit ensemble

Instead of fixed weights, combine forecasts in logit space:

```
logit(P_final) = a + b × logit(P_model) + c × logit(P_market)
P_final = sigmoid(...)
```

Fit `a`, `b`, `c` on out-of-sample data. Properties:

- If `b` is not significantly positive, the model has no incremental value and should be dropped.
- If `c` dominates, the system correctly shrinks to the market.
- The intercept `a` corrects systematic bias.
- The output is calibrated by construction if the logit link is appropriate; still validate with a reliability curve.

#### Calibration

- Fit isotonic or beta calibration on a held-out, time-ordered set.
- Calibrate per domain × horizon, with hierarchical pooling for small samples.
- Report calibration slope, intercept, ECE, and reliability diagrams.
- Use conformal prediction or bootstrapped ensembles for uncertainty intervals; do not use LLM-generated intervals.

#### Uncertainty

The uncertainty interval must reflect:

- parameter uncertainty (model/calibration),
- data uncertainty (stale quotes, venue basis, oracle proxy),
- model disagreement (ensemble spread),
- resolution ambiguity.

A forecast with a wide interval should be more likely to abstain, not more likely to trade.

### 4.6 Decision and execution realism

Inputs:

- `P_final` and its lower confidence bound,
- full order book snapshot (not just top of book),
- platform fee schedule,
- expected latency,
- resolution risk flags,
- market status and time to close.

Compute:

```
VWAP_ask(size) = Σ(price_i × filled_i) / size
effective_cost = VWAP_ask + fees + gas + slippage_buffer
EV = P_lower × payout − effective_cost
```

Gates, in order:

1. `DATA_UNAVAILABLE` — missing/stale quote, missing strike, missing resolution spec.
2. `STALE_QUOTE` — quote age > threshold.
3. `EXCESSIVE_SPREAD` — spread > threshold.
4. `INSUFFICIENT_DEPTH` — size cannot be filled within a price limit.
5. `RESOLUTION_AMBIGUOUS` — oracle/comparator/timestamp unclear.
6. `ORACLE_MISMATCH` — proxy basis > threshold and market near strike.
7. `INSUFFICIENT_EDGE` — EV lower bound < threshold.
8. `RISK_LIMIT` — position size / exposure cap.

Only then emit `BUY_YES` / `BUY_NO`. Every `NO_TRADE` gets a machine-readable reason code and a human-readable explanation.

### 4.7 Evaluation and learning

**Offline replay:**

- Replay historical snapshots as of each forecast time.
- Run the exact production pipeline with the exact feature/model/calibration versions.
- Score against the authoritative outcome.
- Compare to market baseline, constant 0.50, and always-NO_TRADE.

**Walk-forward protocol:**

```
train:      [t0, t1)
validation: [t1, t2)
test:       [t2, t3]
```

- Purge overlapping label windows.
- Embargo a gap between train and test to avoid leakage.
- Use block bootstrap by day (or by market series) for confidence intervals; trades within a day are correlated.
- Freeze the test set; do not tune on it.

**Promotion criteria (example):**

- BSS vs. market > 0 with a 95% block-bootstrap CI that excludes 0.
- Calibration slope in [0.90, 1.10], intercept near 0.
- ECE < 2 percentage points on the test set.
- Positive EV after fees/slippage on at least N independent days.
- No horizon/asset with a material degradation.
- Abstention rate consistent with the intended coverage.

**Learning loop:**

- Batch refit weekly or monthly.
- Champion/challenger: the challenger must beat the champion on a frozen validation window before promotion.
- Human approval for production promotion.
- Postmortems propose hypotheses; they do not mutate weights directly.
- Keep a rollback version and artifact hash.

### 4.8 LLM and research layer

The LLM/research layer should be designed as a **tool-using evidence service**, with hard boundaries:

- structured outputs validated by Pydantic/Zod;
- every numeric claim must cite a tool result or source URL;
- retrieved text is untrusted data, never instructions;
- no shell/HTTP tool invocation from prompt text;
- no secrets in prompts;
- cross-check extracted fields against the platform API;
- contradiction detection across sources;
- explicit "unknown" / "ambiguous" outputs;
- token and latency budgets;
- cached, versioned evidence packs.

For news/sports, the same pattern applies: the LLM extracts events (injury, lineup, announcement), and a historical model maps those events to probability. The LLM does not guess the number.

---

## 5. The first model I would build

Do not start with a deep model. Start with the strongest simple baselines and make them contract-exact.

### 5.1 Crypto 15m baseline

1. **Market mid** at cutoff.
2. **Log-normal with HAR-RV volatility**, using the exact oracle statistic and a basis-corrected spot.
3. **Calibrated blend** of the two in logit space.
4. **Add OFI** and cross-venue basis as features; test incremental BSS.
5. **Add a gradient-boosted model** only if it beats the calibrated blend out of sample.

This sequence gives a clear attribution of value: volatility, then microstructure, then ML.

### 5.2 Why this order

- The log-normal model is transparent and has a clear failure mode: bad volatility or wrong strike.
- HAR is a strong, low-variance volatility baseline.
- OFI has empirical support and is cheap to compute from websocket data.
- A GBM can overfit; it should be added only after the evaluation harness can detect overfitting.
- The market prior prevents the system from becoming confidently wrong in an efficient market.

### 5.3 Calibration sample requirements

For 15m markets, there are about 96 intervals per day per asset. Thirty days gives ~2,880 observations, but they are highly correlated within a day. Treat the effective sample size as closer to the number of independent days (30), not 2,880. This means:

- use block bootstrap by day;
- pool across assets/horizons with hierarchical models;
- do not expect statistical significance from a few hundred trades;
- pre-register the experiment and the minimum detectable effect.

---

## 6. Experiment ladder

| Stage | What runs | Entry criteria | Exit criteria |
|---|---|---|---|
| **0. Offline replay** | Historical 15m markets, frozen snapshots | Clean point-in-time data | Baselines reproducible; no leakage |
| **1. Shadow** | Live data, forecasts logged, no user recommendations | Replay harness passes | 2–4 weeks of stable logs; calibration reasonable |
| **2. Paper** | Executable quotes, simulated fills, fees/latency | Shadow metrics pass | Positive EV after costs on independent days |
| **3. Small live** | Human-approved, capped size | Paper metrics pass | CLV and PnL consistent with paper |
| **4. Scale** | Increase coverage/size | Live metrics pass | No degradation across regimes |

**Kill criteria:**

- No BSS improvement over market after two refit cycles.
- Calibration slope outside [0.80, 1.20] on the test set.
- Negative CLV or negative EV after costs over a meaningful sample.
- A single asset/horizon driving all the performance (fragility).
- Resolution ambiguity or oracle mismatch in a material fraction of trades.

---

## 7. Limitations and mitigations

| Limitation | Why it matters | Mitigation |
|---|---|---|
| **Markets are efficient** | Most "edges" are noise or costs | Market baseline; shrink to market; abstain; require BSS > 0 |
| **Oracle mismatch** | A generic spot feed is not the settlement price | ContractSpec; exact oracle feed; basis model; proxy flag; abstain near strike |
| **5m manipulation/latency** | Final-seconds order flow can flip outcomes | Focus 15m/1h; TWAP settlement; latency budget; avoid 5m unless explicitly researched |
| **Thin books** | Top-of-book is not executable for size | Depth walk; size cap; `INSUFFICIENT_DEPTH` gate |
| **Adverse selection** | Quotes move after you decide | Quote-age gate; latency measurement; limit orders; abort on move |
| **Small samples** | Calibration curves overfit | Hierarchical pooling; block bootstrap; pre-registration; frozen test set |
| **Regime shifts** | A model trained in one vol regime fails in another | Rolling refit; regime features; drift monitoring; champion/challenger |
| **LLM hallucination** | Confident but ungrounded numbers | Structured extraction; tool verification; citations; no direct probability |
| **Prompt injection** | Retrieved text can contain instructions | Treat as data; allowlist tools; no secrets; sandbox; output validation |
| **Data outages** | Missing data can create phantom edges | `DATA_UNAVAILABLE`; no default 0.50; multi-venue consensus; circuit breaker |
| **Resolution disputes** | The contract may not pay as modeled | Resolution-risk flags; UMA/Chainlink rules; abstain on ambiguity |
| **Legal/ToS** | Scraping or unauthorized data use | Official APIs; rate limits; jurisdiction review; no scraping |
| **No guarantee** | Forecasting is probabilistic | Communicate uncertainty; never promise win rates; abstention is valid |

The honest position: the system can aim to be **better calibrated and better at abstaining**, not to predict every 15m move. Most of the time, the correct output may be `NO_TRADE`.

---

## 8. Roadmap

### P0 — Correctness and measurement (1–2 weeks)

**Goal:** Make the system's output mathematically valid and its performance measurable.

1. Fix the crypto blend normalization; add a regression test for total weight = 1.0.
2. Replace fixed weights with a configurable, validated logit ensemble (or at minimum a normalized, documented blend).
3. Add `ContractSpec` with resolution source, oracle, timestamp, comparator, strike source, and ambiguity flags.
4. Require exact market identity (ID/slug/token) before forecasting; return `NEEDS_REVIEW` on ambiguity.
5. Wire real freshness, quote-age, spread, depth, and resolution-risk gates into the decision engine.
6. Remove the 0.50 default asks; missing quotes => `DATA_UNAVAILABLE`.
7. Persist an immutable forecast snapshot: raw inputs, feature vector, model outputs, versions, hashes.
8. Build the offline replay harness and the baseline scorecard (Brier, BSS vs. market, calibration, EV).
9. Make feedback idempotent; separate user feedback from authoritative outcome resolution; stop counting `NO_TRADE` as correct.
10. Replace per-observation LLM deltas with batch refits and a champion/challenger gate.

**Exit criteria:** A historical 15m market can be replayed; the forecast is reproducible; the scorecard reports BSS vs. market; no default/fabricated quotes can reach the decision engine.

### P1 — Contract-exact model and execution realism (3–6 weeks)

**Goal:** Build a model that can actually beat the market baseline, and a decision layer that respects execution.

1. Implement HAR-RV volatility with 1m/5m/15m/1h components and range-based estimators.
2. Implement TWAP-aware terminal distribution for 60s oracle windows.
3. Add cross-venue consensus, basis tracking, and stale/outlier detection.
4. Add websocket L2/trade ingestion for the top venues; compute signed OFI and multi-level depth.
5. Fit per-domain/horizon calibration (isotonic/beta) with hierarchical pooling.
6. Implement depth-walked EV, platform fee schedules, latency, and size caps.
7. Add resolution-risk and oracle-mismatch gates.
8. Build the shadow-mode dashboard: coverage, calibration, BSS, EV, abstention reasons.
9. Keep the LLM as an evidence extractor with citations and validators; remove it from the numeric path.

**Exit criteria:** On a frozen test set, the calibrated model has BSS > 0 vs. market (with CI), calibration slope in [0.9, 1.1], and positive EV after costs in paper trading.

### P2 — Scale and other domains (6–12 weeks)

1. Sports/news specialists with closing-line-value evaluation.
2. Hierarchical calibration across domains.
3. Conformal uncertainty intervals.
4. Portfolio-level risk and exposure limits.
5. Monitoring, alerting, and data-quality dashboards.
6. Model registry with artifact hashes and rollback.
7. Consolidate persistence to one transactional store + one analytical store.

---

## 9. What not to do

- **Do not add more agents or personas.** More LLM roles increase variance and cost without adding calibrated information.
- **Do not let the LLM output the final probability.** The evidence does not support it.
- **Do not calibrate on the same data you evaluate.** Use walk-forward, purged splits.
- **Do not use win rate as the primary metric.** Use proper scoring rules and EV after costs.
- **Do not ignore fees, depth, latency, or oracle basis.** They can exceed the edge.
- **Do not trade 5m markets without a latency/manipulation thesis.** The evidence shows a final-seconds order-flow game.
- **Do not use user feedback as ground truth for outcomes.** Resolve from the platform.
- **Do not apply per-trade LLM weight updates.** Batch refit with validation.
- **Do not hardcode 1% fees or 0.50 asks.** Read the platform and the book.
- **Do not backtest with data after the forecast cutoff.** Purge and embargo.
- **Do not present a positive badge for `NO_TRADE`.** Abstention is not a win; it is a risk decision.

---

## 10. Recommendation and decision

The highest-ROI sequence is:

1. **P0 correctness and measurement** — fix the blend, the contract spec, the decision gates, and the replay harness.
2. **P1 contract-exact model** — HAR volatility, TWAP-aware terminal, OFI, calibration.
3. **P1 execution realism** — depth, fees, latency, resolution risk.
4. **P2 LLM evidence layer and other domains** — only after the numeric core is measurable.

The single most important change is **not** a new model. It is making the system **honest**: a forecast that can be replayed, scored against the market, and abstained from when the data or the edge is insufficient.

### Decision requested

Which track should we implement first?

- **Track A (recommended): 15m crypto correctness + measurement.** Fix the blend, contract spec, decision gates, and replay harness. This is the foundation for every other claim.
- **Track B: research/LLM evidence layer.** Build the retrieval, citation, and structured-extraction pipeline, but keep it out of the numeric path until validated.
- **Track C: full platform calibration/evaluation.** Build the cross-domain scorecard and champion/challenger system before adding new models.

My recommendation is **Track A**, then **Track C** as the evaluation spine, then **Track B** as an evidence layer. The order matters: measurement before models, models before agents.

---

## 11. Track A implementation status (11 November 2026)

This section records what has actually shipped in the repository. It is an
implementation note, not a new claim of accuracy.

### 11.1 Correctness changes

| Area | Implementation | Effect |
|---|---|---|
| Blend normalization | `forecasting/blend.py` + validated `FORECAST_*_WEIGHT` settings | Weights must sum to exactly 1.0; the previous 0.7725 mass is impossible. |
| Blend transparency | `input_snapshot` stores component probabilities, weights, and `blend_version` | Every forecast shows how the final probability was constructed. |
| Contract identity | `schemas/contract.py` + `markets/contract_spec.py` | Captures platform/token IDs, strike source, oracle, settlement timestamp, TWAP window, comparator, tie rule, and ambiguity flags. |
| Strict crypto gating | `CONTRACT_SPEC_STRICT_CRYPTO` + `RESOLUTION_RISK` gate | Critical oracle/strike/comparator ambiguity blocks a trade instead of producing an ungrounded recommendation. |
| Quote mapping | Runner resolves by `token_id`, then unique `outcome_id`, then normalized label | Polymarket's shared condition ID can no longer map YES and NO to the same quote. |
| Market prior | Forecaster resolves the affirmative quote by token ID and omits the market component when no usable mid exists | No synthetic 0.50 prior; weights renormalize and `market_anchor_available` is recorded. |
| Missing quotes | No synthetic 0.50 fallback anywhere in the decision path | A missing ask now produces abstention or a one-sided evaluation, never a fabricated price. |
| Invalid quotes | Asks at `0.00` or `1.00` are treated as non-executable | A zero ask can no longer create a "free contract" with positive edge. |
| Immutable snapshots | SQLite additive columns plus API exposure | Forecasts persist `contract_spec`, `input_snapshot`, `feature_snapshot`, `model_components`, execution diagnostics, and `target_definition`. |
| Point-in-time target | `target_definition` maps `probability_yes` to the affirmative outcome with direction, IDs, labels, strike, and settlement timestamp | UP/DOWN contracts are no longer described as generic YES/NO. |
| Feedback integrity | One settlement per analysis; duplicate matching submissions are idempotent; conflicting outcomes return HTTP 409 | The learning sample cannot be inflated by resubmitting the same result. |
| Abstention integrity | `NO_TRADE` stores `was_correct=False`, `evaluation_eligible=False`, skips calibration updates, and receives a deterministic `ABSTENTION_NO_TRADE` postmortem | Abstaining is recorded as a risk decision, not a win. |

### 11.2 Execution-cost model

Fee models are per share and use the platform-supplied taker rate:

- **Polymarket:** `fee(p) = fee_rate * p * (1 - p)`
- **Bayse:** `fee(p) = fee_rate * p * max(1 - p, 0.5)`
- **Effective all-in cost:** `p + fee(p) + p * slippage_bps / 10000`
- **Depth-aware cost:** walk the ask ladder to the target notional and use the
  VWAP; the walk reports filled notional, shares, and whether the target was
  completed.

At `p = 0.50` with a 7% Polymarket rate the fee is `0.0175` per share; with a
10% Bayse rate it is `0.025` per share. These are materially larger than the
old flat 1% assumption near the middle of the probability range.

### 11.3 Decision gates

`decision/engine.py` evaluates, in order:

1. `DATA_STALE` — upstream feed freshness.
2. `STALE_QUOTE` — order-book snapshot age versus `DECISION_MAX_QUOTE_AGE_SECONDS`.
3. `MISSING_EXECUTABLE_QUOTE` / `INVALID_EXECUTABLE_QUOTE` — absent or endpoint asks.
4. `RESOLUTION_RISK` — critical `ContractSpec` flags (strict crypto mode).
5. `EXCESSIVE_SPREAD` — cross-side spread above `DECISION_MAX_SPREAD`.
6. `INSUFFICIENT_DEPTH` — known depth below `DECISION_MIN_DEPTH_USD` or an incomplete target-notional walk.
7. `INSUFFICIENT_EDGE` — net edge below `DECISION_MIN_EDGE + DECISION_UNCERTAINTY_MARGIN`.

Depth is only gated when `depth_known` is true. Unknown depth is a documented
quality limitation, not silently treated as infinite liquidity.

### 11.4 Evaluation CLI

The offline scorecard operates on resolved JSONL records. It never joins
later data automatically; the caller must supply a frozen, point-in-time
extract.

Required fields per line:

- `forecast_id` — non-empty string.
- `probability_yes` — between 0 and 1.
- `actual_outcome` — `YES`/`NO`, `UP`/`DOWN`, `HIGHER`/`LOWER`,
  `BUY UP`/`BUY DOWN`, `1`/`0`, or a boolean.

Useful optional fields:

- `market_probability_yes` — enables BSS versus the market baseline.
- `recommendation` — `BUY_YES`, `BUY_NO`, or `NO_TRADE`.
- `effective_cost_yes` / `effective_cost_no` — actual after-fee cost per share.
  If absent, `ask_yes` / `ask_no` are used.
- `forecasted_at` / `resolved_at` — validated so settlement cannot predate the
  forecast.
- `evaluation_eligible` — set `false` to exclude a record entirely.
- `category`, `asset`, `market_id`, `model_version`, `calibration_version`,
  `contract_spec_hash`, `uncertainty_interval` — retained for slicing and audit.

Example record:

```json
{"forecast_id":"f-123","probability_yes":"0.62","actual_outcome":"UP","market_probability_yes":"0.55","recommendation":"BUY_YES","effective_cost_yes":"0.58","forecasted_at":"2026-11-11T12:00:00Z","resolved_at":"2026-11-11T12:15:00Z"}
```

Run it from the backend environment:

```bash
cd backend
.venv/bin/python -m prediction_ai.evaluation.cli --input resolved.jsonl
.venv/bin/python -m prediction_ai.evaluation.cli --input resolved.jsonl --output scorecard.json --bins 10
```

The output includes sample/coverage counts, Brier score, log loss, outcome
accuracy, market Brier, BSS, ECE/MCE, sharpness, calibration bins, and a
decision scorecard (`trade_count`, win rate, direction accuracy, average edge,
realized PnL per share, `NO_TRADE` count/rate). Abstentions remain in the
probability scorecard because the forecast still exists, but they are excluded
from trade PnL and are never counted as correct trades.

### 11.5 Remaining limitations

- **No calibration artifact is active.** Track C can fit review-only Platt and
  isotonic artifacts, but none is applied automatically. The active runtime
  label remains `unfitted_linear_normalized_v2`; do not describe live output as
  calibrated until a reviewed artifact is explicitly promoted.
- **No automatic historical replay.** The CLI scores supplied resolved records;
  it does not yet reconstruct order books or run the live pipeline as of a past
  timestamp.
- **Outcome authority is incomplete.** Feedback is idempotent, and crypto
  auto-settlement exists, but a full platform-authoritative outcome ledger
  across both venues is still future work.
- **REST snapshots, not streaming L2.** Quote age, spread, and depth are only as
  good as the last REST snapshot. There is no websocket trade-flow or queue
  model yet.
- **Missing market anchors are informational only.** When the platform has no
  usable mid, the forecast excludes the market prior and renormalizes the
  model weights. The decision layer will normally abstain because there is no
  executable ask; the resulting probability must not be treated as a
  tradeable edge.
- **Oracle proxy basis.** Historical crypto settlement resolution uses an
  exchange candle proxy where the platform's exact Chainlink feed is not
  available; basis risk remains, especially near the strike.
- **SQLite is the live pipeline store.** Postgres/Alembic models and Mongo
  documents remain divergent; the new snapshot columns are implemented in the
  SQLite store used by the running pipeline.
- **Frontend reporting is partial.** The forecast API now returns
  `contract_spec`, `feature_snapshot`, and `model_components`, but the current
  UI does not render every gating flag or depth diagnostic. The fields are
  additive, so existing clients continue to work.
- **Strict-mode scope.** Critical contract ambiguity blocks crypto trades under
  the default policy; non-crypto categories still rely on warning-level flags
  and should be evaluated separately before enabling the same strictness.

---

## 12. Track C implementation status (11 November 2026)

Track C is the evaluation spine: bounded point-in-time export, honest
walk-forward calibration, and review-only artifacts. It does not add a model,
an agent, or an automatically deployed calibrator.

### 12.1 Bounded forecast/feedback export

`SQLiteStore.list_resolved_forecast_feedback()` joins each forecast to the
earliest feedback row for its analysis and orders by
`(forecasted_at, forecast_id)`. Every page is capped at 100 rows. It excludes
forecasts without `forecasted_at` and feedback without an outcome.

The exporter converts each pair into the same `EvaluationRecord` used by the
scorecard. Two rules matter:

- `resolved_at = max(settlement timestamp, feedback.created_at)`. A label is
  never treated as observable before the system could have seen it.
- A missing or invalid probability, an unrecognized outcome, or a malformed
  snapshot drops the record with an explicit skip reason. Invalid asks and
  costs are stored as `null` rather than fabricated. Nothing is imputed.

Export usage:

```bash
cd backend
.venv/bin/python -m prediction_ai.evaluation.export_cli \
  --sqlite-path ./var/prediction_intelligence.db \
  --category crypto \
  --output resolved.jsonl \
  --summary export_summary.json
```

`--limit` must be between 1 and 100. `--max-pages` is a hard loop bound; if it
is reached, the summary reports `"truncated": true` instead of pretending the
export is complete. The SQLite file must already exist; the export CLI does
not create an empty database. The summary reports `exported`, `skipped`,
`skipped_reasons`, `pages`, and `truncated`.

### 12.2 Purged walk-forward calibration

`evaluation/calibration.py` implements two calibrators:

- **Platt scaling** — logistic map over the raw logit. The slope is constrained
  non-negative so calibration cannot invert the forecast ordering.
- **Isotonic regression** — pool-adjacent-violators fit, serialized as strictly
  increasing thresholds with non-decreasing values and linear interpolation
  between knots.

Both fits use float64 internally only for optimization. Serialized parameters
and output probabilities are `Decimal`, clamped to `[1e-6, 1 - 1e-6]` so no
artifact can emit certainty or cause infinite log loss.

Walk-forward protocol:

1. Exclude `evaluation_eligible=False` records and records missing either
   timestamp.
2. Sort by `forecasted_at` and reserve an initial training seed.
3. Split the remainder into sequential test blocks.
4. For a test block starting at `t`, train only on earlier forecasts whose
   `resolved_at < t`. Labels that resolved at or after `t`, or that have no
   resolution timestamp, are purged. The purged count is reported per fold.
5. Score the raw and calibrated probabilities on the identical test records.

A fold with too few training samples or too few examples of either class is
reported as `insufficient_data` with a reason such as
`insufficient_samples_after_purge`. No synthetic samples, class rebalancing, or
0.50 prior is used to force a fit.

### 12.3 Calibration CLI and artifacts

```bash
cd backend
.venv/bin/python -m prediction_ai.evaluation.calibrate_cli \
  --sqlite-path ./var/prediction_intelligence.db \
  --category crypto \
  --output calibration_report.json \
  --artifact-dir ./var/calibration_review \
  --folds 5 \
  --min-train-samples 30 \
  --min-class-samples 5 \
  --ece-target 0.05
```

Use `--input resolved.jsonl` instead of `--sqlite-path` to consume a frozen
export. The report contains dataset diagnostics, per-method fold reports
(training window, purged labels, raw and calibrated Brier/log loss/ECE), and an
aggregate raw-versus-calibrated scorecard on the out-of-sample folds.

A method is recommended only when it improves out-of-sample Brier score **and**
its calibrated ECE is at or below `--ece-target`. Otherwise
`recommended_method` is `null` and `recommendation_reason` states why. An
insufficient-data verdict exits with status 0 because reporting "not enough
evidence" is a successful, honest outcome; it is not converted into a trade or
a confidence claim.

Artifacts are written only when `--artifact-dir` is supplied. Each artifact
records the training window, label-availability boundary, class counts,
training-data hash, fitted parameters, and a `deployment_status` of
`review_required`. The `artifact_hash` is deterministic SHA-256 over the
method, parameters, training hash, and window; `created_at` is excluded so
re-running the same data produces the same `calibration_version`
(`<method>_v1_<hash-prefix>`). The CLI overwrites
`calibration_<method>.json`, so point it at a fresh or versioned review
directory. Nothing in the runtime reads these files.

### 12.4 Track C limitations

- **Feedback is not platform-authoritative yet.** Calibration inherits any
  mislabeled or disputed feedback outcome until an authoritative settlement
  ledger exists.
- **Calibration cannot create discrimination.** A model with no BSS edge over
  the market remains a losing model after calibration; calibration only fixes
  the probability mapping.
- **The calibration sample is selection-biased.** `NO_TRADE` feedback is
  `evaluation_eligible=False`, so the current fit learns only from forecasts
  that led to a trade. If trading is rare, calibration data is scarce; if the
  decision policy changes which markets it trades, the calibration sample
  changes with it. Track this coverage explicitly before promotion.
- **Small samples are unstable.** Isotonic regression is especially prone to
  overfitting below roughly 1,000 labels. The report notes this risk and breaks
  exact Brier ties toward Platt, but promotion still requires human review.
- **No hierarchical pooling.** Crypto, sports, and news currently share one
  global fit per invocation. Per-domain/horizon pooling remains future work.
- **No automatic replay.** The export reads persisted forecast/feedback pairs;
  it does not reconstruct historical order books or rerun the live pipeline at
  a past timestamp.
- **Conservative labels can shorten history.** Using
  `max(settlement, feedback.created_at)` is deliberately pessimistic. Late
  feedback reduces the usable training window rather than leaking a label.
- **Artifacts are inert by design.** Promotion requires a human decision,
  a code/config change, and a new point-in-time evaluation. The CLI never
  edits model weights, thresholds, or environment settings.

Tests covering this path live in
`tests/unit/test_evaluation_exporter.py` and `tests/unit/test_calibration.py`.

---

## Appendix A — Metric definitions

- **Brier score:** `mean((p − y)²)`, lower is better; 0.25 for constant 0.5.
- **Log loss:** `−mean(y log p + (1−y) log(1−p))`, lower is better.
- **BSS:** `1 − Brier_model / Brier_baseline`; positive means better than baseline.
- **Calibration slope:** regress `y` on `logit(p)`; slope 1 is ideal.
- **ECE:** weighted average `|accuracy − confidence|` across bins.
- **Sharpness:** variance of predicted probabilities; higher is sharper, but only meaningful if calibrated.
- **EV:** `P(win) × payout − effective_cost`.
- **CLV:** difference between entry price and closing price in the favorable direction.

## Appendix B — References

1. Halawi, D., et al. (2024). *Approaching Human-Level Forecasting with Language Models.* NeurIPS. arXiv:2402.18563.
2. Gruener, L. P. (2026). *Calibrated but Not Sharp: Stated Probabilities in Five-Minute Crypto Markets on Polymarket.* SSRN 6863546.
3. Dai, D., Jia, R., & Yu, S. (2026). *Settlement Manipulation in Prediction Markets.* arXiv:2606.31675.
4. Cont, R., Kukanov, A., & Stoikov, S. (2014). *The Price Impact of Order Book Events.* Journal of Financial Econometrics, 12(1), 47–88. arXiv:1011.6402.
5. Corsi, F. (2009). *A Simple Approximate Long-Memory Model of Realized Volatility.* Journal of Financial Econometrics, 7(2), 174–196.
6. *Decomposing Crowd Wisdom: Domain-Specific Calibration Dynamics in Prediction Markets.* arXiv:2602.19520 (2026).
7. Gneiting, T., & Raftery, A. E. (2007). *Strictly Proper Scoring Rules, Prediction, and Estimation.* JASA, 102(477), 359–378.
8. Polymarket Documentation — *Resolution* (UMA and Chainlink TWAP). https://docs.polymarket.com/concepts/resolution
9. Bayse Markets — crypto up/down market rules (Chainlink 60-second TWAP). https://www.bayse.markets/crypto
10. Chainlink — *Prediction Markets* and Data Streams. https://chain.link/use-cases/prediction-markets
