# ADR 0000: Monorepo and Technology Stack Selection

- **Status:** Accepted
- **Date:** 2026-11-04
- **Deciders:** Architecture Core Team
- **Consulted:** Frontend, Backend, Machine Learning, Security

---

## 1. Context and Problem Statement

The Prediction Intelligence Platform requires high-performance client interactivity (image uploads, real-time stage progress, interactive probability and decision charts), high-throughput data ingestion (WebSocket market feeds from exchanges and CLOBs), heavy computer vision and OCR pipelines, semantic retrieval over documentary research, and statistical machine learning (GBDTs, probability calibration).

We must decide on:
1. The repository architecture (monorepo vs polyrepo).
2. The frontend web application stack.
3. The core backend API framework and runtime.
4. The persistence, identity, and vector search layer.
5. The asynchronous task queue and distributed caching infrastructure.

---

## 2. Decision Drivers

- **Domain-Specific Runtime Strengths:** Python is the undisputed industry standard for OCR, computer vision, data science, embeddings, and statistical modeling. TypeScript/Next.js is the premier standard for responsive, type-safe, accessible user experiences.
- **Velocity & Cognitive Load:** A unified repository enables shared linting, unified CI/CD, coordinated end-to-end testing, and cohesive versioning without premature distributed system complexity.
- **Data Integrity & Security:** Multi-tenant security requires rock-solid transactional persistence, Row-Level Security (RLS), and unified identity management.
- **Cost & Operational Simplicity:** Minimizing operational overhead in early phases by deploying a cohesive modular monolith rather than premature microservices.

---

## 3. Considered Options

### Repository Topology
- **Option A (Monorepo):** Single git repository with `apps/web/`, `backend/`, `infrastructure/`, and `docs/`.
- **Option B (Polyrepo):** Separate repositories for frontend, backend API, worker service, and ML models.

### Backend Stack
- **Option A (FastAPI + Python 3.12):** Async HTTP APIs, Pydantic v2 type safety, native integration with ML libraries (scikit-learn, LightGBM, PaddleOCR, PyTorch).
- **Option B (Node.js / NestJS / TypeScript):** High I/O performance, but requires multi-process bridges, gRPC, or external microservices to invoke Python ML/OCR pipelines.
- **Option C (Go):** High concurrency, but lacks mature data science, OCR, and ML ecosystems.

### Database & Storage
- **Option A (Supabase Managed PostgreSQL + pgvector + Auth + Storage):** Unified identity (JWT), built-in Row-Level Security (RLS), private S3-compatible object storage, and vector similarity search in a single managed platform.
- **Option B (Separate PostgreSQL, Pinecone/Qdrant, Auth0, AWS S3):** High architectural fragmentation, four separate billing accounts, and complex cross-service synchronization.

---

## 4. Decision Outcome

We choose a **Monorepo Modular Monolith** structured as follows:

1. **Frontend:** **Next.js 15+ (App Router)** with **TypeScript (strict)**, **React Server Components (RSC)**, **TanStack Query v5**, and **Tailwind CSS**.
2. **Backend:** **Python 3.12+ with FastAPI**, using **Pydantic v2** for contract enforcement, **SQLAlchemy 2.x (async)** for database access, and **httpx** for async HTTP clients.
3. **Database & Infrastructure:** **Supabase PostgreSQL** with the **`pgvector`** extension for research document embeddings, **Supabase Auth** for identity, and **Supabase Storage** for private screenshot retention.
4. **Task Orchestration & Cache:** **Redis** for distributed caching, locks, and rate-limiting, coupled with **Celery** for asynchronous task execution across dedicated worker pools.

---

## 5. Consequences & Trade-offs

### Positive
- **Optimal Tooling for Each Task:** Modern React Server Components for UI; Python 3.12 for ML, OCR, and statistical calibration.
- **Single Source of Truth for Data:** PostgreSQL houses transactional data, market metadata, research embeddings, and audit trails without external vector sync drift.
- **Fast Local Development:** Docker Compose orchestrates the API, Redis, Celery workers, and local Supabase instance in a single command.
- **Strict Boundaries:** The modular monolith structure enforces domain decoupling (`api/`, `vision/`, `markets/`, `forecasting/`, `decision/`) without the operational overhead of microservices.

### Negative / Risks & Mitigations
- **Event Loop Blocking in FastAPI:** Python CPU-heavy tasks (OCR, image resizing, feature calculation) can block FastAPI's `asyncio` event loop.
  - *Mitigation:* Heavy CPU tasks are strictly dispatched to Celery background workers; FastAPI handlers execute only non-blocking async I/O.
- **Two Language Ecosystems:** Requires maintaining TypeScript (`pnpm`) and Python (`uv` / `poetry`) toolchains.
  - *Mitigation:* Unified root workspace scripts and containerized CI workflows.

---

## 6. Revisit Triggers

- Market tick ingestion volume exceeds PostgreSQL write capacity ($> 10,000$ events/sec), prompting migration of market data observations to ClickHouse or TimescaleDB.
- GPU inference for local VLM/OCR models demands dedicated auto-scaling Kubernetes worker pods separate from API workers.
