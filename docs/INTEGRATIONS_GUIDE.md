# Third-Party Integrations Guide — Prediction Markets & Market Data Feeds

> **Governing Standards:** This document details the technical specifications, API contracts, rate limits, authentication requirements, and normalization rules for external providers. See also [`prediction-intelligence-engineering-rules.md`](file:///Users/mac/prediction-market/prediction-intelligence-engineering-rules.md) and [`prediction-intelligence-platform-technical-plan.md`](file:///Users/mac/prediction-market/prediction-intelligence-platform-technical-plan.md).

---

## 1. Integration Adapter Architecture

All third-party interactions must be wrapped in isolated adapters implementing typed Python protocols. Direct HTTP calls from domain or router modules are strictly prohibited.

```mermaid
flowchart TD
    subgraph CoreDomain ["Core Platform Domain"]
        Orchestrator[Analysis Orchestrator]
        Forecaster[Forecasting Engine]
    end

    subgraph AdapterInterfaces ["Adapter Interfaces (Protocols)"]
        MarketAdapterProto["PredictionMarketAdapter (Protocol)"]
        ExchangeAdapterProto["ExchangeMarketDataAdapter (Protocol)"]
    end

    subgraph Implementations ["Concrete Implementations"]
        PolyAdapter[PolymarketAdapter]
        BayseAdapter[BayseAdapter]
        BinanceAdapter[BinanceAdapter]
        CoinbaseAdapter[CoinbaseAdapter]
    end

    subgraph ExternalAPIs ["External Provider Endpoints"]
        PolyAPI[Polymarket Gamma & CLOB]
        BayseAPI[Bayse Markets API]
        BinanceAPI[Binance REST & WebSocket]
        CoinbaseAPI[Coinbase REST & WebSocket]
    end

    Orchestrator --> MarketAdapterProto
    Forecaster --> ExchangeAdapterProto

    MarketAdapterProto <|.. PolyAdapter
    MarketAdapterProto <|.. BayseAdapter
    ExchangeAdapterProto <|.. BinanceAdapter
    ExchangeAdapterProto <|.. CoinbaseAdapter

    PolyAdapter <--> PolyAPI
    BayseAdapter <--> BayseAPI
    BinanceAdapter <--> BinanceAPI
    CoinbaseAdapter <--> CoinbaseAPI
```

---

## 2. Polymarket Integration Specification

Polymarket utilizes two primary API architectures: the **Gamma API** for event discovery and metadata, and the **CLOB (Central Limit Order Book) API** for executable pricing and order books.

### 2.1 Gamma API (Market Discovery & Metadata)
- **Base URL:** `https://gamma-api.polymarket.com`
- **Authentication:** None required for public read queries.
- **Key Endpoints:**
  - `GET /events?slug={slug}`: Retrieve parent event and associated markets.
  - `GET /markets?id={id}`: Retrieve specific market metadata, token IDs, and resolution rules.
  - `GET /markets?active=true&closed=false&limit=100`: Batch active market discovery.
- **Critical Data Fields to Extract:**
  - `question`: Exact title text.
  - `description`: Formal resolution criteria and settlement terms.
  - `clobTokenIds`: JSON array containing outcome asset IDs for YES (`clobTokenIds[0]`) and NO (`clobTokenIds[1]`).
  - `endDate`: ISO-8601 market trading close timestamp.
  - `resolutionSource`: Primary settlement data source (e.g. UMA Oracle, Binance 1m close).

### 2.2 CLOB API (Order Books & Executable Depth)
- **Base URL:** `https://clob.polymarket.com`
- **Rate Limits:** 100 requests per 10 seconds per IP on public endpoints.
- **Key Endpoints:**
  - `GET /book?token_id={token_id}`: Full Level 2 order book ladder for an outcome token.
  - `GET /midpoint?token_id={token_id}`: Top-of-book midpoint price.
  - `GET /price?token_id={token_id}&side={buy|sell}`: Best executable price.
  - `GET /prices-history?market={condition_id}&interval=1h`: Historical token prices.
- **WebSocket Feed:**
  - **URL:** `wss://ws-subscriptions-clob.polymarket.com/ws/market`
  - **Subscription Message:**
    ```json
    {
      "type": "market",
      "assets_ids": ["<YES_TOKEN_ID>", "<NO_TOKEN_ID>"]
    }
    ```
- **Error Handling & Normalization:**
  - Prices are reported in USD minor units (from $0.00$ to $1.00$).
  - Missing book depth returns an empty array; the adapter must flag `quality_flags.thin_order_book = true`.

---

## 3. Bayse Markets Integration Specification

Bayse provides fixed-odds and order-driven prediction contracts across crypto, finance, and sports.

### 3.1 REST API Overview
- **Base URL:** `https://api.bayse.markets/v1` (subject to canonical documentation update).
- **Authentication:** Header `X-BAYSE-API-KEY: <secret_key>` for private tiers; read-only public endpoints supported.
- **Key Endpoints:**
  - `GET /markets/search?q={query}`: Fuzzy keyword market search.
  - `GET /markets/{market_id}`: Detailed contract terms, expiry, and oracle definitions.
  - `GET /markets/{market_id}/orderbook`: Level 2 order-book snapshot.
  - `GET /markets/{market_id}/trades`: Recent trade execution history.
- **Data Normalization:**
  - Map Bayse outcome identifiers (`outcome_yes`, `outcome_no`) into normalized internal outcome DTOs.
  - Enforce explicit timezone parsing for settlement times.

---

## 4. Cryptocurrency Exchange Market Data (Binance & Coinbase)

To ensure accurate pricing for crypto markets (BTC MVP), the platform ingests high-frequency exchange market data.

### 4.1 Binance API Specification
- **REST Base URL:** `https://api.binance.com/api/v3`
- **WebSocket URL:** `wss://stream.binance.com:9443/ws`
- **Rate Limits:** 1200 request weight per minute.
- **Primary Channels & Endpoints:**
  - `GET /api/v3/klines?symbol=BTCUSDT&interval=1m&limit=120`: Fetch historical 1-minute candles.
  - `GET /api/v3/depth?symbol=BTCUSDT&limit=20`: Snapshot order-book depth.
  - `Stream: btcusdt@bookTicker`: Continuous sub-second best bid/ask and quantities.
  - `Stream: btcusdt@kline_1m`: Real-time 1-minute candle streaming.
- **Normalization Invariant:** Convert Binance `close` strings to `Decimal` before computing returns.

### 4.2 Coinbase Developer Platform API Specification
- **REST Base URL:** `https://api.exchange.coinbase.com`
- **WebSocket Feed:** `wss://ws-feed.exchange.coinbase.com`
- **Key Endpoints:**
  - `GET /products/BTC-USD/candles?granularity=60`: 1-minute historical candles.
  - `GET /products/BTC-USD/book?level=2`: Top 50 levels of bids and asks.
  - `Stream Channel: ticker`: Real-time trade prices and 24h volume.
- **Basis Monitoring:**
  - Calculate basis divergence: $\Delta_{\text{basis}} = |S_{\text{binance}} - S_{\text{coinbase}}|$.
  - If $\Delta_{\text{basis}} > \$250$ on BTC, flag high cross-exchange dispersion in analysis metadata.

---

## 5. Python Adapter Protocol Contract

All prediction market integrations must satisfy this abstract Python protocol (`typing.Protocol`):

```python
from datetime import datetime
from decimal import Decimal
from typing import Protocol, Literal
from pydantic import BaseModel

class NormalizedOutcomeQuote(BaseModel):
    outcome_id: str
    token_id: str
    label: str
    best_bid: Decimal | None
    best_ask: Decimal | None
    mid_price: Decimal | None
    depth_ask_usd: Decimal
    depth_bid_usd: Decimal

class NormalizedOrderBook(BaseModel):
    platform: Literal["polymarket", "bayse"]
    platform_market_id: str
    observed_at: datetime
    quotes: list[NormalizedOutcomeQuote]
    raw_sequence: str | None

class PredictionMarketAdapter(Protocol):
    async def search_markets(self, query: str, limit: int = 20) -> list[dict]: ...
    async def get_market(self, platform_market_id: str) -> dict: ...
    async def get_market_by_slug(self, slug: str) -> dict: ...
    async def get_order_book(self, platform_market_id: str) -> NormalizedOrderBook: ...
    async def get_resolution_rules(self, platform_market_id: str) -> dict: ...
    async def check_health(self) -> bool: ...
```

---

## 6. Contract Testing & Provider Mocking Strategy

To guarantee CI determinism and protect against third-party API rate limits and network outages:
1. **Sanitized Fixtures:** Record real provider JSON payloads and sanitize them of all secrets, user IDs, and private tokens. Store in `backend/tests/fixtures/providers/`.
2. **Contract Regression Tests:** Replay recorded fixtures through adapters to verify schema stability and numeric parsing.
3. **Scheduled Live Health Sweeps:** A scheduled nightly CI workflow tests live provider endpoints against a known test fixture to detect unexpected upstream API schema changes or deprecations.
