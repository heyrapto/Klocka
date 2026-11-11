"""Unit tests for deterministic claim extraction and LLM output validation."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest

from prediction_ai.research.claims import (
    ClaimExtractor,
    ClaimValidationError,
    LLMClaimItem,
    LLMClaimOutput,
    validate_llm_claims,
)
from prediction_ai.research.enums import (
    AccessMethod,
    ClaimCategory,
    ClaimModality,
    ClaimStance,
    CommercialUseStatus,
    EpistemicState,
    SourceType,
    TargetVerificationStatus,
    ValidationState,
)
from prediction_ai.research.extract import quote_hash
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
        canonical_url="https://example.com/article",
        final_url="https://example.com/article",
        title="Test Article",
        content_hash="sha256:" + "a" * 64,
        capture_method="approved_publisher_page",
        extraction_version="html-extractor-1.0",
        first_seen_at=_BEFORE,
        captured_at=_BEFORE,
        available_at=_BEFORE,
        availability_source="runtime_capture",
        published_at=_BEFORE,
        version_is_valid=True,
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
        terms_reviewed_at=_NOW,
        robots_reviewed_at=_NOW,
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
    """LLM output must validate against the captured source, version, span, and quote."""

    def _context(
        self,
    ) -> tuple[DocumentVersion, dict[UUID, DocumentVersion], dict[UUID, set[str]]]:
        version = _doc_version()
        return (
            version,
            {version.version_id: version},
            {version.version_id: {"paragraph:0"}},
        )

    def _item(self, version: DocumentVersion, **overrides: Any) -> LLMClaimItem:
        excerpt = str(overrides.pop("excerpt", "The manager officially departed."))
        base: dict[str, Any] = {
            "claim_text": excerpt,
            "claim_category": ClaimCategory.MANAGER_DEPARTURE,
            "predicate": "announced_departure",
            "stance_on_proposition": ClaimStance.SUPPORTS_YES,
            "claim_modality": ClaimModality.OFFICIAL_STATEMENT,
            "epistemic_state": EpistemicState.ASSERTED,
            "source_id": version.source_id,
            "document_version_id": version.version_id,
            "span_locator": "paragraph:0",
            "quote_hash": quote_hash(excerpt),
            "excerpt": excerpt,
        }
        base.update(overrides)
        return LLMClaimItem(**base)

    def _validate(
        self,
        items: list[LLMClaimItem],
        context: tuple[DocumentVersion, dict[UUID, DocumentVersion], dict[UUID, set[str]]],
    ) -> list:
        _, versions_by_id, spans_by_version = context
        return validate_llm_claims(
            LLMClaimOutput(claims=items),
            job_id=uuid4(),
            target=_target(),
            versions_by_id=versions_by_id,
            spans_by_version=spans_by_version,
            cutoff_at=_NOW,
            extraction_model_version="test-extractor-v1",
            extraction_prompt_version="test-prompt-v1",
        )

    def test_valid_candidate_accepted(self) -> None:
        version, versions_by_id, spans_by_version = self._context()
        results = validate_llm_claims(
            LLMClaimOutput(claims=[self._item(version)]),
            job_id=uuid4(),
            target=_target(),
            versions_by_id=versions_by_id,
            spans_by_version=spans_by_version,
            cutoff_at=_NOW,
            extraction_model_version="test-extractor-v1",
            extraction_prompt_version="test-prompt-v1",
        )
        assert len(results) == 1
        assert results[0].source_id == version.source_id
        assert results[0].document_version_id == version.version_id
        assert results[0].evidence_span.locator == "paragraph:0"
        assert results[0].validation_state is ValidationState.PENDING_REVIEW

    def test_missing_source_id_rejected(self) -> None:
        version, versions_by_id, spans_by_version = self._context()
        with pytest.raises(ClaimValidationError, match="source_id"):
            validate_llm_claims(
                LLMClaimOutput(
                    claims=[self._item(version, source_id="unknown-source")]
                ),
                job_id=uuid4(),
                target=_target(),
                versions_by_id=versions_by_id,
                spans_by_version=spans_by_version,
                cutoff_at=_NOW,
                extraction_model_version="test-extractor-v1",
                extraction_prompt_version="test-prompt-v1",
            )

    def test_missing_evidence_span_rejected(self) -> None:
        version, versions_by_id, spans_by_version = self._context()
        with pytest.raises(ClaimValidationError, match="span locator"):
            validate_llm_claims(
                LLMClaimOutput(
                    claims=[self._item(version, span_locator="paragraph:99")]
                ),
                job_id=uuid4(),
                target=_target(),
                versions_by_id=versions_by_id,
                spans_by_version=spans_by_version,
                cutoff_at=_NOW,
                extraction_model_version="test-extractor-v1",
                extraction_prompt_version="test-prompt-v1",
            )

    def test_unknown_version_id_rejected(self) -> None:
        version, versions_by_id, spans_by_version = self._context()
        with pytest.raises(ClaimValidationError, match="document version"):
            validate_llm_claims(
                LLMClaimOutput(
                    claims=[self._item(version, document_version_id=uuid4())]
                ),
                job_id=uuid4(),
                target=_target(),
                versions_by_id=versions_by_id,
                spans_by_version=spans_by_version,
                cutoff_at=_NOW,
                extraction_model_version="test-extractor-v1",
                extraction_prompt_version="test-prompt-v1",
            )

    def test_quote_hash_mismatch_rejected(self) -> None:
        version, versions_by_id, spans_by_version = self._context()
        with pytest.raises(ClaimValidationError, match="quote_hash"):
            validate_llm_claims(
                LLMClaimOutput(
                    claims=[
                        self._item(version, quote_hash="sha256:" + "c" * 64)
                    ]
                ),
                job_id=uuid4(),
                target=_target(),
                versions_by_id=versions_by_id,
                spans_by_version=spans_by_version,
                cutoff_at=_NOW,
                extraction_model_version="test-extractor-v1",
                extraction_prompt_version="test-prompt-v1",
            )
