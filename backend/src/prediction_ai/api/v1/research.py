"""Research job and evidence pack API routes.

POST /api/v1/research-jobs/        → Enqueue a new research job (202)
GET  /api/v1/research-jobs/{id}    → Fetch job status and metadata
POST /api/v1/research-jobs/{id}/cancel → Request cancellation
GET  /api/v1/research-jobs/{id}/events → SSE stream of job events

GET  /api/v1/evidence-packs/{id}   → Fetch assembled evidence pack with paginated items
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, AsyncGenerator

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import UUID4, BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from prediction_ai.api.deps import get_current_user, get_db
from prediction_ai.auth.jwt import AuthenticatedUser
from prediction_ai.core.config import get_settings

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Sub-routers
# ---------------------------------------------------------------------------

research_router = APIRouter(tags=["research-jobs"])
evidence_router = APIRouter(tags=["evidence-packs"])

# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class ResearchJobCreate(BaseModel):
    """Payload to enqueue a new research job."""

    market_title: str = Field(..., min_length=3, max_length=1024)
    platform: str = Field(..., min_length=1, max_length=64)
    platform_market_id: str = Field(..., min_length=1, max_length=256)
    category: str = Field(default="general", max_length=64)
    closes_at: datetime | None = None
    resolution_criteria: str | None = Field(default=None, max_length=4096)
    budget_max_tokens: int = Field(default=50_000, ge=1_000, le=500_000)
    budget_max_documents: int = Field(default=20, ge=1, le=200)


class ResearchJobCreated(BaseModel):
    job_id: UUID4
    status: str
    tenant_id: UUID4
    created_at: datetime
    message: str


class ResearchJobView(BaseModel):
    job_id: UUID4
    tenant_id: UUID4
    status: str
    market_title: str
    platform: str
    platform_market_id: str
    category: str
    closes_at: datetime | None
    pack_id: UUID4 | None
    created_at: datetime
    updated_at: datetime
    error_message: str | None


class EvidencePackItemView(BaseModel):
    item_type: str
    item_ref_id: str
    ordinal: int


class EvidencePackView(BaseModel):
    pack_id: UUID4
    job_id: UUID4
    pack_hash: str
    readiness: str
    reasons: list[str]
    cutoff_at: datetime
    created_at: datetime
    items: list[EvidencePackItemView]
    next_cursor: str | None


# ---------------------------------------------------------------------------
# Research job endpoints
# ---------------------------------------------------------------------------


@research_router.post(
    "/",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ResearchJobCreated,
    summary="Enqueue a research job",
)
async def create_research_job(
    body: ResearchJobCreate,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[AsyncSession | None, Depends(get_db)],
) -> ResearchJobCreated:
    """Create a research job and add it to the outbox for the Celery worker."""
    if db is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database is unavailable — cannot create research job.",
        )

    job_id = uuid.uuid4()
    tenant_id = uuid.UUID(current_user.user_id)
    now = datetime.now(UTC)

    async with db.begin():
        await db.execute(
            text(
                """
                INSERT INTO research_jobs (
                    job_id, tenant_id, status, market_title, platform,
                    platform_market_id, category, closes_at,
                    resolution_criteria, budget_max_tokens,
                    budget_max_documents, created_at, updated_at
                ) VALUES (
                    :job_id, :tenant_id, 'QUEUED', :market_title, :platform,
                    :platform_market_id, :category, :closes_at,
                    :resolution_criteria, :budget_max_tokens,
                    :budget_max_documents, :now, :now
                )
                """
            ),
            {
                "job_id": str(job_id),
                "tenant_id": str(tenant_id),
                "market_title": body.market_title,
                "platform": body.platform,
                "platform_market_id": body.platform_market_id,
                "category": body.category,
                "closes_at": body.closes_at,
                "resolution_criteria": body.resolution_criteria,
                "budget_max_tokens": body.budget_max_tokens,
                "budget_max_documents": body.budget_max_documents,
                "now": now,
            },
        )
        # Insert into transactional outbox in same transaction
        await db.execute(
            text(
                """
                INSERT INTO research_job_outbox (
                    outbox_id, job_id, status, created_at
                ) VALUES (
                    :outbox_id, :job_id, 'pending', :now
                )
                """
            ),
            {
                "outbox_id": str(uuid.uuid4()),
                "job_id": str(job_id),
                "now": now,
            },
        )

    logger.info(
        "Research job enqueued",
        job_id=str(job_id),
        tenant_id=str(tenant_id),
        market_title=body.market_title,
    )

    return ResearchJobCreated(
        job_id=job_id,
        status="QUEUED",
        tenant_id=tenant_id,
        created_at=now,
        message="Research job accepted. It will be dispatched to the worker within seconds.",
    )


@research_router.get(
    "/{job_id}",
    response_model=ResearchJobView,
    summary="Get research job status",
)
async def get_research_job(
    job_id: UUID4,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[AsyncSession | None, Depends(get_db)],
) -> ResearchJobView:
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable.")

    result = await db.execute(
        text(
            """
            SELECT job_id, tenant_id, status, market_title, platform,
                   platform_market_id, category, closes_at, pack_id,
                   created_at, updated_at, error_message
            FROM research_jobs
            WHERE job_id = :job_id AND tenant_id = :tenant_id
            """
        ),
        {"job_id": str(job_id), "tenant_id": current_user.user_id},
    )
    row = result.mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Research job not found.")

    return ResearchJobView(
        job_id=row["job_id"],
        tenant_id=row["tenant_id"],
        status=row["status"],
        market_title=row["market_title"],
        platform=row["platform"],
        platform_market_id=row["platform_market_id"],
        category=row["category"],
        closes_at=row["closes_at"],
        pack_id=row["pack_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        error_message=row["error_message"],
    )


@research_router.post(
    "/{job_id}/cancel",
    status_code=status.HTTP_200_OK,
    summary="Cancel a research job",
)
async def cancel_research_job(
    job_id: UUID4,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[AsyncSession | None, Depends(get_db)],
) -> dict[str, str]:
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable.")

    # Only transition from non-terminal states
    _TERMINAL = ("RESEARCH_COMPLETE", "READY_FOR_FORECAST", "PARTIAL_EVIDENCE",
                 "ABSTAINED", "NEEDS_HUMAN_REVIEW", "CANCELLED", "FAILED")

    async with db.begin():
        result = await db.execute(
            text(
                """
                UPDATE research_jobs
                SET status = 'CANCELLING', updated_at = :now
                WHERE job_id = :job_id
                  AND tenant_id = :tenant_id
                  AND status NOT IN :terminal_statuses
                RETURNING job_id
                """
            ),
            {
                "job_id": str(job_id),
                "tenant_id": current_user.user_id,
                "now": datetime.now(UTC),
                "terminal_statuses": _TERMINAL,
            },
        )
        updated = result.fetchone()

    if updated is None:
        raise HTTPException(
            status_code=409,
            detail="Job not found, already terminal, or not owned by this user.",
        )

    return {"job_id": str(job_id), "status": "CANCELLING"}


@research_router.get(
    "/{job_id}/events",
    summary="Stream research job events via SSE",
)
async def stream_research_job_events(
    job_id: UUID4,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[AsyncSession | None, Depends(get_db)],
    after_sequence: int = Query(default=0, ge=0, description="Only return events after this sequence number"),
) -> StreamingResponse:
    """Server-Sent Events stream for research job progress."""
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable.")

    # Verify ownership
    result = await db.execute(
        text("SELECT job_id FROM research_jobs WHERE job_id = :job_id AND tenant_id = :tid"),
        {"job_id": str(job_id), "tid": current_user.user_id},
    )
    if result.fetchone() is None:
        raise HTTPException(status_code=404, detail="Research job not found.")

    settings = get_settings()

    async def event_generator() -> AsyncGenerator[str, None]:
        """Poll research_job_events and emit SSE lines."""
        last_seq = after_sequence
        _TERMINAL_STATUSES = {
            "RESEARCH_COMPLETE", "READY_FOR_FORECAST", "PARTIAL_EVIDENCE",
            "ABSTAINED", "NEEDS_HUMAN_REVIEW", "CANCELLED", "FAILED",
        }
        heartbeat_counter = 0.0

        while True:
            try:
                rows_result = await db.execute(
                    text(
                        """
                        SELECT event_id, sequence_number, event_type, payload, occurred_at
                        FROM research_job_events
                        WHERE job_id = :job_id AND sequence_number > :last_seq
                        ORDER BY sequence_number ASC
                        LIMIT 50
                        """
                    ),
                    {"job_id": str(job_id), "last_seq": last_seq},
                )
                rows = rows_result.mappings().all()
                for row in rows:
                    last_seq = row["sequence_number"]
                    payload: dict[str, Any] = {
                        "event_id": str(row["event_id"]),
                        "sequence": row["sequence_number"],
                        "event_type": row["event_type"],
                        "payload": row["payload"] or {},
                        "occurred_at": row["occurred_at"].isoformat(),
                    }
                    yield f"data: {json.dumps(payload)}\n\n"
                    # Check terminal signal
                    if row["event_type"] in _TERMINAL_STATUSES or (
                        row["payload"] and row["payload"].get("status") in _TERMINAL_STATUSES
                    ):
                        yield "event: done\ndata: {}\n\n"
                        return

                # Heartbeat every SSE_HEARTBEAT_SECONDS
                heartbeat_counter += settings.SSE_POLL_INTERVAL_SECONDS
                if heartbeat_counter >= settings.SSE_HEARTBEAT_SECONDS:
                    yield ": heartbeat\n\n"
                    heartbeat_counter = 0.0

                await asyncio.sleep(settings.SSE_POLL_INTERVAL_SECONDS)
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.warning("SSE event stream error", job_id=str(job_id), error=str(exc))
                yield f"event: error\ndata: {json.dumps({'detail': str(exc)})}\n\n"
                return

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# Evidence pack endpoints
# ---------------------------------------------------------------------------


@evidence_router.get(
    "/{pack_id}",
    response_model=EvidencePackView,
    summary="Fetch evidence pack with paginated items",
)
async def get_evidence_pack(
    pack_id: UUID4,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[AsyncSession | None, Depends(get_db)],
    cursor: int = Query(default=0, ge=0, description="Ordinal cursor for pagination"),
    limit: int = Query(default=50, ge=1, le=100),
) -> EvidencePackView:
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable.")

    # Verify pack belongs to this tenant via JOIN with research_jobs
    pack_result = await db.execute(
        text(
            """
            SELECT ep.pack_id, ep.job_id, ep.pack_hash, ep.readiness,
                   ep.reasons, ep.cutoff_at, ep.created_at
            FROM evidence_packs ep
            JOIN research_jobs rj ON rj.job_id = ep.job_id
            WHERE ep.pack_id = :pack_id
              AND rj.tenant_id = :tenant_id
            """
        ),
        {"pack_id": str(pack_id), "tenant_id": current_user.user_id},
    )
    pack_row = pack_result.mappings().first()
    if pack_row is None:
        raise HTTPException(status_code=404, detail="Evidence pack not found.")

    # Paginate items
    items_result = await db.execute(
        text(
            """
            SELECT item_type, item_ref_id, ordinal
            FROM evidence_pack_items
            WHERE pack_id = :pack_id AND ordinal >= :cursor
            ORDER BY ordinal ASC
            LIMIT :limit
            """
        ),
        {"pack_id": str(pack_id), "cursor": cursor, "limit": limit + 1},
    )
    item_rows = items_result.mappings().all()
    has_more = len(item_rows) > limit
    page = item_rows[:limit]

    next_cursor: str | None = None
    if has_more and page:
        next_cursor = str(page[-1]["ordinal"] + 1)

    return EvidencePackView(
        pack_id=pack_row["pack_id"],
        job_id=pack_row["job_id"],
        pack_hash=pack_row["pack_hash"],
        readiness=pack_row["readiness"],
        reasons=pack_row["reasons"] or [],
        cutoff_at=pack_row["cutoff_at"],
        created_at=pack_row["created_at"],
        items=[
            EvidencePackItemView(
                item_type=r["item_type"],
                item_ref_id=r["item_ref_id"],
                ordinal=r["ordinal"],
            )
            for r in page
        ],
        next_cursor=next_cursor,
    )
