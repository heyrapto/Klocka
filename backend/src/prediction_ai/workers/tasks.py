import asyncio
from typing import Any
from uuid import UUID

import structlog

from prediction_ai.orchestration.runner import AnalysisWorkflowRunner
from prediction_ai.workers.celery_app import celery_app

logger = structlog.get_logger(__name__)


@celery_app.task(name="prediction_ai.run_analysis", bind=True, max_retries=3)
def run_analysis_pipeline(self: Any, analysis_id: str) -> dict[str, Any]:
    """Execute asynchronous analysis workflow inside Celery worker."""
    logger.info("Executing Celery analysis pipeline", analysis_id=analysis_id, task_id=self.request.id)

    runner = AnalysisWorkflowRunner()
    asyncio.run(runner.run(UUID(analysis_id)))

    return {
        "analysis_id": analysis_id,
        "status": "COMPLETED",
        "task_id": self.request.id,
    }
