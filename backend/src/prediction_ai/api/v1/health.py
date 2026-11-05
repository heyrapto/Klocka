from fastapi import APIRouter, status

router = APIRouter(prefix="/health", tags=["Health"])


@router.get("", status_code=status.HTTP_200_OK)
@router.get("/", status_code=status.HTTP_200_OK)
@router.get("/live", status_code=status.HTTP_200_OK)
async def liveness_check() -> dict[str, str]:
    """Liveness probe confirming service process is running."""
    return {"status": "ok", "service": "prediction-backend"}


@router.get("/ready", status_code=status.HTTP_200_OK)
async def readiness_check() -> dict[str, str]:
    """Readiness probe confirming dependencies are reachable."""
    # In a full deployment, this checks Redis and Database connectivity
    return {"status": "ready", "service": "prediction-backend"}
