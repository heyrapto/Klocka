"""Claim extraction with mandatory provenance.

The default extractor is deterministic. It preserves rumor, denial,
conditionality, and absence-of-evidence language instead of converting it into
confirmation or a confirmed negative. An optional LLM extractor contract is
defined here, but its output is rejected unless every claim validates against
source IDs, version IDs, span locators, enums, and timestamps already loaded by
the worker.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime
from typing import Any
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from prediction_ai.research.enums import (
    ClaimCategory,
    ClaimModality,
    ClaimStance,
    EligibilityState,
    EpistemicState,
    SourceType,
    SupportState,
    ValidationState,
)
from prediction_ai.research.extract import quote_hash, split_sentences
from prediction_ai.research.models import (
    Claim,
    DocumentVersion,
    EvidenceSpan,
    ExtractedDocument,
    ResearchPlan,
    ResearchTarget,
)
from prediction_ai.research.registry import SourceRegistry
from prediction_ai.research.time import version_is_eligible

_CLAIM_NAMESPACE = UUID("b6b8f1b0-5a7f-4d5f-9d6a-2f1f5f9a7c31")

_CATEGORY_KEYWORDS: dict[ClaimCategory, tuple[str, ...]] = {
    ClaimCategory.MANAGER_DEPARTURE: (
        "leave",
        "leaves",
        "leaving",
        "departure",
        "depart",
        "sacked",
        "dismissed",
        "axed",
        "part ways",
        "parted ways",
        "resign",
        "resigned",
        "exit",
        "contract terminated",
        "step down",
        "stepped down",
        "replaced",
        "replacement",
        "mutual consent",
    ),
    ClaimCategory.APPOINTMENT: (
        "appoint",
        "appointed",
        "named",
        "hire",
        "hired",
        "interim",
        "successor",
        "takes over",
        "take over",
    ),
    ClaimCategory.CURRENT_ROLE_STATUS: (
        "remains",
        "remain",
        "stays",
        "stay",
        "still in charge",
        "in charge",
        "under contract",
        "committed",
        "current manager",
        "head coach",
        "boss",
    ),
    ClaimCategory.FIXTURE_STATUS: (
        "fixture",
        "kickoff",
        "kick-off",
        "postponed",
        "abandoned",
        "scheduled",
        "venue",
        "match will",
        "game will",
        "cancelled",
        "canceled",
    ),
    ClaimCategory.MATCH_RESULT: (
        "full-time",
        "full time",
        "final score",
        "beat",
        "defeated",
        "won",
        "lost",
        "drew",
        "draw",
        "result",
        "score",
    ),
    ClaimCategory.PLAYER_AVAILABILITY: (
        "available",
        "unavailable",
        "fit",
        "fitness",
        "ruled out",
        "doubt",
        "suspended",
        "injury",
        "injured",
        "return",
        "returns",
    ),
    ClaimCategory.TEAM_LINEUP: (
        "lineup",
        "line-up",
        "starting xi",
        "starting eleven",
        "team sheet",
        "team news",
        "starts",
        "benched",
    ),
    ClaimCategory.INJURY_SUSPENSION: (
        "injury",
        "injured",
        "suspension",
        "suspended",
        "red card",
        "yellow card",
        "ban",
    ),
    ClaimCategory.POLICY_ANNOUNCEMENT: (
        "announced",
        "announcement",
        "signed",
        "approved",
        "rejected",
        "delayed",
        "legislation",
        "executive order",
        "regulation",
        "ministry",
        "government",
        "parliament",
        "gazette",
        "effective",
    ),
    ClaimCategory.OFFICIAL_STATEMENT: (
        "statement",
        "confirmed",
        "official",
        "spokesperson",
        "said in a statement",
        "club says",
    ),
}

_DENIAL_PATTERNS = (
    r"\bden(?:y|ies|ied)\b",
    r"\bnot\s+(?:leaving|departing|resigning|for\s+sale)\b",
    r"\bno\s+plans?\s+to\s+(?:leave|depart|resign)\b",
    r"\bwill\s+stay\b",
    r"\bremains?\s+committed\b",
    r"\bhas\s+not\s+(?:left|departed|resigned)\b",
    r"\bnot\s+been\s+(?:sacked|dismissed)\b",
)
_OFFICIAL_PATTERNS = (
    r"\bofficial(?:ly)?\b",
    r"\bannounc(?:e|ed|ement)\b",
    r"\bconfirm(?:ed|s)?\b",
    r"\bin\s+a\s+statement\b",
    r"\bclub\s+says?\b",
    r"\bspokesperson\b",
)
_CONDITIONAL_PATTERNS = (r"\bif\b", r"\bunless\b", r"\bsubject\s+to\b", r"\bprovided\s+that\b")
_QUOTED_PATTERNS = (
    r"\baccording\s+to\b",
    r"\bquoted\s+by\b",
    r"\bciting\b",
    r"\bsources?\s+told\b",
)
_SPECULATION_PATTERNS = (
    r"\bcould\b",
    r"\bmay\b",
    r"\bmight\b",
    r"\bexpected\b",
    r"\bin\s+talks\b",
    r"\bnegotiat(?:e|ing|ions?)\b",
    r"\breportedly\b",
    r"\bsources?\s+say\b",
    r"\bunderstands?\b",
    r"\blikely\b",
    r"\bconsidering\b",
    r"\bweighing\b",
    r"\bspeculat(?:e|ion|ed)\b",
    r"\brumou?r\b",
)
_ABSENCE_PATTERNS = (
    r"\bno\s+(?:official\s+)?announcement\b",
    r"\bhas\s+not\s+(?:yet\s+)?announc(?:ed|e)\b",
    r"\bwithout\s+(?:an?\s+)?announcement\b",
    r"\bno\s+confirmation\b",
    r"\bdeclined\s+to\s+comment\b",
)


def _matches(pattern_group: tuple[str, ...], text: str) -> bool:
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in pattern_group)


def _subject_terms(target: ResearchTarget) -> list[str]:
    terms: list[str] = []
    for entity in target.subject_entities:
        terms.append(entity.name.lower())
        terms.extend(alias.lower() for alias in entity.aliases)
    if not terms:
        terms = [
            token.lower()
            for token in re.findall(r"[A-Za-z][A-Za-z0-9'\-]{3,}", target.proposition)
            if token.lower()
            not in {
                "will",
                "would",
                "could",
                "before",
                "after",
                "leave",
                "win",
                "market",
                "team",
                "club",
                "from",
                "with",
                "this",
                "that",
            }
        ][:6]
    return terms


def _matches_subject(text: str, terms: list[str]) -> bool:
    lowered = text.lower()
    return any(term and term in lowered for term in terms)


def _classify(text: str, plan: ResearchPlan) -> ClaimCategory | None:
    preferred = {
        category for fact in plan.required_facts for category in fact.claim_categories
    }
    for category, keywords in _CATEGORY_KEYWORDS.items():
        if preferred and category not in preferred:
            continue
        if any(keyword in text.lower() for keyword in keywords):
            return category
    return None


def _predicate(category: ClaimCategory, text: str, modality: ClaimModality) -> str:
    lowered = text.lower()
    if category is ClaimCategory.MANAGER_DEPARTURE:
        if modality is ClaimModality.DENIED:
            return "denied_departure"
        if modality is ClaimModality.SPECULATED:
            return "speculated_departure"
        if modality is ClaimModality.OFFICIAL_STATEMENT:
            return "announced_departure"
        return "reported_departure"
    if category is ClaimCategory.APPOINTMENT:
        return "reported_appointment" if modality is not ClaimModality.OFFICIAL_STATEMENT else "announced_appointment"
    if category is ClaimCategory.CURRENT_ROLE_STATUS:
        return "current_role_status"
    if category is ClaimCategory.FIXTURE_STATUS:
        if "postpon" in lowered:
            return "fixture_postponed"
        if "abandon" in lowered:
            return "fixture_abandoned"
        if "cancel" in lowered:
            return "fixture_cancelled"
        return "fixture_status"
    if category is ClaimCategory.MATCH_RESULT:
        return "match_result"
    if category is ClaimCategory.PLAYER_AVAILABILITY:
        return "player_availability"
    if category is ClaimCategory.TEAM_LINEUP:
        return "team_lineup"
    if category is ClaimCategory.INJURY_SUSPENSION:
        return "injury_or_suspension"
    if category is ClaimCategory.POLICY_ANNOUNCEMENT:
        return "policy_action"
    if category is ClaimCategory.OFFICIAL_STATEMENT:
        return "official_statement"
    return "reported_claim"


def _stance(category: ClaimCategory, text: str, modality: ClaimModality) -> ClaimStance:
    lowered = text.lower()
    if modality is ClaimModality.DENIED:
        return ClaimStance.SUPPORTS_NO
    if modality is ClaimModality.NOT_STATED:
        return ClaimStance.UNRESOLVED
    if category is ClaimCategory.MATCH_RESULT:
        if re.search(r"\b(?:lost|defeated by|draw|drew)\b", lowered):
            return ClaimStance.SUPPORTS_NO
        if re.search(r"\b(?:won|beat|defeated|victory)\b", lowered):
            return ClaimStance.SUPPORTS_YES
        return ClaimStance.UNRESOLVED
    if category in {
        ClaimCategory.MANAGER_DEPARTURE,
        ClaimCategory.APPOINTMENT,
        ClaimCategory.POLICY_ANNOUNCEMENT,
    }:
        if re.search(r"\b(?:rejected|delayed|blocked|denied|cancelled|canceled)\b", lowered):
            return ClaimStance.SUPPORTS_NO
        return ClaimStance.SUPPORTS_YES
    if category is ClaimCategory.PLAYER_AVAILABILITY:
        if re.search(r"\b(?:ruled out|unavailable|injured|suspended)\b", lowered):
            return ClaimStance.SUPPORTS_NO
        if re.search(r"\b(?:fit|available|returns?)\b", lowered):
            return ClaimStance.SUPPORTS_YES
    return ClaimStance.UNRESOLVED


def _modality(text: str) -> ClaimModality:
    if _matches(_ABSENCE_PATTERNS, text):
        return ClaimModality.NOT_STATED
    if _matches(_DENIAL_PATTERNS, text):
        return ClaimModality.DENIED
    if _matches(_OFFICIAL_PATTERNS, text):
        return ClaimModality.OFFICIAL_STATEMENT
    if _matches(_CONDITIONAL_PATTERNS, text):
        return ClaimModality.CONDITIONAL
    if _matches(_QUOTED_PATTERNS, text):
        return ClaimModality.QUOTED_FROM_OTHER_SOURCE
    if _matches(_SPECULATION_PATTERNS, text):
        return ClaimModality.SPECULATED
    return ClaimModality.ASSERTED


def _epistemic(modality: ClaimModality) -> EpistemicState:
    return EpistemicState(modality.value)


def _eligibility(version: DocumentVersion, cutoff_at: datetime) -> EligibilityState:
    if version_is_eligible(version, cutoff_at):
        return EligibilityState.ELIGIBLE_AT_CUTOFF
    if version.available_at is not None and version.available_at > cutoff_at:
        return EligibilityState.POST_CUTOFF
    if version.available_at is None or version.availability_source == "unknown":
        return EligibilityState.TIMESTAMP_UNCERTAIN
    return EligibilityState.POST_CUTOFF


class RuleBasedClaimExtractor:
    """Deterministic extractor for bounded passages.

    This is deliberately conservative. It extracts only sentences that mention a
    target entity and a claim-category keyword, and it never turns an absence of
    announcement into a negative fact.
    """

    def __init__(self, *, max_passages_per_document: int = 3, max_claims: int = 50) -> None:
        self._max_passages = max(1, min(max_passages_per_document, 10))
        self._max_claims = max(1, min(max_claims, 200))

    def extract(
        self,
        *,
        job_id: UUID,
        target: ResearchTarget,
        version: DocumentVersion,
        document: ExtractedDocument,
        plan: ResearchPlan,
        registry: SourceRegistry,
        cutoff_at: datetime,
    ) -> list[Claim]:
        subject_terms = _subject_terms(target)
        entry = registry.get(version.source_id)
        claims: list[Claim] = []
        passages = 0

        for span in document.spans:
            if passages >= self._max_passages or len(claims) >= self._max_claims:
                break
            sentences = split_sentences(span.text)
            matched_in_span = False
            for sentence in sentences:
                if len(sentence) < 20 or not _matches_subject(sentence, subject_terms):
                    continue
                category = _classify(sentence, plan)
                if category is None:
                    continue
                matched_in_span = True
                modality = _modality(sentence)
                stance = _stance(category, sentence, modality)
                predicate = _predicate(category, sentence, modality)
                claim_text = sentence[:2000]
                digest = quote_hash(claim_text)
                claim_id = uuid5(
                    _CLAIM_NAMESPACE,
                    f"{version.version_id}:{span.locator}:{digest}:{predicate}",
                )
                quality_flags: list[str] = []
                if modality is ClaimModality.SPECULATED:
                    quality_flags.append("RUMOR_LANGUAGE_PRESERVED")
                if modality is ClaimModality.NOT_STATED:
                    quality_flags.append("ABSENCE_OF_EVIDENCE")
                if modality is ClaimModality.CONDITIONAL:
                    quality_flags.append("CONDITIONAL_LANGUAGE_PRESERVED")
                if entry is None:
                    quality_flags.append("SOURCE_NOT_IN_REGISTRY")

                authoritative = bool(
                    entry
                    and entry.authority_tier == 1
                    and category in entry.claim_categories
                    and entry.source_type
                    in {
                        SourceType.OFFICIAL,
                        SourceType.GOVERNMENT,
                        SourceType.REGULATOR,
                        SourceType.SPORTS_AUTHORITY,
                    }
                )
                support = (
                    SupportState.AUTHORITATIVE_PRIMARY_SOURCE
                    if authoritative
                    else SupportState.SINGLE_SOURCE
                )
                claims.append(
                    Claim(
                        claim_id=claim_id,
                        job_id=job_id,
                        target_id=target.target_id,
                        claim_text=claim_text,
                        claim_category=category,
                        subject_entity_ids=[
                            entity.canonical_id or entity.name
                            for entity in target.subject_entities
                            if entity.name.lower() in sentence.lower()
                            or any(alias.lower() in sentence.lower() for alias in entity.aliases)
                        ],
                        predicate=predicate,
                        object_value=target.deadline_at.isoformat() if target.deadline_at else None,
                        stance_on_proposition=stance,
                        claim_modality=modality,
                        epistemic_state=_epistemic(modality),
                        source_id=version.source_id,
                        document_version_id=version.version_id,
                        evidence_span=EvidenceSpan(
                            kind=span.kind,
                            locator=span.locator,
                            quote_hash=digest,
                            excerpt=claim_text[:1000],
                            char_start=span.char_start,
                            char_end=span.char_end,
                        ),
                        published_at=version.published_at,
                        available_at=version.available_at,
                        independence_group_id=str(version.document_id),
                        extraction_model_version="rule-based-claims-v1",
                        extraction_prompt_version="claims-v1",
                        schema_version="claims-v1",
                        support_state=support,
                        eligibility_state=_eligibility(version, cutoff_at),
                        validation_state=ValidationState.SCHEMA_VALIDATED,
                        quality_flags=quality_flags,
                    )
                )
            if matched_in_span:
                passages += 1
        return claims[: self._max_claims]


class LLMClaimItem(BaseModel):
    """Strict structured-output contract for an optional LLM extractor."""

    model_config = ConfigDict(extra="forbid")

    claim_text: str = Field(min_length=1, max_length=2000)
    claim_category: ClaimCategory
    predicate: str = Field(min_length=1, max_length=128)
    stance_on_proposition: ClaimStance
    claim_modality: ClaimModality
    epistemic_state: EpistemicState
    source_id: str = Field(min_length=1, max_length=128)
    document_version_id: UUID
    span_locator: str = Field(min_length=1, max_length=255)
    quote_hash: str = Field(min_length=1, max_length=80)
    excerpt: str = Field(min_length=1, max_length=1000)
    published_at: datetime | None = None
    available_at: datetime | None = None
    subject_entity_ids: list[str] = Field(default_factory=list, max_length=20)


class LLMClaimOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claims: list[LLMClaimItem] = Field(default_factory=list, max_length=50)


class ClaimValidationError(ValueError):
    """Raised when a model output cannot be tied to captured source metadata."""


def validate_llm_claims(
    payload: dict[str, Any] | LLMClaimOutput,
    *,
    job_id: UUID,
    target: ResearchTarget,
    versions_by_id: dict[UUID, DocumentVersion],
    spans_by_version: dict[UUID, set[str]],
    cutoff_at: datetime,
    extraction_model_version: str,
    extraction_prompt_version: str,
) -> list[Claim]:
    """Validate a model response before any claim is persisted.

    Rejects unknown source/version IDs, unknown spans, timestamps that do not
    match captured metadata, invalid enums, oversized output, and claims with no
    provenance.
    """

    try:
        output = (
            payload
            if isinstance(payload, LLMClaimOutput)
            else LLMClaimOutput.model_validate(payload)
        )
    except ValidationError as err:
        raise ClaimValidationError(f"Structured claim output failed schema validation: {err}") from err

    claims: list[Claim] = []
    for item in output.claims:
        version = versions_by_id.get(item.document_version_id)
        if version is None:
            raise ClaimValidationError("Claim references a document version not loaded by the worker.")
        if item.source_id != version.source_id:
            raise ClaimValidationError("Claim source_id does not match the document version source.")
        allowed_spans = spans_by_version.get(item.document_version_id, set())
        if item.span_locator not in allowed_spans:
            raise ClaimValidationError("Claim span locator is not present in the captured document.")
        if item.published_at is not None and item.published_at != version.published_at:
            raise ClaimValidationError("Claim published_at does not match captured source metadata.")
        if item.available_at is not None and item.available_at != version.available_at:
            raise ClaimValidationError("Claim available_at does not match captured source metadata.")
        if item.quote_hash != quote_hash(item.excerpt):
            raise ClaimValidationError("Claim quote_hash does not match the supplied excerpt.")

        claims.append(
            Claim(
                claim_id=uuid5(
                    _CLAIM_NAMESPACE,
                    f"{version.version_id}:{item.span_locator}:{item.quote_hash}:{item.predicate}",
                ),
                job_id=job_id,
                target_id=target.target_id,
                claim_text=item.claim_text,
                claim_category=item.claim_category,
                subject_entity_ids=item.subject_entity_ids,
                predicate=item.predicate,
                stance_on_proposition=item.stance_on_proposition,
                claim_modality=item.claim_modality,
                epistemic_state=item.epistemic_state,
                source_id=version.source_id,
                document_version_id=version.version_id,
                evidence_span=EvidenceSpan(
                    kind="html_paragraph",
                    locator=item.span_locator,
                    quote_hash=item.quote_hash,
                    excerpt=item.excerpt,
                ),
                published_at=version.published_at,
                available_at=version.available_at,
                independence_group_id=str(version.document_id),
                extraction_model_version=extraction_model_version,
                extraction_prompt_version=extraction_prompt_version,
                schema_version="claims-v1",
                eligibility_state=_eligibility(version, cutoff_at),
                validation_state=ValidationState.PENDING_REVIEW,
            )
        )
    return claims


def extract_with_one_repair(
    call_model: Callable[[str | None], dict[str, Any] | LLMClaimOutput],
    *,
    validator: Callable[[dict[str, Any] | LLMClaimOutput], list[Claim]],
) -> list[Claim]:
    """Allow exactly one schema-repair attempt, then fail closed."""

    try:
        return validator(call_model(None))
    except ClaimValidationError as first_error:
        try:
            return validator(call_model(str(first_error)))
        except ClaimValidationError as second_error:
            raise ClaimValidationError(
                f"Claim extraction failed after one bounded repair attempt: {second_error}"
            ) from first_error
