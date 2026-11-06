"""Unit tests for deterministic claim extraction and LLM output validation."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from prediction_ai.research.enums import (
    ClaimCategory,
    ClaimModality,
    ClaimStance,
    EpistemicState,
)
from prediction_ai.research.claims import (
    ClaimExtractor,
    LLMClaimCandidate,
    validate_llm_claims,
)
from prediction_ai.research.enums import (
    AccessMethod,
    CommercialUseStatus,
    EligibilityState,
    SourceType,
    SupportState,
    TargetVerificationStatus,
    ValidationState,
)
from prediction_ai.research.models import (
    DocumentVersion,
    ExtractedDocument,
    ResearchTarget,
    SourceRegistryEntry,
    SubjectEntity,
    TextSpan,
)

_NOW = datetime(2026, 11, 5, 18, 0, 0, tzinfo=UTC)
_BEFORE = datetime(2026, 11, 5, 8, 0, 0, tzinfo=UTC)


def _doc_version() -> DocumentVersion:
    return DocumentVersion(
        version_id=uuid4(),
        document_id=uuid4(),
        source_id="test-source",
        content_hash="sha256:" + "a" * 64,
        capture_method="approved_publisher_page",
        extraction_version="html-extractor-1.0",
        captured_at=_BEFORE,
        available_at=_BEFORE,
        availability_source="capture",
        published_at=_BEFORE,
        version_is_valid=True,
        created_at=_BEFORE,
    )


def _extracted_doc(text: str) -> ExtractedDocument:
    return ExtractedDocument(
        canonical_url="https://example.com/article",
        final_url="https://example.com/article",
        title="Test Article",
        publisher="Sports News",
        author="Reporter",
        published_at=_BEFORE,
        text=text,
        spans=[
            TextSpan(
                kind="html_paragraph",
                locator="paragraph:0",
                text=text[:200],
                char_start=0,
                char_end=len(text),
                quote_hash="sha256:" + "b" * 64,
            )
        ],
        content_type="text/html",
        extraction_version="html-extractor-1.0",
        quality_flags=[],
    )


def _target() -> ResearchTarget:
    return ResearchTarget(
        target_id=uuid4(),
        platform="polymarket",
        platform_market_id="mkt-001",
        proposition="Will the manager leave Test Club before the deadline?",
        verification_status=TargetVerificationStatus.TARGET_VERIFIED,
        created_at=_NOW,
        subject_entities=[
            SubjectEntity(entity_type="person", name="Test Manager"),
            SubjectEntity(entity_type="organization", name="Test Club"),
        ],
    )


def _source_entry() -> SourceRegistryEntry:
    return SourceRegistryEntry(
        source_id="test-source",
        provider_key="test",
        domain_pattern="example.com",
        source_type=SourceType.PUBLISHER,
        access_method=AccessMethod.OFFICIAL_API,
        commercial_use_status=CommercialUseStatus.APPROVED,
        enabled=True,
        created_at=_NOW,
        updated_at=_NOW,
    )


class TestDeterministicExtractor:
    def _extractor(self) -> ClaimExtractor:
        return ClaimExtractor()

    def test_departure_claim_detected(self) -> None:
        doc = _extracted_doc("Test Club officially announced the manager will leave at the end of the season.")
        version = _doc_version()
        target = _target()
        claims = self._extractor().extract_from_document(doc, version=version, target=target, cutoff_at=_NOW)
        categories = {c.claim_category for c in claims}
        assert ClaimCategory.MANAGER_DEPARTURE in categories

    def test_denial_preserved_as_denial(self) -> None:
        doc = _extracted_doc("The manager denied reports of his imminent departure and said he will stay.")
        version = _doc_version()
        target = _target()
        claims = self._extractor().extract_from_document(doc, version=version, target=target, cutoff_at=_NOW)
        # At least one claim should have DENIED modality or SUPPORTS_NO stance
        modalities = {c.claim_modality for c in claims}
        stances = {c.stance_on_proposition for c in claims}
        assert ClaimModality.DENIED in modalities or ClaimStance.SUPPORTS_NO in stances

    def test_speculative_language_preserved(self) -> None:
        doc = _extracted_doc("Sources say the manager could leave if the team fails to win this weekend.")
        version = _doc_version()
        target = _target()
        claims = self._extractor().extract_from_document(doc, version=version, target=target, cutoff_at=_NOW)
        modalities = {c.claim_modality for c in claims}
        # Conditional or speculated — must NOT be asserted or official_statement
        assert ClaimModality.OFFICIAL_STATEMENT not in modalities

    def test_every_claim_has_evidence_span(self) -> None:
        doc = _extracted_doc("Test Manager officially left Test Club today in a statement.")
        version = _doc_version()
        target = _target()
        claims = self._extractor().extract_from_document(doc, version=version, target=target, cutoff_at=_NOW)
        for claim in claims:
            assert claim.evidence_span is not None
            assert claim.evidence_span.locator is not None

    def test_every_claim_has_source_id(self) -> None:
        doc = _extracted_doc("Test Manager officially left Test Club today.")
        version = _doc_version()
        target = _target()
        claims = self._extractor().extract_from_document(doc, version=version, target=target, cutoff_at=_NOW)
        for claim in claims:
            assert claim.source_id is not None

    def test_every_claim_has_document_version_id(self) -> None:
        doc = _extracted_doc("Test Manager officially left Test Club today.")
        version = _doc_version()
        target = _target()
        claims = self._extractor().extract_from_document(doc, version=version, target=target, cutoff_at=_NOW)
        for claim in claims:
            assert claim.document_version_id == version.version_id


class TestLLMClaimValidation:
    """LLM claim output must be rejected unless it validates against known source IDs and spans."""

    def _known_ids(self) -> tuple[set[str], set[str]]:
        source_id = "test-source"
        version_id = str(uuid4())
        return {source_id}, {version_id}

    def test_valid_candidate_accepted(self) -> None:
        source_ids, version_ids = self._known_ids()
        version_id = next(iter(version_ids))
        source_id = next(iter(source_ids))

        candidate = LLMClaimCandidate(
            claim_text="The manager officially departed.",
            claim_category=ClaimCategory.MANAGER_DEPARTURE,
            stance_on_proposition=ClaimStance.SUPPORTS_YES,
            claim_modality=ClaimModality.OFFICIAL_STATEMENT,
            epistemic_state=EpistemicState.ASSERTED,
            source_id=source_id,
            document_version_id=version_id,
            evidence_span_locator="paragraph:1",
            evidence_span_quote_hash="sha256:" + "c" * 64,
        )
        results = validate_llm_claims([candidate], known_source_ids=source_ids, known_version_ids=version_ids)
        assert len(results) == 1

    def test_missing_source_id_rejected(self) -> None:
        source_ids, version_ids = self._known_ids()
        version_id = next(iter(version_ids))

        candidate = LLMClaimCandidate(
            claim_text="The manager officially departed.",
            claim_category=ClaimCategory.MANAGER_DEPARTURE,
            stance_on_proposition=ClaimStance.SUPPORTS_YES,
            claim_modality=ClaimModality.OFFICIAL_STATEMENT,
            epistemic_state=EpistemicState.ASSERTED,
            source_id="unknown-source",   # <- not in known_source_ids
            document_version_id=version_id,
            evidence_span_locator="paragraph:1",
            evidence_span_quote_hash="sha256:" + "c" * 64,
        )
        results = validate_llm_claims([candidate], known_source_ids=source_ids, known_version_ids=version_ids)
        assert len(results) == 0  # rejected

    def test_missing_evidence_span_rejected(self) -> None:
        source_ids, version_ids = self._known_ids()
        version_id = next(iter(version_ids))
        source_id = next(iter(source_ids))

        candidate = LLMClaimCandidate(
            claim_text="The manager officially departed.",
            claim_category=ClaimCategory.MANAGER_DEPARTURE,
            stance_on_proposition=ClaimStance.SUPPORTS_YES,
            claim_modality=ClaimModality.OFFICIAL_STATEMENT,
            epistemic_state=EpistemicState.ASSERTED,
            source_id=source_id,
            document_version_id=version_id,
            evidence_span_locator=None,   # <- missing span locator
            evidence_span_quote_hash=None,
        )
        results = validate_llm_claims([candidate], known_source_ids=source_ids, known_version_ids=version_ids)
        assert len(results) == 0

    def test_unknown_version_id_rejected(self) -> None:
        source_ids, version_ids = self._known_ids()
        source_id = next(iter(source_ids))

        candidate = LLMClaimCandidate(
            claim_text="The manager officially departed.",
            claim_category=ClaimCategory.MANAGER_DEPARTURE,
            stance_on_proposition=ClaimStance.SUPPORTS_YES,
            claim_modality=ClaimModality.OFFICIAL_STATEMENT,
            epistemic_state=EpistemicState.ASSERTED,
            source_id=source_id,
            document_version_id=str(uuid4()),  # <- not in known_version_ids
            evidence_span_locator="paragraph:1",
            evidence_span_quote_hash="sha256:" + "c" * 64,
        )
        results = validate_llm_claims([candidate], known_source_ids=source_ids, known_version_ids=version_ids)
        assert len(results) == 0
