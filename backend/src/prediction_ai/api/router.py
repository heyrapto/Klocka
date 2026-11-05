from fastapi import APIRouter

from prediction_ai.api.v1 import analyses, forecasts, health, markets

api_router = APIRouter(prefix="/api/v1")

# Mount v1 routes
api_router.include_router(health.router)
api_router.include_router(analyses.router)
api_router.include_router(forecasts.router)
api_router.include_router(markets.router)
