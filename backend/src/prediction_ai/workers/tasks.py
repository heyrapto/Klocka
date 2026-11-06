"""Celery tasks for the Prediction Intelligence platform.

Tasks:
  prediction_ai.run_analysis       — existing screenshot→forecast pipeline
  prediction_ai.run_research_job   — multi-stage research runtime pipeline
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog

from prediction_ai.orchestration.runner import AnalysisWorkflowRunner
from prediction_ai.workers.celery_app import celery_app

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Existing analysis pipeline task (unchanged)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Research runtime pipeline task
# ---------------------------------------------------------------------------


@celery_app.task(
    name="prediction_ai.run_research_job",
    bind=True,
    max_retries=1,
    soft_time_limit=240,
    time_limit=300,
)
def run_research_job(self: Any, job_id_str: str) -> dict[str, Any]:
    """Execute the multi-stage research runtime pipeline for a research job.

    Stages (in order):
      1. TARGET_VALIDATION    — verify job still exists and is QUEUED
      2. RESEARCH_PLANNING    — build a ResearchPlan via the planner
      3. RESEARCH_SEARCHING   — query enabled sources
      4. RESEARCH_READING     — fetch document content via SafeFetcher
      5. DOCUMENT_EXTRACTION  — extract structured content from HTML/text
      6. CLAIM_EXTRACTION     — extract claims via ClaimExtractor
      7. DEDUPLICATION        — deduplicate claim set
      8. CONTRADICTION_REVIEW — detect opposing claims
      9. EVIDENCE_QUALITY_CHECK — run coverage gate
     10. Terminal state       — READY_FOR_FORECAST / PARTIAL_EVIDENCE / ABSTAINED / NEEDS_HUMAN_REVIEW
    """
    logger.info("Research job started", job_id=job_id_str, task_id=self.request.id)

    try:
        result = asyncio.run(_run_research_pipeline(UUID(job_id_str)))
        return result
    except Exception as exc:
        logger.exception("Research job pipeline failed", job_id=job_id_str, error=str(exc))
        # Best-effort: mark job as FAILED
        try:
            asyncio.run(_mark_job_failed(UUID(job_id_str), str(exc)))
        except Exception as inner:
            logger.error("Could not mark job failed", job_id=job_id_str, error=str(inner))
        raise


async def _run_research_pipeline(job_id: UUID) -> dict[str, Any]:
    """Async implementation of the research pipeline."""
    from sqlalchemy import text

    from prediction_ai.core.config import get_settings
    from prediction_ai.db.session import get_database
    from prediction_ai.research.budget import BudgetTracker
    from prediction_ai.research.claims import ClaimExtractor
    from prediction_ai.research.contradiction import detect_contradictions
    from prediction_ai.research.coverage_gate import evaluate_coverage
    from prediction_ai.research.dedupe import deduplicate_claims
    from prediction_ai.research.enums import ReadinessStatus, ResearchJobStatus
    from prediction_ai.research.evidence_pack import assemble_pack, build_forecast_handoff
    from prediction_ai.research.fetch import SafeFetcher
    from prediction_ai.research.models import (
        BudgetLimits,
        BudgetUsage,
        ResearchTarget,
    )
    from prediction_ai.research.planner import build_plan
    from prediction_ai.research.registry import SourceRegistry
    from prediction_ai.research.time import make_cutoff

    settings = get_settings()
    db_factory = get_database()

    async with db_factory.session() as session:
        # ------------------------------------------------------------------
        # STAGE 1: TARGET_VALIDATION — load job, verify it is still QUEUED
        # ------------------------------------------------------------------
        await _transition_status(session, job_id, ResearchJobStatus.TARGET_VALIDATION)

        row = await _load_job_row(session, job_id)
        if row is None:
            raise RuntimeError(f"Research job {job_id} not found in database.")

        if row["status"] not in (ResearchJobStatus.QUEUED, ResearchJobStatus.TARGET_VALIDATION):
            logger.warning("Job not in expected status — skipping", job_id=str(job_id), status=row["status"])
            return {"job_id": str(job_id), "skipped": True}

        await _emit_event(session, job_id, "TARGET_VALIDATION", {"message": "Job loaded and validated."})
        await _check_cancelling(session, job_id)

        # Build a minimal ResearchTarget from the job row
        target = ResearchTarget(
            target_id=uuid.uuid4(),
            tenant_id=uuid.UUID(str(row["tenant_id"])),
            platform=row["platform"] if row["platform"] in ("polymarket", "bayse") else "polymarket",
            platform_market_id=str(row["platform_market_id"]),
            proposition=str(row["market_title"]),
            resolution_rules=str(row["resolution_criteria"] or row["market_title"]),
            resolution_rules_hash="",
            cutoff_at=make_cutoff(row["closes_at"]),
            verification_status="TARGET_VERIFIED",
        )

        # ------------------------------------------------------------------
        # STAGE 2: RESEARCH_PLANNING
        # ------------------------------------------------------------------
        await _transition_status(session, job_id, ResearchJobStatus.RESEARCH_PLANNING)
        await _check_cancelling(session, job_id)

        cutoff_at = target.cutoff_at
        budget_limits = BudgetLimits(
            max_tokens=int(row.get("budget_max_tokens") or settings.RESEARCH_MAX_DOCUMENTS * 2000),
            max_documents=int(row.get("budget_max_documents") or settings.RESEARCH_MAX_DOCUMENTS),
            max_claims_per_document=settings.RESEARCH_MAX_CLAIMS_PER_DOC,
        )
        tracker = BudgetTracker(limits=budget_limits)
        plan = build_plan(target=target, budget_limits=budget_limits)

        # Load source registry
        registry = SourceRegistry.from_file(settings.RESEARCH_SOURCES_REGISTRY_PATH)
        enabled_sources = registry.enabled_sources()

        await _emit_event(
            session, job_id, "RESEARCH_PLANNING",
            {"market_type": plan.market_type, "required_facts": len(plan.required_facts), "enabled_sources": len(enabled_sources)},
        )
        await _check_cancelling(session, job_id)

        # ------------------------------------------------------------------
        # STAGE 3: RESEARCH_SEARCHING — query enabled sources
        # ------------------------------------------------------------------
        await _transition_status(session, job_id, ResearchJobStatus.RESEARCH_SEARCHING)
        await _check_cancelling(session, job_id)

        import httpx
        fetcher_client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        fetcher = SafeFetcher(registry=registry, budget=tracker)

        # Build query URLs from the plan's queries
        document_urls: list[str] = []
        queries_summary: list[dict[str, Any]] = []
        for query in plan.queries[:20]:  # cap at 20 queries
            query_id = str(uuid.uuid4())
            queries_summary.append({
                "query_id": query_id,
                "provider": query.source_id,
                "query_text": query.query_text,
                "result_count": 0,
            })
            # Only add enabled, non-prohibited source URLs
            source_entry = registry.get(query.source_id)
            if source_entry and source_entry.enabled:
                document_urls.append(query.url)

        await _emit_event(session, job_id, "RESEARCH_SEARCHING", {"query_count": len(queries_summary)})
        await _check_cancelling(session, job_id)

        # ------------------------------------------------------------------
        # STAGE 4: RESEARCH_READING — fetch document content
        # ------------------------------------------------------------------
        await _transition_status(session, job_id, ResearchJobStatus.RESEARCH_READING)

        raw_documents: list[dict[str, Any]] = []
        for url in document_urls:
            if tracker.is_document_budget_exhausted():
                break
            try:
                content = await fetcher.fetch(url, cutoff_at=cutoff_at, now=datetime.now(UTC))
                if content:
                    raw_documents.append({"url": url, "content": content})
                    tracker.record_document()
            except Exception as fetch_err:
                logger.warning("Document fetch failed", url=url, error=str(fetch_err))

        await _emit_event(session, job_id, "RESEARCH_READING", {"documents_fetched": len(raw_documents)})
        await _check_cancelling(session, job_id)

        # ------------------------------------------------------------------
        # STAGE 5: DOCUMENT_EXTRACTION
        # ------------------------------------------------------------------
        await _transition_status(session, job_id, ResearchJobStatus.DOCUMENT_EXTRACTION)

        from prediction_ai.research.extract import extract_html

        document_versions = []
        for doc in raw_documents:
            try:
                version = extract_html(doc["content"], source_url=doc["url"], cutoff_at=cutoff_at)
                if version:
                    document_versions.append(version)
                    tracker.record_tokens(len(version.text_content or "") // 4)
            except Exception as ex_err:
                logger.warning("HTML extraction failed", url=doc["url"], error=str(ex_err))

        await _emit_event(session, job_id, "DOCUMENT_EXTRACTION", {"versions_extracted": len(document_versions)})
        await _check_cancelling(session, job_id)

        # ------------------------------------------------------------------
        # STAGE 6: CLAIM_EXTRACTION
        # ------------------------------------------------------------------
        await _transition_status(session, job_id, ResearchJobStatus.CLAIM_EXTRACTION)

        extractor = ClaimExtractor(budget=tracker, cutoff_at=cutoff_at)
        all_claims = []
        for dv in document_versions:
            claims = extractor.extract(document_version=dv, target=target)
            all_claims.extend(claims)
            if tracker.is_token_budget_exhausted():
                break

        await _emit_event(session, job_id, "CLAIM_EXTRACTION", {"raw_claims": len(all_claims)})
        await _check_cancelling(session, job_id)

        # ------------------------------------------------------------------
        # STAGE 7: DEDUPLICATION
        # ------------------------------------------------------------------
        await _transition_status(session, job_id, ResearchJobStatus.DEDUPLICATION)

        deduped_claims = deduplicate_claims(all_claims)
        await _emit_event(session, job_id, "DEDUPLICATION", {"unique_claims": len(deduped_claims)})
        await _check_cancelling(session, job_id)

        # ------------------------------------------------------------------
        # STAGE 8: CONTRADICTION_REVIEW
        # ------------------------------------------------------------------
        await _transition_status(session, job_id, ResearchJobStatus.CONTRADICTION_REVIEW)

        contradictions = detect_contradictions(deduped_claims, job_id=job_id)
        await _emit_event(session, job_id, "CONTRADICTION_REVIEW", {"contradictions_detected": len(contradictions)})
        await _check_cancelling(session, job_id)

        # ------------------------------------------------------------------
        # STAGE 9: EVIDENCE_QUALITY_CHECK (coverage gate)
        # ------------------------------------------------------------------
        await _transition_status(session, job_id, ResearchJobStatus.EVIDENCE_QUALITY_CHECK)

        source_registry_dict = {src.source_id: src for src in enabled_sources}
        now = datetime.now(UTC)
        budget_exhausted = tracker.is_token_budget_exhausted() or tracker.is_document_budget_exhausted()

        coverage_report = evaluate_coverage(
            plan=plan,
            claims=deduped_claims,
            claim_relations=contradictions,
            source_registry=source_registry_dict,
            cutoff_at=cutoff_at,
            now=now,
            budget_exhausted=budget_exhausted,
        )

        await _emit_event(
            session, job_id, "EVIDENCE_QUALITY_CHECK",
            {
                "readiness": coverage_report.readiness,
                "mandatory_satisfied": coverage_report.mandatory_facts_satisfied,
                "mandatory_total": coverage_report.mandatory_facts_total,
                "unresolved_contradictions": coverage_report.unresolved_contradictions,
            },
        )

        # ------------------------------------------------------------------
        # STAGE 10: Assemble evidence pack and transition to terminal status
        # ------------------------------------------------------------------
        budget_usage = BudgetUsage(
            tokens_used=tracker.tokens_used,
            documents_fetched=tracker.documents_fetched,
            claims_extracted=len(all_claims),
        )

        pack, pack_items = assemble_pack(
            job_id=job_id,
            target=target,
            plan=plan,
            document_versions=document_versions,
            claims=deduped_claims,
            source_groups=[],
            queries_summary=queries_summary,
            coverage_report=coverage_report,
            budget_limits=budget_limits,
            budget_usage=budget_usage,
            cutoff_at=cutoff_at,
            registry_version=registry.version,
            extractor_versions=["html_extractor_v1"],
            prompt_versions=["claim_extractor_v1"],
            schema_versions=["research_schema_v1"],
            model_versions=["gemini_research_v1"],
        )

        # Persist the evidence pack + items
        await session.execute(
            text(
                """
                INSERT INTO evidence_packs (pack_id, job_id, target_id, pack_hash, readiness, reasons, cutoff_at, created_at)
                VALUES (:pack_id, :job_id, :target_id, :pack_hash, :readiness, :reasons::jsonb, :cutoff_at, :now)
                ON CONFLICT (pack_id) DO NOTHING
                """
            ),
            {
                "pack_id": str(pack.pack_id),
                "job_id": str(job_id),
                "target_id": str(target.target_id),
                "pack_hash": pack.pack_hash,
                "readiness": pack.readiness,
                "reasons": str(pack.reasons).replace("'", '"'),
                "cutoff_at": pack.cutoff_at,
                "now": pack.created_at,
            },
        )

        for item in pack_items:
            await session.execute(
                text(
                    """
                    INSERT INTO evidence_pack_items (pack_id, item_type, item_ref_id, ordinal)
                    VALUES (:pack_id, :item_type, :item_ref_id, :ordinal)
                    ON CONFLICT DO NOTHING
                    """
                ),
                {
                    "pack_id": str(pack.pack_id),
                    "item_type": item.item_type,
                    "item_ref_id": item.item_ref_id,
                    "ordinal": item.ordinal,
                },
            )

        # Map CoverageReport readiness → job terminal status
        readiness_to_status = {
            ReadinessStatus.READY_FOR_FORECAST: ResearchJobStatus.READY_FOR_FORECAST,
            ReadinessStatus.RESEARCH_COMPLETE: ResearchJobStatus.RESEARCH_COMPLETE,
            ReadinessStatus.PARTIAL_EVIDENCE: ResearchJobStatus.PARTIAL_EVIDENCE,
            ReadinessStatus.ABSTAINED: ResearchJobStatus.ABSTAINED,
            ReadinessStatus.NEEDS_HUMAN_REVIEW: ResearchJobStatus.NEEDS_HUMAN_REVIEW,
        }
        terminal_status = readiness_to_status.get(
            coverage_report.readiness, ResearchJobStatus.RESEARCH_COMPLETE
        )

        await session.execute(
            text(
                """
                UPDATE research_jobs
                SET status = :status, pack_id = :pack_id, updated_at = :now
                WHERE job_id = :job_id
                """
            ),
            {
                "status": terminal_status,
                "pack_id": str(pack.pack_id),
                "job_id": str(job_id),
                "now": datetime.now(UTC),
            },
        )
        await _emit_event(
            session, job_id, terminal_status,
            {"pack_id": str(pack.pack_id), "readiness": coverage_report.readiness},
        )
        await session.commit()

        logger.info(
            "Research job completed",
            job_id=str(job_id),
            terminal_status=terminal_status,
            pack_id=str(pack.pack_id),
            eligible_claims=coverage_report.eligible_claim_count,
        )

        return {
            "job_id": str(job_id),
            "status": terminal_status,
            "pack_id": str(pack.pack_id),
            "readiness": coverage_report.readiness,
            "eligible_claims": coverage_report.eligible_claim_count,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _load_job_row(session: Any, job_id: UUID) -> dict[str, Any] | None:
    from sqlalchemy import text

    result = await session.execute(
        text(
            "SELECT * FROM research_jobs WHERE job_id = :job_id"
        ),
        {"job_id": str(job_id)},
    )
    return result.mappings().first()


async def _transition_status(session: Any, job_id: UUID, status: str) -> None:
    from sqlalchemy import text

    await session.execute(
        text(
            "UPDATE research_jobs SET status = :status, updated_at = :now WHERE job_id = :job_id"
        ),
        {"status": status, "now": datetime.now(UTC), "job_id": str(job_id)},
    )
    await session.flush()


async def _emit_event(session: Any, job_id: UUID, event_type: str, payload: dict[str, Any]) -> None:
    """Write a research_job_events row for the SSE stream."""
    import json

    from sqlalchemy import text

    await session.execute(
        text(
            """
            INSERT INTO research_job_events (event_id, job_id, event_type, payload, occurred_at)
            VALUES (:event_id, :job_id, :event_type, :payload::jsonb, :now)
            """
        ),
        {
            "event_id": str(uuid.uuid4()),
            "job_id": str(job_id),
            "event_type": event_type,
            "payload": json.dumps(payload),
            "now": datetime.now(UTC),
        },
    )
    await session.flush()


async def _check_cancelling(session: Any, job_id: UUID) -> None:
    """Raise RuntimeError if the job has been signalled CANCELLING."""
    from sqlalchemy import text

    result = await session.execute(
        text("SELECT status FROM research_jobs WHERE job_id = :job_id"),
        {"job_id": str(job_id)},
    )
    row = result.mappings().first()
    if row and row["status"] == "CANCELLING":
        # Transition to CANCELLED
        await session.execute(
            text("UPDATE research_jobs SET status = 'CANCELLED', updated_at = :now WHERE job_id = :job_id"),
            {"now": datetime.now(UTC), "job_id": str(job_id)},
        )
        await _emit_event(session, job_id, "CANCELLED", {"reason": "Cancellation requested by user."})
        await session.commit()
        raise RuntimeError(f"Research job {job_id} cancelled by user request.")


async def _mark_job_failed(job_id: UUID, error_message: str) -> None:
    from sqlalchemy import text

    from prediction_ai.db.session import get_database

    db_factory = get_database()
    async with db_factory.session() as session:
        await session.execute(
            text(
                """
                UPDATE research_jobs
                SET status = 'FAILED', error_message = :err, updated_at = :now
                WHERE job_id = :job_id
                """
            ),
            {"err": error_message[:2048], "now": datetime.now(UTC), "job_id": str(job_id)},
        )
        await session.commit()
