# Market Price History & Closing-Line Evaluation

> **Scope:** historical market-price capture, Polymarket backfill, and the
> market-relative evaluation report. This document does not change the
> analysis/decision-only product boundary.

---

## 1. Why this exists

The model probability must be judged against the **market**, not only against
itself. Without a stored price path there is no way to answer:

- did the forecast beat the market price at the moment it was made?
- did the market move toward the model's side by settlement (CLV)?
- was a `NO_TRADE` a disciplined save or a missed edge?

Prices are probabilities in `[0, 1]` and are stored as decimal **text**, never
binary floats.

---

## 2. Storage

SQLite table `market_price_history`:

| Column | Meaning |
|---|---|
| `platform` | `polymarket` or `bayse` |
| `platform_market_id` | Venue market id |
| `outcome_id` | Internal outcome id from the verified market snapshot |
| `token_id` | CLOB token id used by Polymarket history |
| `label` | Outcome label (`Up`, `Down`, `Yes`, `No`, …) |
| `observed_at` | UTC ISO-8601 observation time |
| `price` | Decimal probability text |
| `kind` | `mid`, `last_trade`, or `displayed` |
| `source` | `polymarket_clob_prices_history`, `polymarket_clob_snapshot`, or `bayse_clob_snapshot` |
| `granularity_seconds` | Nominal spacing of the path |
| `ingested_at` | When this row was written |

Primary key: `(platform, platform_market_id, outcome_id, observed_at, source)`.
Inserts use `INSERT OR IGNORE`, so re-running a backfill is idempotent.

Repository methods (`prediction_ai.db.sqlite.SQLiteStore`):

- `record_price_history(history)` / `record_price_histories(histories)`
- `get_price_history(platform, market_id, outcome_id, start, end, limit)`
- `get_latest_price_before(platform, market_id, outcome_id, cutoff)`

`get_price_history` is capped at `MAX_HISTORY_POINTS = 5000`.
`get_latest_price_before` is the point-in-time primitive used by the report; it
never returns a price observed after the cutoff.

---

## 3. Forward capture (both platforms)

After the pre-decision order-book refresh, the analysis runner converts the
exact book used for the decision into one midpoint snapshot per outcome and
persists it. Capture is best-effort: a history write cannot fail a forecast.
Each snapshot records the book's `observed_at`, so quote age remains auditable.

Bayse publishes no documented historical-price endpoint. Its forward snapshots
are therefore the only non-fabricated Bayse price path. Polymarket history can
be backfilled from the public CLOB endpoint below.

---

## 4. Polymarket backfill

Endpoint: `GET {POLYMARKET_CLOB_API_URL}/prices-history`

Parameters used:

| Parameter | Value |
|---|---|
| `market` | outcome CLOB token id |
| `startTs` | window start, Unix seconds |
| `endTs` | window end, Unix seconds |
| `fidelity` | minutes per point |

The backfill service is `prediction_ai.evaluation.history_backfill`. It:

1. Pages resolved forecast/feedback pairs at 100 rows per page, cursor-keyed on
   `(forecasted_at, forecast_id)`, with a hard page cap.
2. Reads the affirmative outcome id from the forecast's recorded `contract_spec`
   (never by re-parsing the title).
3. Fetches both outcomes over `[forecasted_at − lookback, settlement_at]`
   (falling back to the label-availability time when settlement is unknown).
4. Chooses fidelity from the window (1 min up to 24 h, 5 min up to 7 d, 15 min
   up to 90 d, 60 min beyond) unless `--fidelity-minutes` is fixed.
5. Downsamples to `PRICE_HISTORY_MAX_POINTS` before writing.
6. Skips a missing token id or a provider failure per outcome; never drops the
   page.

Run it:

```bash
cd backend
.venv/bin/python -m prediction_ai.evaluation.history_cli backfill \
  --sqlite-path ./var/prediction_intelligence.db \
  --category crypto \
  --lookback-hours 6
```

The summary reports `pairs_scanned`, `outcomes_requested`, `points_fetched`,
`points_inserted`, `fetch_failures`, skip reasons, and whether pagination was
truncated. `points_inserted` is zero on an idempotent re-run.

---

## 5. Closing-line report

```bash
cd backend
.venv/bin/python -m prediction_ai.evaluation.history_cli report \
  --sqlite-path ./var/prediction_intelligence.db \
  --category crypto \
  --output closing_line.json
```

Per resolved forecast the report records:

- **entry market probability** — the forecast snapshot's `market_implied` when
  present, otherwise the latest stored price at or before `forecasted_at`;
- **closing market probability** — the latest stored price at or before the
  settlement timestamp. A later post-resolution print is never used;
- **Brier** for the model, the entry market, and the closing market;
- **BSS** versus each market baseline, computed on the same sample subset;
- **CLV** for `BUY_YES`/`BUY_NO`: closing price of the bought side minus the
  recorded effective entry cost.

Aggregate fields include `entry_coverage`, `closing_coverage`, `trade_count`,
`mean_clv`, and `positive_clv_rate`. Use `--include-samples` to keep the
per-forecast rows in the JSON output.

A positive BSS is evidence of skill on the stored sample; it is not a guarantee
of future returns. A negative BSS means the model lost to the market and should
trigger model review, not a trade.

---

## 6. Bounds and failure behaviour

- Page size is capped at 100; pages are capped (default 200).
- Lookback is capped at 7 days; the total backfill window at 365 days.
- Fidelity is capped at 1440 minutes; points are capped at 5000 per outcome.
- Provider calls use the existing `ProviderHttpClient` retry, timeout, and
  circuit-breaker policy.
- Runner capture and backfill failures are logged with structured context and
  skipped; they never invent a price or a settlement outcome.

---

## 7. Known limitations

- Bayse has no historical backfill; only forward snapshots exist.
- A closing line requires a stored observation at or before settlement. If the
  market is illiquid, the last pre-settlement observation may be older than the
  nominal fidelity; the report exposes `closing_observed_at` so this is visible.
- Entry market probability from a forecast snapshot is the decision-time book
  mid, not a trade print. CLV uses the recorded effective entry cost for the
  trade side when available.
- The report is descriptive. It does not mutate calibration parameters or
  promote a model to production.
