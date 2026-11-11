import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from prediction_ai.api.router import api_router
from prediction_ai.core.config import get_settings
from prediction_ai.core.exceptions import DomainException
from prediction_ai.core.logging import setup_logging
from prediction_ai.db.sqlite import get_sqlite_db


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application startup and shutdown event lifecycle."""
    settings = get_settings()
    setup_logging(log_level=settings.LOG_LEVEL)
    # Initialize zero-daemon embedded SQLite database
    sqlite = get_sqlite_db()
    sqlite.initialize()
    # Pre-warm market data DNS cache in background so live analyses experience 0ms DNS latency
    from prediction_ai.integrations.network import prewarm_market_data_dns

    prewarm_task = asyncio.create_task(prewarm_market_data_dns())
    try:
        yield
    finally:
        prewarm_task.cancel()


def create_application() -> FastAPI:
    """Factory creating configured FastAPI instance."""
    settings = get_settings()

    app = FastAPI(
        title="Prediction Intelligence Platform API",
        version="0.1.0",
        description="Probabilistic Forecasting and Decision Engine for Prediction Markets",
        lifespan=lifespan,
    )

    # CORS configuration
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.FRONTEND_URL],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Domain exception handling
    @app.exception_handler(DomainException)
    async def domain_exception_handler(request: Request, exc: DomainException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                }
            },
        )

    # Mount API routers
    app.include_router(api_router)

    return app


app = create_application()
