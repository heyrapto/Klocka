# Backend Service — Prediction Intelligence Platform

> **FastAPI + Celery + SQLAlchemy 2.x + Pydantic v2 + Supabase PostgreSQL**

This directory houses the core Python backend service for the Prediction Intelligence Platform. It implements the architecture and engineering standards defined in [`docs/ARCHITECTURE.md`](file:///Users/mac/prediction-market/docs/ARCHITECTURE.md) and [`prediction-intelligence-engineering-rules.md`](file:///Users/mac/prediction-market/prediction-intelligence-engineering-rules.md).

---

## 1. Directory Structure

```text
backend/
├── pyproject.toml              # Build system, dependencies, and tool settings (ruff, mypy, pytest)
├── requirements.txt            # Production dependencies
├── requirements-dev.txt        # Development, testing, and linting dependencies
├── alembic.ini                 # Alembic database migration configuration
├── Dockerfile                  # Container definition for API and Celery workers
├── .env.example                # Template environment variables
├── migrations/                 # Alembic migration scripts
├── tests/                      # Unit, integration, and contract tests
└── src/prediction_ai/          # Application source package
    ├── api/                    # FastAPI route handlers, dependencies, and v1 endpoints
    ├── auth/                   # Supabase JWT validation and user identity extraction
    ├── core/                   # Configuration (pydantic-settings), exceptions, and logging
    ├── db/                     # Async SQLAlchemy sessionmaker and ORM models
    ├── decision/               # Expected Value (EV) calculation and NO_TRADE gating
    ├── forecasting/            # Model inference, baselines, and probability calibration
    ├── ingestion/              # Real-time WebSocket feed consumers (Binance, Coinbase, CLOB)
    ├── integrations/           # Platform adapters (Polymarket, Bayse, Exchanges)
    ├── learning/               # Resolution verification, postmortems, and evaluation
    ├── markets/                # Market matching and authoritative verification
    ├── observability/          # OpenTelemetry tracing and Prometheus metrics
    ├── orchestration/          # State machine coordination and pipeline execution
    ├── research/               # Document ingestion, SSRF filtering, and pgvector retrieval
    ├── schemas/                # Strict Pydantic v2 boundary models
    ├── specialists/            # Domain-specific feature engineering (crypto, BTC)
    ├── vision/                 # PaddleOCR wrapper and VLM structured extraction
    └── workers/                # Celery application and background tasks
```

---

## 2. Local Development & Installation

### Activate Virtual Environment
```bash
cd backend
source .venv/bin/activate
```

### Install Dependencies
```bash
pip install -r requirements-dev.txt
pip install -e .
```

### Configure Environment Variables
```bash
cp .env.example .env
# Edit .env with your local Supabase and Redis credentials
```

---

## 3. Running Services

### Start FastAPI Dev Server
```bash
uvicorn prediction_ai.main:app --host 0.0.0.0 --port 8000 --reload
```
API Documentation will be accessible at:
- Swagger UI: `http://localhost:8000/docs`
- OpenAPI JSON: `http://localhost:8000/openapi.json`

### Start Celery Analysis Worker
```bash
celery -A prediction_ai.workers.celery_app worker --loglevel=info --concurrency=4
```

---

## 4. Running Tests, Linters & Type Checks

```bash
# Run unit tests
pytest

# Run tests with coverage
pytest --cov=prediction_ai --cov-report=term-missing

# Run Ruff linter and formatter check
ruff check .
ruff format --check .

# Run strict Mypy type checker
mypy src
```
