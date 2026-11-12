"""Finite state and vocabulary for the research runtime.

These enums are the single source of truth for persisted status values. Database
check constraints, API schemas, and tests all import from here so a new state
cannot be introduced in only one layer.
"""

from enum import StrEnum


class TargetVerificationStatus(StrEnum):
    TARGET_VERIFIED = "TARGET_VERIFIED"
    TARGET_NEEDS_REVIEW = "TARGET_NEEDS_REVIEW"
    TARGET_UNSUPPORTED = "TARGET_UNSUPPORTED"
    TARGET_ALREADY_RESOLVED = "TARGET_ALREADY_RESOLVED"
    TARGET_RULES_UNAVAILABLE = "TARGET_RULES_UNAVAILABLE"


class ResearchJobStatus(StrEnum):
    QUEUED = "QUEUED"
    TARGET_VALIDATION = "TARGET_VALIDATION"
    RESEARCH_PLANNING = "RESEARCH_PLANNING"
    RESEARCH_SEARCHING = "RESEARCH_SEARCHING"
    RESEARCH_READING = "RESEARCH_READING"
    DOCUMENT_EXTRACTION = "DOCUMENT_EXTRACTION"
    CLAIM_EXTRACTION = "CLAIM_EXTRACTION"
    DEDUPLICATION = "DEDUPLICATION"
    CONTRADICTION_REVIEW = "CONTRADICTION_REVIEW"
    EVIDENCE_QUALITY_CHECK = "EVIDENCE_QUALITY_CHECK"
    RESEARCH_COMPLETE = "RESEARCH_COMPLETE"
    READY_FOR_FORECAST = "READY_FOR_FORECAST"
    PARTIAL_EVIDENCE = "PARTIAL_EVIDENCE"
    ABSTAINED = "ABSTAINED"
    NEEDS_HUMAN_REVIEW = "NEEDS_HUMAN_REVIEW"
    CANCELLING = "CANCELLING"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


TERMINAL_JOB_STATUSES: frozenset[ResearchJobStatus] = frozenset(
    {
        ResearchJobStatus.RESEARCH_COMPLETE,
        ResearchJobStatus.READY_FOR_FORECAST,
        ResearchJobStatus.PARTIAL_EVIDENCE,
        ResearchJobStatus.ABSTAINED,
        ResearchJobStatus.NEEDS_HUMAN_REVIEW,
        ResearchJobStatus.CANCELLED,
        ResearchJobStatus.FAILED,
    }
)

# Statuses in which the runtime may still make provider or model calls.
ACTIVE_JOB_STATUSES: frozenset[ResearchJobStatus] = frozenset(
    status for status in ResearchJobStatus if status not in TERMINAL_JOB_STATUSES
)


class ReadinessStatus(StrEnum):
    READY_FOR_FORECAST = "READY_FOR_FORECAST"
    RESEARCH_COMPLETE = "RESEARCH_COMPLETE"
    PARTIAL_EVIDENCE = "PARTIAL_EVIDENCE"
    ABSTAINED = "ABSTAINED"
    NEEDS_HUMAN_REVIEW = "NEEDS_HUMAN_REVIEW"
    FAILED = "FAILED"


class ResearchDepth(StrEnum):
    QUICK = "quick"
    STANDARD = "standard"
    DEEP = "deep"


class SourceType(StrEnum):
    OFFICIAL = "official"
    LICENSED_PROVIDER = "licensed_provider"
    PUBLISHER = "publisher"
    WIRE_SERVICE = "wire_service"
    AGGREGATOR = "aggregator"
    GOVERNMENT = "government"
    REGULATOR = "regulator"
    SPORTS_AUTHORITY = "sports_authority"
    SOCIAL = "social"


class AccessMethod(StrEnum):
    OFFICIAL_API = "official_api"
    LICENSED_API = "licensed_api"
    RSS = "rss"
    PUBLIC_PAGE = "public_page"
    BROWSER = "browser"


class CommercialUseStatus(StrEnum):
    APPROVED = "approved"
    APPROVAL_REQUIRED = "approval_required"
    PROHIBITED = "prohibited"
    UNDER_REVIEW = "under_review"


class ClaimStance(StrEnum):
    SUPPORTS_YES = "supports_yes"
    SUPPORTS_NO = "supports_no"
    NEUTRAL = "neutral"
    UNRESOLVED = "unresolved"


class ClaimModality(StrEnum):
    ASSERTED = "asserted"
    OFFICIAL_STATEMENT = "official_statement"
    DENIED = "denied"
    SPECULATED = "speculated"
    CONDITIONAL = "conditional"
    QUOTED_FROM_OTHER_SOURCE = "quoted_from_other_source"
    NOT_STATED = "not_stated"


class EpistemicState(StrEnum):
    ASSERTED = "asserted"
    OFFICIAL_STATEMENT = "official_statement"
    DENIED = "denied"
    SPECULATED = "speculated"
    CONDITIONAL = "conditional"
    QUOTED_FROM_OTHER_SOURCE = "quoted_from_other_source"
    NOT_STATED = "not_stated"


