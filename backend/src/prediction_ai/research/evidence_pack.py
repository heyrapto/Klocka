"""Evidence pack assembly and deterministic SHA-256 hashing (§12.3).

Assembles an immutable EvidencePack from a completed research job.
The pack hash covers a canonical manifest that makes the pack reproducible
(subject to source retention rights).

No DB access here — the pack is returned as a domain object to be persisted
by the caller (the research worker).
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

from prediction_ai.research.coverage_gate import CoverageReport
from prediction_ai.research.enums import EvidenceItemType, ReadinessStatus
from prediction_ai.research.models import (
    BudgetLimits,
    BudgetUsage,
    Claim,
    ClaimRelation,
    DocumentVersion,
    EvidencePack,
    EvidencePackItem,
    EvidencePackManifest,
    ForecastHandoff,
    ResearchPlan,
    ResearchTarget,
    SourceGroup,
)


# ---------------------------------------------------------------------------
# Canonical serialisation helpers
# ---------------------------------------------------------------------------


def _iso_utc(dt: datetime) -> str:
    """Serialise a datetime as an ISO-8601 UTC string with Z suffix."""
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def _make_serialisable(obj: object) -> object:
    """Recursively convert an object to a JSON-serialisable form.

    - datetime → ISO-8601 UTC string
    - UUID → str
    - None → kept as None (json.dumps encodes as 'null')
    - dict keys are sorted at all levels
    - lists preserve their order (order is semantically meaningful in manifests)
    """
    if isinstance(obj, datetime):
        return _iso_utc(obj)
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, dict):
        return {k: _make_serialisable(v) for k, v in sorted(obj.items())}
    if isinstance(obj, (list, tuple)):
        return [_make_serialisable(item) for item in obj]
    # Pydantic models expose their data via model_dump()
    if hasattr(obj, "model_dump"):
        return _make_serialisable(obj.model_dump())
    return obj


def _canonical_serialize(obj: dict) -> str:
    """Produce a deterministic JSON string from a dictionary.

    Rules:
    - Keys sorted at every level.
    - No extra whitespace (compact separators).
    - ASCII-safe output.
    - datetime values serialised as ISO-8601 UTC strings.
    - None values serialised as JSON null.
    - UUIDs serialised as strings.
    """
    cleaned = _make_serialisable(obj)
    return json.dumps(cleaned, sort_keys=True, ensure_ascii=True, separators=(",", ":"))


# ---------------------------------------------------------------------------
# Manifest builder
# ---------------------------------------------------------------------------


def build_manifest(
    target: ResearchTarget,
    plan: ResearchPlan,
    document_versions: list[DocumentVersion],
    claims: list[Claim],
    source_groups: list[SourceGroup],
    queries_summary: list[dict],
    coverage_report: CoverageReport,
    budget_limits: BudgetLimits,
    budget_usage: BudgetUsage,
    cutoff_at: datetime,
    registry_version: str,
    extractor_versions: list[str],
    prompt_versions: list[str],
    schema_versions: list[str],
    model_versions: list[str],
) -> EvidencePackManifest:
    """Assemble an EvidencePackManifest from a completed research job.

    Parameters
    ----------
    target:
        The verified ResearchTarget.
    plan:
        The ResearchPlan used for the job.
    document_versions:
        All DocumentVersion objects retrieved during this job.
    claims:
        All extracted Claim objects for this job.
    source_groups:
        All SourceGroup objects (independence/story clusters).
    queries_summary:
        Provider query provenance records (each a dict with at minimum
        "query_id", "provider", "query_text", and "result_count").
    coverage_report:
        The CoverageReport produced by evaluate_coverage.
    budget_limits:
        The BudgetLimits configured for this job.
    budget_usage:
        The actual BudgetUsage consumed by this job.
    cutoff_at:
        The immutable forecast cutoff datetime.
    registry_version:
        Source registry version string (e.g. "sources-v4").
    extractor_versions:
        Distinct extractor version strings used during this job.
    prompt_versions:
        Distinct prompt version strings used during this job.
    schema_versions:
        Distinct schema version strings used during this job.
    model_versions:
        Distinct model version strings used during this job.

    Returns
    -------
    EvidencePackManifest
    """
    doc_version_entries = [
        {"version_id": str(dv.version_id), "content_hash": dv.content_hash}
        for dv in sorted(document_versions, key=lambda dv: str(dv.version_id))
    ]

    claim_entries = [
        {
            "claim_id": str(c.claim_id),
            "claim_category": c.claim_category.value,
            "stance": c.stance_on_proposition.value,
        }
        for c in sorted(claims, key=lambda c: str(c.claim_id))
    ]

    source_group_entries = [
        {
            "group_id": sg.group_id,
            "member_count": sg.member_count,
        }
        for sg in sorted(source_groups, key=lambda sg: sg.group_id)
    ]

    # Normalise queries_summary: each entry must be a dict of JSON-serialisable values
    normalised_queries = [
        {str(k): v for k, v in q.items()}
        for q in queries_summary
    ]

    return EvidencePackManifest(
        target_id=target.target_id,
        resolution_rules_hash=target.resolution_rules_hash,
        forecast_cutoff_at=cutoff_at,
        document_versions=doc_version_entries,
        claims=claim_entries,
        source_groups=source_group_entries,
        queries=normalised_queries,
        source_registry_version=registry_version,
        extractor_versions=sorted(set(extractor_versions)),
        prompt_versions=sorted(set(prompt_versions)),
        schema_versions=sorted(set(schema_versions)),
        model_versions=sorted(set(model_versions)),
        budget_limits=budget_limits,
        budget_usage=budget_usage,
        readiness=coverage_report.readiness,
        readiness_reasons=coverage_report.abstention_reasons,
        created_at=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------


def hash_manifest(manifest: EvidencePackManifest) -> str:
    """Compute a deterministic SHA-256 hash over the canonical manifest.

    Returns a string of the form ``"sha256:<64 hex chars>"``.
    """
    serialisable = manifest.model_dump()
    canonical = _canonical_serialize(serialisable)
    digest = hashlib.sha256(canonical.encode("ascii")).hexdigest()
    return f"sha256:{digest}"


# ---------------------------------------------------------------------------
# Pack assembly
# ---------------------------------------------------------------------------


def assemble_pack(
    job_id: UUID,
    target: ResearchTarget,
    plan: ResearchPlan,
    document_versions: list[DocumentVersion],
    claims: list[Claim],
    source_groups: list[SourceGroup],
    queries_summary: list[dict],
    coverage_report: CoverageReport,
    budget_limits: BudgetLimits,
    budget_usage: BudgetUsage,
    cutoff_at: datetime,
    registry_version: str,
    extractor_versions: list[str],
    prompt_versions: list[str],
    schema_versions: list[str],
    model_versions: list[str],
) -> tuple[EvidencePack, list[EvidencePackItem]]:
    """Assemble an immutable EvidencePack and its item rows.

    No database access is performed here. The caller (research worker) is
    responsible for persisting the returned objects transactionally.

    Returns
    -------
    tuple[EvidencePack, list[EvidencePackItem]]
        A frozen EvidencePack domain object and all its EvidencePackItem rows,
        in ordinal order: document versions first, then claims, then source groups.
    """
    manifest = build_manifest(
        target=target,
        plan=plan,
        document_versions=document_versions,
        claims=claims,
        source_groups=source_groups,
        queries_summary=queries_summary,
        coverage_report=coverage_report,
        budget_limits=budget_limits,
        budget_usage=budget_usage,
        cutoff_at=cutoff_at,
        registry_version=registry_version,
        extractor_versions=extractor_versions,
        prompt_versions=prompt_versions,
        schema_versions=schema_versions,
        model_versions=model_versions,
    )

    pack_hash = hash_manifest(manifest)
    pack_id = uuid4()
    now = datetime.now(UTC)

    pack = EvidencePack(
        pack_id=pack_id,
        job_id=job_id,
        target_id=target.target_id,
        pack_hash=pack_hash,
        manifest=manifest,
        readiness=coverage_report.readiness,
        reasons=coverage_report.abstention_reasons,
        cutoff_at=cutoff_at,
        created_at=now,
    )

    # Build EvidencePackItem rows in stable ordinal order
    items: list[EvidencePackItem] = []
    ordinal = 0

    # 1. Document versions
    for dv in sorted(document_versions, key=lambda d: str(d.version_id)):
        items.append(
            EvidencePackItem(
                pack_id=pack_id,
                item_type=EvidenceItemType.DOCUMENT_VERSION,
                item_ref_id=str(dv.version_id),
                ordinal=ordinal,
            )
        )
        ordinal += 1

    # 2. Claims
    for claim in sorted(claims, key=lambda c: str(c.claim_id)):
        items.append(
            EvidencePackItem(
                pack_id=pack_id,
                item_type=EvidenceItemType.CLAIM,
                item_ref_id=str(claim.claim_id),
                ordinal=ordinal,
            )
        )
        ordinal += 1

    # 3. Source groups
    for sg in sorted(source_groups, key=lambda s: s.group_id):
        items.append(
            EvidencePackItem(
                pack_id=pack_id,
                item_type=EvidenceItemType.SOURCE_GROUP,
                item_ref_id=sg.group_id,
                ordinal=ordinal,
            )
        )
        ordinal += 1

    return pack, items


# ---------------------------------------------------------------------------
# Forecast handoff
# ---------------------------------------------------------------------------


def build_forecast_handoff(
    pack: EvidencePack,
    target: ResearchTarget,
    coverage_report: CoverageReport,
    eligible_claims: list[Claim],
    contradictions: list[ClaimRelation],
) -> ForecastHandoff:
    """Assemble a ForecastHandoff from §14.1.

    Parameters
    ----------
    pack:
        The assembled and hashed EvidencePack.
    target:
        The verified ResearchTarget.
    coverage_report:
        The CoverageReport with per-fact results and readiness status.
    eligible_claims:
        Claims that passed the eligibility filter at cutoff.
    contradictions:
        ClaimRelation records with relation_type=CONTRADICTS.

    Returns
    -------
    ForecastHandoff
        Ready to be serialised and dispatched to the forecasting service.
    """
    eligible_claim_dicts = [
        {
            "claim_id": str(c.claim_id),
            "category": c.claim_category.value,
            "stance": c.stance_on_proposition.value,
            "epistemic_state": c.epistemic_state.value,
            "support_state": c.support_state.value,
            "independence_group_id": c.independence_group_id,
            "available_at": _iso_utc(c.available_at) if c.available_at else None,
        }
        for c in sorted(eligible_claims, key=lambda c: str(c.claim_id))
    ]

    contradiction_dicts = [
        {
            "relation_id": str(rel.relation_id),
            "from_claim_id": str(rel.from_claim_id),
            "to_claim_id": str(rel.to_claim_id),
            "contradiction_kind": rel.contradiction_kind.value
            if rel.contradiction_kind
            else None,
            "resolution_status": rel.resolution_status,
        }
        for rel in sorted(contradictions, key=lambda r: str(r.relation_id))
    ]

    return ForecastHandoff(
        forecast_input_id=uuid4(),
        target_id=target.target_id,
        evidence_pack_id=pack.pack_id,
        evidence_pack_hash=pack.pack_hash,
        forecast_cutoff_at=pack.cutoff_at,
        required_facts=coverage_report.fact_results,
        eligible_claims=eligible_claim_dicts,
        contradictions=contradiction_dicts,
        readiness=coverage_report.readiness,
    )
