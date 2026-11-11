"""Validated domain models for the research runtime.

All external boundaries (API bodies, provider payloads, LLM structured output)
are validated into these models before they can affect a persisted evidence
pack. Datetimes are timezone-aware UTC; a naive timestamp is rejected rather
than silently assumed to be server-local time.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from prediction_ai.research.enums import (
    AccessMethod,
    ClaimCategory,
    ClaimModality,
    ClaimStance,
    CommercialUseStatus,
    ContradictionKind,
    EligibilityState,
    EpistemicState,
    EvidenceItemType,
    FactStatus,
    MarketType,
    ReadinessStatus,
    RelationType,
    ResearchDepth,
    ResearchJobStatus,
    SourceType,
    SupportState,
    TargetVerificationStatus,
    ValidationState,
)


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        raise ValueError("Timestamp must be timezone-aware; naive timestamps are not accepted.")
    return value.astimezone(UTC)


class _UTCBoundary(BaseModel):
    """Base model that normalizes every datetime field to UTC."""

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _normalize_datetimes(self) -> "_UTCBoundary":
        for field_name in type(self).model_fields:
            value = getattr(self, field_name, None)
            if isinstance(value, datetime):
                object.__setattr__(self, field_name, _utc(value))
        return self


class SubjectEntity(_UTCBoundary):
    entity_type: Literal["person", "organization", "competition", "location", "other"]
    canonical_id: str | None = Field(default=None, max_length=128)
    name: str = Field(min_length=1, max_length=255)
    aliases: list[str] = Field(default_factory=list, max_length=20)


class ResearchTarget(_UTCBoundary):
    """Verified proposition supplied by the market-verification workflow."""

    target_id: UUID
    tenant_id: UUID | None = None
    analysis_id: UUID | None = None
    platform: Literal["polymarket", "bayse"]
    platform_market_id: str = Field(min_length=1, max_length=128)
    market_url: str | None = Field(default=None, max_length=1024)
    market_type: str = Field(default="unknown", max_length=128)
    proposition: str = Field(min_length=1, max_length=2000)
    subject_entities: list[SubjectEntity] = Field(default_factory=list, max_length=20)
    deadline_at: datetime | None = None
    timezone: str | None = Field(default=None, max_length=64)
    resolution_source: str | None = Field(default=None, max_length=1024)
    resolution_rules_text: str | None = Field(default=None, max_length=20_000)
    resolution_rules_hash: str | None = Field(default=None, max_length=80)
    verification_status: TargetVerificationStatus
    market_status: Literal[
        "active", "paused", "closed", "resolved", "void", "disputed", "unknown"
    ] = "unknown"
    retrospective: bool = False
    created_at: datetime

    @field_validator("resolution_rules_hash")
    @classmethod
    def _validate_hash(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.startswith("sha256:") or len(value) != 71:
            raise ValueError("resolution_rules_hash must be 'sha256:' plus 64 hex characters.")
        int(value.removeprefix("sha256:"), 16)
        return value.lower()

    @field_validator("deadline_at")
    @classmethod
    def _validate_deadline(cls, value: datetime | None) -> datetime | None:
        return _utc(value)


class SourceRegistryEntry(_UTCBoundary):
    """One versioned, reviewed source in the approved source registry."""

    source_id: str = Field(min_length=1, max_length=128)
    provider_key: str = Field(min_length=1, max_length=128)
    domain_pattern: str = Field(min_length=1, max_length=255)
    source_type: SourceType
    allowed_market_types: list[str] = Field(default_factory=list, max_length=20)
    claim_categories: list[ClaimCategory] = Field(default_factory=list, max_length=20)
    access_method: AccessMethod
    commercial_use_status: CommercialUseStatus
    terms_url: str | None = Field(default=None, max_length=1024)
    license_or_contract_ref: str | None = Field(default=None, max_length=255)
    robots_reviewed_at: datetime | None = None
    terms_reviewed_at: datetime | None = None
    retention_policy: str = Field(default="metadata-only", max_length=128)
    max_requests_per_minute: int = Field(default=10, ge=1, le=600)
    max_concurrency: int = Field(default=1, ge=1, le=20)
    max_response_bytes: int = Field(default=2_000_000, ge=1_000, le=50_000_000)
    default_freshness_seconds: int = Field(default=86_400, ge=60, le=31_536_000)
    source_authority_ruleset_id: str = Field(default="authority-v1", max_length=64)
    authority_tier: int = Field(default=3, ge=1, le=3)
    enabled: bool = False
    registry_version: str = Field(default="sources-v1", max_length=64)
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def _approved_sources_are_reviewed(self) -> "SourceRegistryEntry":
        if self.commercial_use_status is CommercialUseStatus.APPROVED:
            if self.terms_reviewed_at is None or self.robots_reviewed_at is None:
                raise ValueError(
                    "An approved source requires terms_reviewed_at and robots_reviewed_at."
                )
        return self

    @property
    def usable(self) -> bool:
        return (
            self.enabled
            and self.commercial_use_status is CommercialUseStatus.APPROVED
            and self.terms_reviewed_at is not None
            and self.robots_reviewed_at is not None
        )


class SourceLead(_UTCBoundary):
    """A search result or feed item. A lead is not evidence until opened."""

    provider: str = Field(min_length=1, max_length=64)
    source_id: str | None = Field(default=None, max_length=128)
    title: str = Field(min_length=1, max_length=1000)
    url: str = Field(min_length=1, max_length=2048)
    publisher: str | None = Field(default=None, max_length=255)
    published_raw: str | None = Field(default=None, max_length=128)
    published_at: datetime | None = None
    snippet: str | None = Field(default=None, max_length=1000)
    verification_state: Literal["unverified"] = "unverified"


class TextSpan(_UTCBoundary):
    kind: Literal["html_paragraph", "pdf_page", "api_field", "rss_item"]
    locator: str = Field(min_length=1, max_length=255)
    text: str = Field(min_length=1, max_length=4000)
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=0)
    quote_hash: str = Field(min_length=1, max_length=80)


class ExtractedDocument(_UTCBoundary):
    canonical_url: str = Field(min_length=1, max_length=2048)
    final_url: str = Field(min_length=1, max_length=2048)
    title: str = Field(min_length=1, max_length=1000)
    publisher: str | None = Field(default=None, max_length=255)
    author: str | None = Field(default=None, max_length=255)
    published_raw: str | None = Field(default=None, max_length=128)
    source_updated_raw: str | None = Field(default=None, max_length=128)
    published_at: datetime | None = None
    source_updated_at: datetime | None = None
    text: str = Field(min_length=1, max_length=200_000)
    spans: list[TextSpan] = Field(default_factory=list, max_length=500)
    content_type: str = Field(default="text/html", max_length=128)
    language: str | None = Field(default=None, max_length=16)
    extraction_version: str = Field(default="html-extractor-1.0", max_length=64)
    quality_flags: list[str] = Field(default_factory=list, max_length=20)


class DocumentVersion(_UTCBoundary):
    """Immutable snapshot of one exact source content version."""

    version_id: UUID
    document_id: UUID
    source_id: str = Field(min_length=1, max_length=128)
    canonical_url: str = Field(min_length=1, max_length=2048)
    final_url: str = Field(min_length=1, max_length=2048)
    title: str = Field(min_length=1, max_length=1000)
    publisher: str | None = Field(default=None, max_length=255)
    author: str | None = Field(default=None, max_length=255)
    published_at: datetime | None = None
    source_updated_at: datetime | None = None
    first_seen_at: datetime
    captured_at: datetime
    available_at: datetime | None = None
    availability_source: Literal["runtime_capture", "provider_as_of", "unknown"] = "unknown"
    processed_at: datetime | None = None
    content_hash: str = Field(min_length=1, max_length=80)
    content_object_key: str | None = Field(default=None, max_length=512)
    capture_method: str = Field(default="approved_http_get", max_length=64)
    extraction_version: str = Field(default="html-extractor-1.0", max_length=64)
    retention_policy_id: str = Field(default="metadata-only", max_length=128)
    raw_timestamps: dict[str, str | None] = Field(default_factory=dict)
    text_excerpt: str | None = Field(default=None, max_length=200_000)
    spans: list[TextSpan] = Field(default_factory=list, max_length=500)
    quality_flags: list[str] = Field(default_factory=list, max_length=20)
    version_is_valid: bool = True

    @model_validator(mode="after")
    def _availability_requires_capture(self) -> "DocumentVersion":
        if self.available_at is not None and self.captured_at is None:
            raise ValueError("A version with available_at must also have captured_at.")
        if self.available_at is not None and self.available_at < self.captured_at:
            raise ValueError("available_at cannot precede captured_at for the same version.")
        return self


class EvidenceSpan(_UTCBoundary):
    kind: Literal["html_paragraph", "pdf_page", "api_field", "rss_item"]
    locator: str = Field(min_length=1, max_length=255)
    quote_hash: str = Field(min_length=1, max_length=80)
    excerpt: str = Field(min_length=1, max_length=1000)
    char_start: int | None = Field(default=None, ge=0)
    char_end: int | None = Field(default=None, ge=0)


class Claim(_UTCBoundary):
    """A source-backed assertion with a traceable span.

    The three dimensions are deliberately separate: what the source says
    (epistemic_state/modality), how it is supported (support_state), and whether
    it is usable at the forecast cutoff (eligibility_state).
    """

    claim_id: UUID
    job_id: UUID
    target_id: UUID
    claim_text: str = Field(min_length=1, max_length=2000)
    claim_category: ClaimCategory
    subject_entity_ids: list[str] = Field(default_factory=list, max_length=20)
    predicate: str = Field(min_length=1, max_length=128)
    object_value: str | None = Field(default=None, max_length=255)
    stance_on_proposition: ClaimStance
    claim_modality: ClaimModality
    epistemic_state: EpistemicState
    source_id: str = Field(min_length=1, max_length=128)
    document_version_id: UUID
    evidence_span: EvidenceSpan
    published_at: datetime | None = None
    available_at: datetime | None = None
    independence_group_id: str = Field(min_length=1, max_length=128)
    extraction_model_version: str = Field(default="rule-based-claims-v1", max_length=64)
    extraction_prompt_version: str = Field(default="claims-v1", max_length=64)
    schema_version: str = Field(default="claims-v1", max_length=64)
    support_state: SupportState = SupportState.SINGLE_SOURCE
    eligibility_state: EligibilityState = EligibilityState.TIMESTAMP_UNCERTAIN
    validation_state: ValidationState = ValidationState.SCHEMA_VALIDATED
    quality_flags: list[str] = Field(default_factory=list, max_length=20)


class ClaimRelation(_UTCBoundary):
    relation_id: UUID
    job_id: UUID
    from_claim_id: UUID
    to_claim_id: UUID
    relation_type: RelationType
    contradiction_kind: ContradictionKind | None = None
    resolution_status: Literal["unresolved", "resolved_by_review", "not_a_conflict"] = "unresolved"
    detail: dict[str, Any] = Field(default_factory=dict)


class SourceGroup(_UTCBoundary):
    group_id: str = Field(min_length=1, max_length=128)
    job_id: UUID
    cluster_key: str = Field(min_length=1, max_length=255)
    origin_url: str | None = Field(default=None, max_length=2048)
    publisher: str | None = Field(default=None, max_length=255)
    member_count: int = Field(default=1, ge=1, le=100)
    independence_confidence: Literal["established", "uncertain"] = "uncertain"


class RequiredFact(_UTCBoundary):
    fact_key: str = Field(min_length=1, max_length=64)
    question: str = Field(min_length=1, max_length=500)
    preferred_source_types: list[SourceType] = Field(default_factory=list, max_length=10)
    claim_categories: list[ClaimCategory] = Field(default_factory=list, max_length=10)
    required: bool = True
    freshness_seconds: int = Field(default=604_800, ge=60, le=31_536_000)
    min_independent_groups: int = Field(default=1, ge=1, le=10)
    min_eligible_claims: int = Field(default=1, ge=1, le=100)
    authoritative_required: bool = False
    authoritative_satisfies: bool = True
    completion_rule: str = Field(min_length=1, max_length=500)


class ResearchPlan(_UTCBoundary):
    plan_version: str = Field(default="research-plan-v1", max_length=64)
    market_type: MarketType
    proposition_type: str = Field(min_length=1, max_length=128)
    required_facts: list[RequiredFact] = Field(default_factory=list, max_length=20)
    queries: list[str] = Field(default_factory=list, max_length=20)
    approved_source_ids: list[str] = Field(default_factory=list, max_length=50)
    budget_profile: ResearchDepth = ResearchDepth.STANDARD
    stop_conditions: list[str] = Field(default_factory=list, max_length=20)
    abstention_requirements: list[str] = Field(default_factory=list, max_length=20)


class CoverageFactResult(_UTCBoundary):
    fact_key: str = Field(min_length=1, max_length=64)
    status: FactStatus
    detail: str = Field(min_length=1, max_length=1000)
    claim_ids: list[UUID] = Field(default_factory=list, max_length=50)


class CoverageDiagnostics(_UTCBoundary):
    facts: list[CoverageFactResult] = Field(default_factory=list, max_length=20)
    required_fact_coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    newest_eligible_evidence_at: datetime | None = None
    independent_source_groups: int = Field(default=0, ge=0, le=100)
    unresolved_contradictions: int = Field(default=0, ge=0, le=1000)
    provider_failures: list[str] = Field(default_factory=list, max_length=50)
    budget_exhausted: bool = False
    claims_total: int = Field(default=0, ge=0)
    claims_eligible: int = Field(default=0, ge=0)
    documents_read: int = Field(default=0, ge=0)
    source_failures: dict[str, int] = Field(default_factory=dict)


class ReadinessDecision(_UTCBoundary):
    readiness: ReadinessStatus
    reasons: list[str] = Field(default_factory=list, max_length=50)
    diagnostics: CoverageDiagnostics


class BudgetLimits(_UTCBoundary):
    research_rounds: int = Field(default=2, ge=0, le=10)
    search_requests: int = Field(default=12, ge=0, le=100)
    source_documents: int = Field(default=8, ge=0, le=50)
    browser_pages: int = Field(default=0, ge=0, le=20)
    max_link_depth: int = Field(default=1, ge=0, le=3)
    llm_calls: int = Field(default=2, ge=0, le=10)
    structured_output_repairs: int = Field(default=1, ge=0, le=2)
    wall_clock_seconds: int = Field(default=45, ge=1, le=600)
    max_requests_per_provider: int = Field(default=6, ge=1, le=50)
    max_bytes_downloaded: int = Field(default=8_000_000, ge=1000, le=100_000_000)
    max_redirects: int = Field(default=3, ge=0, le=10)

    @classmethod
    def from_depth(cls, depth: ResearchDepth) -> "BudgetLimits":
        """Return the bounded guardrail preset for a research depth profile."""

        if depth is ResearchDepth.QUICK:
            return cls(
                research_rounds=1,
                search_requests=6,
                source_documents=4,
                browser_pages=2,
                max_link_depth=0,
                llm_calls=1,
                structured_output_repairs=1,
                wall_clock_seconds=20,
                max_requests_per_provider=4,
                max_bytes_downloaded=4_000_000,
                max_redirects=2,
            )
        if depth is ResearchDepth.DEEP:
            return cls(
                research_rounds=3,
                search_requests=20,
                source_documents=12,
                browser_pages=8,
                max_link_depth=2,
                llm_calls=3,
                structured_output_repairs=1,
                wall_clock_seconds=90,
                max_requests_per_provider=10,
                max_bytes_downloaded=16_000_000,
                max_redirects=3,
            )
        return cls()


class BudgetUsage(_UTCBoundary):
    counters: dict[str, int] = Field(default_factory=dict)
    per_provider: dict[str, int] = Field(default_factory=dict)
    wall_clock_seconds: float = Field(default=0.0, ge=0.0)


class EvidencePackManifest(_UTCBoundary):
    """Deterministic manifest covered by the evidence-pack hash."""

    pack_version: str = Field(default="evidence-pack-v1", max_length=64)
    target_id: UUID
    resolution_rules_hash: str | None = Field(default=None, max_length=80)
    forecast_cutoff_at: datetime
    document_versions: list[dict[str, str]] = Field(default_factory=list, max_length=100)
    claims: list[dict[str, str]] = Field(default_factory=list, max_length=500)
    source_groups: list[dict[str, str | int]] = Field(default_factory=list, max_length=100)
    queries: list[dict[str, str | int | None]] = Field(default_factory=list, max_length=100)
    source_registry_version: str = Field(default="sources-v1", max_length=64)
    extractor_versions: list[str] = Field(default_factory=list, max_length=20)
    prompt_versions: list[str] = Field(default_factory=list, max_length=20)
    schema_versions: list[str] = Field(default_factory=list, max_length=20)
    model_versions: list[str] = Field(default_factory=list, max_length=20)
    budget_limits: BudgetLimits
    budget_usage: BudgetUsage
    readiness: ReadinessStatus
    readiness_reasons: list[str] = Field(default_factory=list, max_length=50)
    created_at: datetime


class EvidencePack(_UTCBoundary):
    pack_id: UUID
    job_id: UUID
    target_id: UUID
    pack_hash: str = Field(min_length=1, max_length=80)
    manifest: EvidencePackManifest
    readiness: ReadinessStatus
    reasons: list[str] = Field(default_factory=list, max_length=50)
    cutoff_at: datetime
    created_at: datetime


class EvidencePackItem(_UTCBoundary):
    item_id: int | None = None
    pack_id: UUID
    item_type: EvidenceItemType
    item_ref_id: str = Field(min_length=1, max_length=128)
    ordinal: int = Field(ge=0)


class ForecastHandoff(_UTCBoundary):
    forecast_input_id: UUID
    target_id: UUID
    evidence_pack_id: UUID
    evidence_pack_hash: str = Field(min_length=1, max_length=80)
    forecast_cutoff_at: datetime
    target_schema_version: str = Field(default="target-v2", max_length=64)
    source_ruleset_version: str = Field(default="sources-v1", max_length=64)
    claim_schema_version: str = Field(default="claims-v1", max_length=64)
    required_facts: list[CoverageFactResult] = Field(default_factory=list, max_length=20)
    eligible_claims: list[dict[str, Any]] = Field(default_factory=list, max_length=500)
    contradictions: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    market_snapshot_ref: str | None = Field(default=None, max_length=128)
    feature_snapshot_ref: str | None = Field(default=None, max_length=128)
    readiness: ReadinessStatus


class ResearchJobView(_UTCBoundary):
    job_id: UUID
    tenant_id: UUID | None = None
    analysis_id: UUID | None = None
    target_id: UUID
    status: ResearchJobStatus
    research_profile: str = Field(max_length=32)
    requested_depth: ResearchDepth
    forecast_cutoff_at: datetime
    readiness: ReadinessStatus | None = None
    readiness_reasons: list[str] = Field(default_factory=list, max_length=50)
    pack_id: UUID | None = None
    error_code: str | None = Field(default=None, max_length=64)
    error_message: str | None = Field(default=None, max_length=2000)
    budget_limits: BudgetLimits
    budget_usage: BudgetUsage
    attempt_count: int = Field(default=0, ge=0)
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    cancelled_at: datetime | None = None


class ResearchJobCreate(_UTCBoundary):
    analysis_id: UUID | None = None
    target_id: UUID | None = None
    research_profile: str = Field(default="standard", max_length=32)
    requested_depth: ResearchDepth = ResearchDepth.STANDARD

    @model_validator(mode="after")
    def _one_target_source(self) -> "ResearchJobCreate":
        if self.analysis_id is None and self.target_id is None:
            raise ValueError("Either analysis_id or target_id is required.")
        return self


class ResearchJobCreated(_UTCBoundary):
    job_id: UUID
    status: ResearchJobStatus
    created_at: datetime
    status_url: str
    events_url: str


class ResearchEventView(_UTCBoundary):
    id: int
    event: str
    stage: str
    data: dict[str, Any]
    created_at: datetime