class SupportState(StrEnum):
    SINGLE_SOURCE = "single_source"
    MULTIPLE_INDEPENDENT_SOURCES = "multiple_independent_sources"
    AUTHORITATIVE_PRIMARY_SOURCE = "authoritative_primary_source"
    CONFLICTING_SOURCES = "conflicting_sources"
    UNSUPPORTED = "unsupported"


class EligibilityState(StrEnum):
    ELIGIBLE_AT_CUTOFF = "eligible_at_cutoff"
    POST_CUTOFF = "post_cutoff"
    STALE_FOR_CLAIM_TYPE = "stale_for_claim_type"
    TIMESTAMP_UNCERTAIN = "timestamp_uncertain"
    SOURCE_UNAVAILABLE = "source_unavailable"


class ValidationState(StrEnum):
    SCHEMA_VALIDATED = "schema_validated"
    PENDING_REVIEW = "pending_review"
    REJECTED = "rejected"


class ClaimCategory(StrEnum):
    CURRENT_ROLE_STATUS = "current_role_status"
    MANAGER_DEPARTURE = "manager_departure"
    APPOINTMENT = "appointment"
    FIXTURE_STATUS = "fixture_status"
    MATCH_RESULT = "match_result"
    PLAYER_AVAILABILITY = "player_availability"
    TEAM_LINEUP = "team_lineup"
    INJURY_SUSPENSION = "injury_suspension"
    POLICY_ANNOUNCEMENT = "policy_announcement"
    OFFICIAL_STATEMENT = "official_statement"
    REPORTED_NEWS = "reported_news"
    OTHER = "other"


class MarketType(StrEnum):
    MANAGER_DEPARTURE_BY_DEADLINE = "manager_departure_by_deadline"
    APPOINTMENT_BY_DEADLINE = "appointment_by_deadline"
    MATCH_RESULT = "match_result"
    PLAYER_PARTICIPATION = "player_participation"
    POLICY_ANNOUNCEMENT = "policy_announcement"
    UNKNOWN = "unknown"


class FactStatus(StrEnum):
    SATISFIED = "satisfied"
    MISSING = "missing"
    STALE = "stale"
    CONFLICTED = "conflicted"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    NOT_APPLICABLE = "not_applicable"


class RelationType(StrEnum):
    CONTRADICTS = "contradicts"
    SUPPORTS = "supports"
    DUPLICATE = "duplicate"
    QUOTES = "quotes"
    SUPERSEDES = "supersedes"


class ContradictionKind(StrEnum):
    FACTUAL = "factual"
    TEMPORAL = "temporal"
    DEFINITIONAL = "definitional"
    WORDING = "wording"


class EvidenceItemType(StrEnum):
    DOCUMENT_VERSION = "document_version"
    CLAIM = "claim"
    SOURCE_GROUP = "source_group"


class OutboxStatus(StrEnum):
    PENDING = "pending"
    PUBLISHED = "published"
    FAILED = "failed"


class BudgetCategory(StrEnum):
    RESEARCH_ROUNDS = "research_rounds"
    SEARCH_REQUESTS = "search_requests"
    SOURCE_DOCUMENTS = "source_documents"
    BROWSER_PAGES = "browser_pages"
    MAX_LINK_DEPTH = "max_link_depth"
    LLM_CALLS = "llm_calls"
    STRUCTURED_OUTPUT_REPAIRS = "structured_output_repairs"
    PDF_PAGES = "pdf_pages"
    OCR_PAGES = "ocr_pages"
    REDIRECTS = "redirects"
    BYTES_DOWNLOADED = "bytes_downloaded"
    RETRIES = "retries"


class SourceFailureCode(StrEnum):
    SOURCE_NOT_REGISTERED = "SOURCE_NOT_REGISTERED"
    SOURCE_NOT_APPROVED = "SOURCE_NOT_APPROVED"
    SOURCE_ACCESS_DENIED = "SOURCE_ACCESS_DENIED"
    SOURCE_RATE_LIMITED = "SOURCE_RATE_LIMITED"
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
    SOURCE_UNSUPPORTED_CONTENT = "SOURCE_UNSUPPORTED_CONTENT"
    SOURCE_TOO_LARGE = "SOURCE_TOO_LARGE"
    SSRF_BLOCKED = "SSRF_BLOCKED"
    REDIRECT_BLOCKED = "REDIRECT_BLOCKED"


class ResearchErrorCode(StrEnum):
    TARGET_NOT_VERIFIED = "TARGET_NOT_VERIFIED"
    TARGET_RULES_UNAVAILABLE = "TARGET_RULES_UNAVAILABLE"
    TARGET_ALREADY_RESOLVED = "TARGET_ALREADY_RESOLVED"
    TARGET_UNSUPPORTED = "TARGET_UNSUPPORTED"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    NO_APPROVED_SOURCES = "NO_APPROVED_SOURCES"
    PROVIDER_FAILURE = "PROVIDER_FAILURE"
    INVALID_JOB_STATE = "INVALID_JOB_STATE"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    CANCELLED = "CANCELLED"
