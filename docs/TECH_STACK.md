# Technology Stack Specification — Prediction Intelligence Platform

> **Governing Standards:** This document details the technology choices, library dependencies, runtime environments, and infrastructure topology for the platform. See also [`prediction-intelligence-engineering-rules.md`](file:///Users/mac/prediction-market/prediction-intelligence-engineering-rules.md) and [`prediction-intelligence-platform-technical-plan.md`](file:///Users/mac/prediction-market/prediction-intelligence-platform-technical-plan.md).

---

## 1. Stack Topology & Component Matrix

| Layer | Primary Technology | Version | Key Dependencies | Primary Role |
|---|---|---|---|---|
| **Frontend Application** | Next.js (App Router) | `16.x` | TypeScript 5.x, React 19, TanStack Query v5, Tailwind CSS v4, Lucide React, Zod | Responsive UI, Server Components, SSE client, accessible user dashboard |
| **Backend API Gateway** | FastAPI / Python | `3.12+` | Pydantic v2, Uvicorn, httpx, structlog, google-genai, transformers, torch | Async HTTP routing, auth validation, SSE broadcasting, job dispatch |
| **Document & Feedback Store**| MongoDB | `7.0+` | `motor` (3.7+), `pymongo` (4.19+) | Primary document database: analyses, multimodal forecasts, order books, and self-improving feedback loop |
| **Object Storage** | Local / S3 API | S3 Compatible | aiofiles, Pillow, httpx | Private encrypted storage for raw screenshots and research files |
| **Vision & Image Captioning**| HuggingFace BLIP (Pre-trained) | `transformers 5.x` | PyTorch, Pillow, `Salesforce/blip-image-captioning-base` | Pre-trained local captioning model describing screenshots into natural language text |
| **Multimodal Synthesis** | Google Gemini API | `google-genai 2.x` | `gemini-3.8-flash` / `gemini-3.8-flash` | Multimodal VLM contract extraction, documentary synthesis, and calibrated probabilistic forecasting |
| **Domain Research Specialists**| Specialist Context Tools | Native Python | httpx, Binance Spot API, domain momentum oscillators | 15-minute interval movement pattern studies, RSI, order book depth imbalance, and historical postmortems |
| **Observability** | Structured Logging | structlog 25.x | OpenTelemetry SDK, structlog | JSON tracing across API, workers, and external provider calls |

---

## 2. Frontend Application Stack (`apps/web`)

### 2.1 Framework & Architecture
- **Next.js 15+ App Router:** Leveraging React Server Components (RSC) for data-heavy read screens (forecast detail, market rules, historical logs) to minimize client JavaScript bundle size.
- **Client Components (`"use client"`):** Restricted strictly to interactive boundaries: screenshot drag-and-drop zone, live SSE stage progress listener, interactive probability calibration curves, and user feedback forms.
- **Strict TypeScript:** Configured with `strict: true`, `noImplicitAny: true`, and `exactOptionalPropertyTypes: true`. Zero `any` policy.

### 2.2 Client State & Data Fetching
- **TanStack Query v5 (`@tanstack/react-query`):** Manages server-state caching, automatic refetch on window focus (configured per route), query invalidation, and optimistic mutations.
  - Cache keys are normalized and scoped: `['analyses', user.id, analysisId]`, `['forecasts', user.id, { category: 'crypto' }]`.
- **Form & Input Validation:** **Zod** paired with **React Hook Form**. All schemas replicate backend Pydantic models for immediate client feedback, while the backend remains authoritative.
- **Real-Time Client:** Native browser `EventSource` wrapped in an auto-reconnecting client supporting exponential backoff, jitter, and `Last-Event-ID` tracking.

### 2.3 Styling & UI System
- **Tailwind CSS v4:** Modern utility-first styling with strict design token configuration for dark/light themes.
- **Component Primitives:** Accessible, headless primitives (Radix UI / Shadcn patterns) ensuring full WCAG 2.1 AA accessibility (focus rings, keyboard navigation, screen reader ARIA live regions for SSE progress updates).
- **Visualization:** **Recharts** or lightweight SVG primitives for rendering probability distributions, order-book depth ladders, and calibration reliability diagrams.

---

## 3. Backend Service Stack (`backend`)

### 3.1 Runtime & API Gateway
- **Python 3.12+:** Utilizing native asyncio performance improvements, improved exception groups, and type parameter syntax (`type Alias = ...`).
- **FastAPI:** High-performance asynchronous REST API framework.
  - Dependency injection for database sessions, Redis connections, and verified user identities.
  - OpenAPI 3.1 schema auto-generation used to validate frontend client contracts.
- **Pydantic v2:** Rust-backed validation core (`pydantic-core`) providing $> 10\times$ faster serialization and strict runtime typing:
  - Custom decimal encoders for currency precision.
  - Strict validators enforcing bounds $[0, 1]$ on probabilities.

### 3.2 Asynchronous Worker & Messaging System
- **Celery 5.4+ with Redis 7.2+:** 
  - Worker concurrency tuned to available cores and I/O profiles.
  - Task results expire in 1 hour; durable state is immediately committed to PostgreSQL.
  - Dedicated worker queues to prevent resource starvation:
    - `analysis.fifo`: High-priority user screenshot orchestration.
    - `vision.heavy`: Memory-bounded OCR and image processing.
    - `ingest.stream`: High-frequency exchange and market data streams.
    - `research.batch`: Document retrieval, chunking, and embedding.
- **Tenacity:** Resilient retry library for external network operations with jittered exponential backoff.
- **Distributed Locks:** Redis Redlock / single-flight distributed locks ensuring identical market fetches and duplicate analysis requests are coalesced.

---

## 4. Database & Storage Architecture

### 4.1 MongoDB Primary Document Store
- **Document Model Authority:** MongoDB is the primary document database for the platform, accessed via `motor.motor_asyncio` and `pymongo`. It accommodates rich nested multimodal analyses, CLOB order-book depth snapshots, documentary evidence, and postmortem feedback documents.
- **Resilient Fallback Mode:** The database layer implements transparent in-memory dev fallback when running offline or without an active local MongoDB daemon on `localhost:27017`.
- **Primary Collections:**
  - `analyses`: Stores screenshot metadata, stage events, BLIP visual captions, verification candidates, and links to forecasts.
  - `markets`: Normalized prediction contracts verified against Polymarket (Gamma/CLOB) and Bayse APIs.
  - `forecasts`: Immutable calibrated forecasts with model metadata, uncertainty intervals, EV decisions, and itemized citations.
  - `feedback`: Stores post-settlement actual trade outcomes (YES/NO), AI postmortem reflections, and key generalized learnings for continuous improvement.

### 4.2 Local & S3 Storage
- **Screenshot Storage:** Local directory (`./var/screenshots`) or S3-compatible private object storage with server-side encryption.
- **Image Sanitization:** Image uploads are stripped of EXIF metadata and normalized to WebP before ingestion.

---

## 5. Machine Learning, Computer Vision & Multimodal Foundation Models

> **Crucial Architecture Decision:** We do NOT train foundation ML models from scratch. Instead, we utilize high-performance pre-trained vision models and industry-leading multimodal foundation models:

### 5.1 Pre-Trained Local Image Captioning (BLIP)
- **Model:** `Salesforce/blip-image-captioning-base` via HuggingFace `transformers` and `torch`.
- **Role:** Automatically downloads and caches pre-trained weights (~900MB). Transcribes screenshot images into natural language scene descriptions entirely locally.
- **Concurrency:** Executed via worker threadpools (`anyio.to_thread.run_sync`) to prevent blocking the async FastAPI event loop.

### 5.2 Multimodal Reasoning & Foundation Models (Gemini)
- **Provider:** Google Gemini API via official `google-genai` SDK (`client.aio.models.generate_content`).
- **Model:** `gemini-3.8-flash` / `gemini-3.8-flash` with structured JSON schema enforcement (`GeminiForecastOutput`).
- **Input Feeds:** Verified contract terms, official resolution criteria, real-time CLOB order-book spreads, BLIP visual scene caption, specialist domain research, and recent postmortem lessons.

### 5.3 Specialist Domain Research & Continuous Improvement Loop
- **Crypto Specialist Tools:** For short-horizon contracts (e.g. 15-minute BTC price brackets), gathers interval trend patterns, 15m RSI oscillators, order-book depth bid/ask ratios, Binance spot reference prices, and social media prediction consensus.
- **Continuous Learning Loop:** After each trade candle settles, the user reports the actual real-world outcome via `POST /api/v1/analyses/{analysis_id}/feedback`. The system performs an automated AI postmortem, extracts generalized principles, and stores them in the MongoDB `feedback` collection. Future forecasts automatically query and ingest prior learnings to continually improve accuracy towards near-100%.

---

## 6. Observability, Logging & Operations

### 6.1 Structured Logging
- **`structlog`:** JSON-formatted structured logging in production environments, ensuring uniform metadata propagation:
  ```json
  {
    "timestamp": "2026-11-04T10:40:00.123Z",
    "level": "info",
    "event": "analysis_stage_completed",
    "service": "prediction-backend",
    "stage": "MARKET_VERIFIED",
    "analysis_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
    "market_id": "poly_btc_100k_nov",
    "duration_ms": 342,
    "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736"
  }
  ```

### 6.2 Metrics & Distributed Tracing
- **OpenTelemetry SDK:** Instruments FastAPI endpoints, Celery workers, HTTP client calls (`httpx`), and PostgreSQL queries.
- **Prometheus Metrics Exporter:** Exposes:
  - `http_requests_total` & `http_request_duration_seconds` (p50, p95, p99).
  - `celery_queue_depth` & `celery_task_duration_seconds`.
  - `market_feed_staleness_seconds` (critical alert threshold $> 5\text{s}$).
  - `llm_token_usage_total` & `llm_cost_estimated_usd`.
  - `brier_score_rolling_mean` by domain and horizon.

---

## 7. Local Development & CI Environment

- **Containerization:** `compose.yaml` spinning up:
  - `api`: FastAPI dev server with hot-reload.
  - `worker`: Celery worker with task auto-reload.
  - `redis`: Redis 7.2 Alpine.
  - `supabase-local`: Local Supabase stack (PostgreSQL, Auth, Storage, Studio).
- **Package Management:**
  - Frontend: `pnpm` with locked dependencies (`pnpm-lock.yaml`).
  - Backend: `uv` or `poetry` with locked dependencies.
- **Static Analysis & Linting:**
  - TypeScript: `eslint`, `tsc --noEmit`.
  - Python: `ruff` (linter and formatter), `mypy` (strict type checking).
