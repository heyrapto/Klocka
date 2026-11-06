"""Transactional outbox dispatcher for research jobs.

Polls ``research_job_outbox`` for pending rows and dispatches them to the
Celery worker via ``prediction_ai.run_research_job``.  Runs as a lightweight
asyncio loop — typically started by a FastAPI lifespan task or a standalone
``asyncio.run(run_dispatcher())`` call.

Design rules:
- Exactly-once delivery is guaranteed by the SKIP LOCKED advisory lock pattern:
  two dispatcher processes will never pick up the same row.
- Each dispatch attempt is wrapped in its own DB transaction so a Celery send
  failure only marks that row as failed, not the whole batch.
- Never crashes the process on a single dispatch failure — logs the error and
  continues to the next row.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import structlog

from prediction_ai.core.config import get_settings
from prediction_ai.db.session import get_database
from prediction_ai.workers.celery_app import celery_app

logger = structlog.get_logger(__name__)

_TASK_NAME = "prediction_ai.run_research_job"


async def dispatch_once() -> int:
    """Fetch one batch of pending outbox rows and dispatch them to Celery.

    Returns the number of rows successfully dispatched.
    """
    settings = get_settings()
    db_factory = get_database()
    dispatched = 0

    async with db_factory.session() as session:
        from sqlalchemy import text

        # SELECT … FOR UPDATE SKIP LOCKED prevents double-dispatch when multiple
        # dispatcher instances run concurrently.
        result = await session.execute(
            text(
                """
                SELECT outbox_id, job_id
                FROM research_job_outbox
                WHERE status = 'pending'
                ORDER BY created_at ASC
                LIMIT :batch_size
                FOR UPDATE SKIP LOCKED
                """
            ),
            {"batch_size": settings.RESEARCH_OUTBOX_BATCH_SIZE},
        )
        rows = result.mappings().all()

        for row in rows:
            outbox_id = str(row["outbox_id"])
            job_id = str(row["job_id"])
            now = datetime.now(UTC)
            try:
                celery_app.send_task(_TASK_NAME, args=[job_id])
                await session.execute(
                    text(
                        """
                        UPDATE research_job_outbox
                        SET status = 'published', published_at = :now
                        WHERE outbox_id = :outbox_id
                        """
                    ),
                    {"outbox_id": outbox_id, "now": now},
                )
                dispatched += 1
                logger.info(
                    "Research job dispatched to Celery",
                    job_id=job_id,
                    outbox_id=outbox_id,
                )
            except Exception as exc:
                logger.error(
                    "Failed to dispatch research job",
                    job_id=job_id,
                    outbox_id=outbox_id,
                    error=str(exc),
                )
                await session.execute(
                    text(
                        """
                        UPDATE research_job_outbox
                        SET status = 'failed', error_detail = :err
                        WHERE outbox_id = :outbox_id
                        """
                    ),
                    {"outbox_id": outbox_id, "err": str(exc)[:1024]},
                )

        await session.commit()

    return dispatched


async def run_dispatcher() -> None:
    """Long-running async loop that continuously polls the outbox.

    Intended to be started as a background task by the FastAPI lifespan
    or as an ``asyncio.run()`` entry point in a standalone process.
    """
    settings = get_settings()
    logger.info("Research outbox dispatcher started")

    while True:
        try:
            dispatched = await dispatch_once()
            if dispatched:
                logger.debug("Outbox batch dispatched", count=dispatched)
        except Exception as exc:
            logger.error("Outbox dispatcher error", error=str(exc))

        await asyncio.sleep(settings.RESEARCH_OUTBOX_POLL_INTERVAL_SECONDS)
