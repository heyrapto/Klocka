from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve .env relative to this file's location (backend/.env) so the
# server can be started from any working directory and always finds its
# environment variables.
# parents[0]=core, parents[1]=prediction_ai, parents[2]=src, parents[3]=backend
_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


class Settings(BaseSettings):
    """Validated application configuration loaded once at process start."""

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # ------------------------------------------------------------------
    # Application
    # ------------------------------------------------------------------
    ENVIRONMENT: Literal["development", "staging", "production", "test"] = "development"
    APP_NAME: str = "Prediction Intelligence API"
    APP_VERSION: str = "0.2.0"
    LOG_LEVEL: str = "INFO"
    LOG_JSON: bool = True
    DEBUG: bool = False
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000
    FRONTEND_URL: str = "http://localhost:3000"
    CORS_ORIGINS: str = "http://localhost:3000,http://127.0.0.1:3000"
    REQUEST_ID_HEADER: str = "X-Request-ID"

    # ------------------------------------------------------------------
    # Supabase identity (authentication is optional only outside production)
    # ------------------------------------------------------------------
    AUTH_REQUIRED: bool = False
    DEV_USER_ID: str = "00000000-0000-0000-0000-000000000001"
    SUPABASE_URL: str | None = None
    SUPABASE_JWT_SECRET: SecretStr | None = None
    SUPABASE_JWT_AUDIENCE: str = "authenticated"
    SUPABASE_SERVICE_ROLE_KEY: SecretStr | None = None

    # ------------------------------------------------------------------
    # Database (SQLite Primary & Zero-Daemon Local Storage)
    # ------------------------------------------------------------------
    SQLITE_DB_PATH: str = "./var/prediction_intelligence.db"
    MONGODB_URL: str = "mongodb://localhost:27017"
    MONGODB_DB_NAME: str = "prediction_intelligence"
    MONGODB_SERVER_SELECTION_TIMEOUT_MS: int = 1500
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:54322/postgres"
    DATABASE_POOL_SIZE: int = Field(default=10, ge=1, le=100)
    DATABASE_MAX_OVERFLOW: int = Field(default=20, ge=0, le=200)
    DATABASE_POOL_RECYCLE_SECONDS: int = Field(default=1800, ge=60)
    DATABASE_COMMAND_TIMEOUT_SECONDS: float = Field(default=15.0, gt=0)

    # ------------------------------------------------------------------
    # Redis / Celery
    # ------------------------------------------------------------------
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: str = "redis://localhost:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/2"
    ANALYSIS_TASK_MODE: Literal["celery", "inline"] = "inline"
    ANALYSIS_TIMEOUT_SECONDS: int = Field(default=180, gt=0)
    ANALYSIS_TASK_SOFT_TIME_LIMIT_SECONDS: int = Field(default=240, gt=0)
    ANALYSIS_TASK_TIME_LIMIT_SECONDS: int = Field(default=300, gt=0)

    # ------------------------------------------------------------------
    # Storage
    # ------------------------------------------------------------------
    STORAGE_BACKEND: Literal["local", "supabase"] = "local"
    STORAGE_LOCAL_DIR: str = "./var/screenshots"
    SUPABASE_STORAGE_BUCKET: str = "screenshots"

    # ------------------------------------------------------------------
    # Uploads
    # ------------------------------------------------------------------
    MAX_UPLOAD_BYTES: int = Field(default=10 * 1024 * 1024, gt=0)
    MAX_IMAGE_PIXELS: int = Field(default=40_000_000, gt=0)
    MAX_IMAGE_EDGE: int = Field(default=6000, gt=0)

    # ------------------------------------------------------------------
    # Vision & LLM Providers (Gemini / OpenAI)
    # ------------------------------------------------------------------
    VISION_PROVIDER: Literal["disabled", "openai", "gemini"] = "gemini"
    GEMINI_API_KEY: SecretStr | None = None
    GEMINI_MODEL: str = "gemini-3.8-flash"
    GEMINI_VISION_MODEL: str = "gemini-3.8-flash"
    GEMINI_TIMEOUT_SECONDS: float = Field(default=60.0, gt=0)
    VISION_API_BASE_URL: str = "https://api.openai.com/v1"
    VISION_API_KEY: SecretStr | None = None
    VISION_MODEL: str = "gpt-4o-mini"
    VISION_TIMEOUT_SECONDS: float = Field(default=60.0, gt=0)
    VISION_MAX_OUTPUT_TOKENS: int = Field(default=1600, gt=0)
    VISION_MAX_IMAGE_EDGE: int = Field(default=1600, gt=0)
    VISION_PROMPT_VERSION: str = "vision_extract_v1"

    # ------------------------------------------------------------------
    # Prediction market providers
    # ------------------------------------------------------------------
    POLYMARKET_GAMMA_API_URL: str = "https://gamma-api.polymarket.com"
    POLYMARKET_CLOB_API_URL: str = "https://clob.polymarket.com"
    POLYMARKET_SITE_URL: str = "https://polymarket.com"
    BAYSE_API_URL: str = "https://relay.bayse.markets"
    BAYSE_SITE_URL: str = "https://www.bayse.markets"
    BAYSE_PUBLIC_KEY: SecretStr | None = None
    PROVIDER_TIMEOUT_SECONDS: float = Field(default=12.0, gt=0)
    PROVIDER_MAX_RETRIES: int = Field(default=2, ge=0, le=5)
    PROVIDER_MAX_RESPONSE_BYTES: int = Field(default=2_000_000, gt=0)
    PROVIDER_CIRCUIT_FAILURE_THRESHOLD: int = Field(default=5, ge=1)
    PROVIDER_CIRCUIT_RESET_SECONDS: float = Field(default=30.0, gt=0)
    MARKET_SEARCH_LIMIT: int = Field(default=10, ge=1, le=50)
    MARKET_MATCH_MIN_SCORE: float = Field(default=0.86, ge=0.0, le=1.0)
    MARKET_MATCH_MIN_MARGIN: float = Field(default=0.06, ge=0.0, le=1.0)

    # ------------------------------------------------------------------
    # External market-data transport
    # ------------------------------------------------------------------
    # The system resolver is always tried first.  DoH is a fallback for
    # networks where DNS for financial APIs is filtered, broken, or slow.
    MARKET_DATA_DNS_FALLBACK_ENABLED: bool = True
    # IP-literal endpoints avoid a DNS bootstrap loop when the system resolver
    # is the component that has failed.  Hostname endpoints may be added when a
    # deployment requires them (and can already resolve their own hostnames).
    MARKET_DATA_DOH_ENDPOINTS: str = (
        "https://8.8.8.8/resolve,"
        "https://1.1.1.1/dns-query"
    )
    MARKET_DATA_DNS_SYSTEM_TIMEOUT_SECONDS: float = Field(default=1.5, gt=0.0, le=10.0)
    MARKET_DATA_DNS_DOH_TIMEOUT_SECONDS: float = Field(default=2.5, gt=0.0, le=10.0)
    MARKET_DATA_DNS_CACHE_TTL_SECONDS: int = Field(default=300, ge=30, le=3600)
    MARKET_DATA_DNS_NEGATIVE_CACHE_SECONDS: int = Field(default=30, ge=5, le=300)
    MARKET_DATA_CONNECT_TIMEOUT_SECONDS: float = Field(default=4.0, gt=0.0, le=30.0)
    MARKET_DATA_READ_TIMEOUT_SECONDS: float = Field(default=8.0, gt=0.0, le=60.0)
    MARKET_DATA_GROUP_BUDGET_SECONDS: float = Field(default=10.0, gt=0.0, le=60.0)

    # ------------------------------------------------------------------
    # Forecasting
    # ------------------------------------------------------------------
    FORECAST_MODEL_VERSION: str = "market_baseline_v1"
    FORECAST_MAX_INTERVAL_HALF_WIDTH: float = Field(default=0.25, gt=0.0, le=0.5)
    FORECAST_BLEND_VERSION: str = "linear_normalized_v2"
    # Crypto contracts have a grounded mathematical anchor.  The market remains
    # the largest single component because the crowd is a strong calibrated
    # baseline; the LLM is deliberately a small complement.
    FORECAST_CRYPTO_MATH_WEIGHT: float = Field(default=0.40, ge=0.0, le=1.0)
    FORECAST_CRYPTO_LLM_WEIGHT: float = Field(default=0.10, ge=0.0, le=1.0)
    FORECAST_CRYPTO_MARKET_WEIGHT: float = Field(default=0.50, ge=0.0, le=1.0)
    FORECAST_GENERAL_LLM_WEIGHT: float = Field(default=0.35, ge=0.0, le=1.0)
    FORECAST_GENERAL_MARKET_WEIGHT: float = Field(default=0.65, ge=0.0, le=1.0)
    # When true, crypto contracts with unresolved oracle/comparator/strike
    # semantics are blocked at the decision layer instead of being traded.
    CONTRACT_SPEC_STRICT_CRYPTO: bool = True
    RESEARCH_ENABLED: bool = False

    # ------------------------------------------------------------------
    # Historical market prices
    # ------------------------------------------------------------------
    # Bounds for provider price-history ingestion.  The CLOB endpoint returns
    # at most one point per fidelity minute, so a 5,000-point cap covers a
    # multi-day window without permitting unbounded responses.
    PRICE_HISTORY_MAX_POINTS: int = Field(default=5000, ge=1, le=20_000)
    PRICE_HISTORY_DEFAULT_FIDELITY_MINUTES: int = Field(default=1, ge=1, le=1440)

    # ------------------------------------------------------------------
    # Research runtime
    # ------------------------------------------------------------------
    RESEARCH_SOURCES_REGISTRY_PATH: str = "config/sources_registry.json"
    RESEARCH_JOB_TIMEOUT_SECONDS: int = Field(default=300, gt=0)
    RESEARCH_JOB_SOFT_TIME_LIMIT_SECONDS: int = Field(default=240, gt=0)
    RESEARCH_MAX_DOCUMENTS: int = Field(default=20, ge=1, le=200)
    RESEARCH_MAX_CLAIMS_PER_DOC: int = Field(default=10, ge=1, le=100)
    RESEARCH_OUTBOX_POLL_INTERVAL_SECONDS: float = Field(default=5.0, gt=0.0)
    RESEARCH_OUTBOX_BATCH_SIZE: int = Field(default=20, ge=1, le=100)

    # ------------------------------------------------------------------
    # Decision policy
    # ------------------------------------------------------------------
    DECISION_MIN_EDGE: float = Field(default=0.04, ge=0.0, le=1.0)
    DECISION_MAX_SPREAD: float = Field(default=0.05, ge=0.0, le=1.0)
    DECISION_MIN_DEPTH_USD: float = Field(default=100.0, gt=0.0)
    DECISION_TARGET_ORDER_USD: float = Field(default=100.0, gt=0.0)
    DECISION_UNCERTAINTY_MARGIN: float = Field(default=0.02, ge=0.0, le=1.0)
    DECISION_SLIPPAGE_BPS: int = Field(default=15, ge=0, le=1000)
    DECISION_KELLY_FRACTION: float = Field(default=0.25, gt=0.0, le=1.0)
    # 60 s gives enough headroom for the full multi-venue API call chain
    # (spot fetch → order book → decision) without triggering STALE_QUOTE
    # abstention on every real-world analysis.
    DECISION_MAX_QUOTE_AGE_SECONDS: float = Field(default=60.0, gt=0.0)
    DECISION_POLICY_VERSION: str = "decision_policy_v1"

    # ------------------------------------------------------------------
    # API protection
    # ------------------------------------------------------------------
    RATE_LIMIT_ANALYSES_PER_10_MIN: int = Field(default=5, ge=1)
    RATE_LIMIT_ANALYSES_PER_DAY: int = Field(default=100, ge=1)
    SSE_POLL_INTERVAL_SECONDS: float = Field(default=1.0, gt=0.0)
    SSE_HEARTBEAT_SECONDS: float = Field(default=15.0, gt=0.0)

    @property
    def cors_origins(self) -> list[str]:
        origins = [item.strip() for item in self.CORS_ORIGINS.split(",") if item.strip()]
        if self.FRONTEND_URL and self.FRONTEND_URL not in origins:
            origins.append(self.FRONTEND_URL)
        return origins

    @property
    def jwks_url(self) -> str | None:
        if not self.SUPABASE_URL:
            return None
        return f"{self.SUPABASE_URL.rstrip('/')}/auth/v1/.well-known/jwks.json"

    @property
    def jwt_issuer(self) -> str | None:
        if not self.SUPABASE_URL:
            return None
        return f"{self.SUPABASE_URL.rstrip('/')}/auth/v1"

    @model_validator(mode="after")
    def enforce_production_controls(self) -> "Settings":
        if self.ENVIRONMENT == "production":
            if not self.AUTH_REQUIRED:
                raise ValueError("AUTH_REQUIRED must be true in production.")
            if self.ANALYSIS_TASK_MODE != "celery":
                raise ValueError("ANALYSIS_TASK_MODE must be 'celery' in production.")
            if self.DEBUG:
                raise ValueError("DEBUG must be false in production.")
        if self.AUTH_REQUIRED:
            if not self.SUPABASE_URL and not self.SUPABASE_JWT_SECRET:
                raise ValueError(
                    "AUTH_REQUIRED needs SUPABASE_URL (JWKS) or SUPABASE_JWT_SECRET."
                )
        if self.VISION_PROVIDER == "openai" and not self.VISION_API_KEY:
            raise ValueError("VISION_API_KEY is required when VISION_PROVIDER=openai.")
        if (
            self.VISION_PROVIDER == "gemini"
            and self.ENVIRONMENT == "production"
            and not self.GEMINI_API_KEY
        ):
            raise ValueError("GEMINI_API_KEY is required in production when VISION_PROVIDER=gemini.")
        if self.STORAGE_BACKEND == "supabase":
            if not self.SUPABASE_URL or not self.SUPABASE_SERVICE_ROLE_KEY:
                raise ValueError(
                    "Supabase storage needs SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY."
                )

        crypto_total = (
            self.FORECAST_CRYPTO_MATH_WEIGHT
            + self.FORECAST_CRYPTO_LLM_WEIGHT
            + self.FORECAST_CRYPTO_MARKET_WEIGHT
        )
        if abs(crypto_total - 1.0) > 1e-9:
            raise ValueError(
                "FORECAST_CRYPTO_* weights must sum to 1.0 "
                f"(got {crypto_total:.6f})."
            )
        general_total = (
            self.FORECAST_GENERAL_LLM_WEIGHT + self.FORECAST_GENERAL_MARKET_WEIGHT
        )
        if abs(general_total - 1.0) > 1e-9:
            raise ValueError(
                "FORECAST_GENERAL_* weights must sum to 1.0 "
                f"(got {general_total:.6f})."
            )
        return self


@lru_cache
def get_settings() -> Settings:
    """Return the cached validated settings singleton."""
    return Settings()
