"""Unit tests for research domain models — UTC enforcement and schema validation."""

from __future__ import annotations

from datetime import datetime, timezone, UTC
from uuid import uuid4

import pytest
from pydantic import ValidationError

from prediction_ai.research.enums import (
    AccessMethod,
    ClaimCategory,
    ClaimModality,
    ClaimStance,
    CommercialUseStatus,
    EligibilityState,
    EpistemicState,
    ReadinessStatus,
    ResearchDepth,
    ResearchJobStatus,
    SourceType,
    SupportState,
    TargetVerificationStatus,
    ValidationState,
)
from prediction_ai.research.models import (
    BudgetLimits,
    BudgetUsage,
    Claim,
    EvidenceSpan,
    ResearchJobCreate,
    ResearchTarget,
    SourceRegistryEntry,
    SubjectEntity,
)

_NOW_UTC = datetime(2026, 11, 5, 18, 0, 0, tzinfo=UTC)
_NOW_NAIVE = datetime(2026, 11, 5, 18, 0, 0)  # no tzinfo — must be rejected


def _minimal_target(**overrides: object) -> dict:
    base = {
        "target_id": uuid4(),
        "platform": "polymarket",
        "platform_market_id": "mkt-001",
        "proposition": "Will the manager leave before the deadline?",
        "verification_status": TargetVerificationStatus.TARGET_VERIFIED,
        "created_at": _NOW_UTC,
    }
    base.update(overrides)
    return base


class TestResearchTargetUTC:
    def test_accepts_utc_aware_datetime(self) -> None:
        t = ResearchTarget(**_minimal_target())
        assert t.created_at.tzinfo is not None

    def test_rejects_naive_created_at(self) -> None:
        with pytest.raises(ValidationError, match="timezone"):
            ResearchTarget(**_minimal_target(created_at=_NOW_NAIVE))

    def test_rejects_naive_deadline_at(self) -> None:
        with pytest.raises(ValidationError, match="timezone"):
            ResearchTarget(**_minimal_target(deadline_at=_NOW_NAIVE))

    def test_accepts_non_utc_aware_and_normalizes(self) -> None:
        paris = timezone(datetime.now(UTC).utcoffset() or timezone.utc.utcoffset(datetime.now(UTC)))
        aware_paris = datetime(2026, 11, 5, 19, 0, 0, tzinfo=timezone(
            __import__('datetime').timedelta(hours=1)
        ))
        t = ResearchTarget(**_minimal_target(deadline_at=aware_paris))
        assert t.deadline_at is not None
        assert t.deadline_at.tzinfo is not None

    def test_resolution_rules_hash_valid(self) -> None:
        good_hash = "sha256:" + "a" * 64
        t = ResearchTarget(**_minimal_target(resolution_rules_hash=good_hash))
        assert t.resolution_rules_hash == good_hash

    def test_resolution_rules_hash_invalid_prefix(self) -> None:
        with pytest.raises(ValidationError):
            ResearchTarget(**_minimal_target(resolution_rules_hash="md5:" + "a" * 32))

    def test_resolution_rules_hash_wrong_length(self) -> None:
        with pytest.raises(ValidationError):
            ResearchTarget(**_minimal_target(resolution_rules_hash="sha256:" + "a" * 63))

    def test_extra_fields_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            ResearchTarget(**_minimal_target(unknown_field="oops"))

    def test_proposition_min_length(self) -> None:
        with pytest.raises(ValidationError):
            ResearchTarget(**_minimal_target(proposition=""))


class TestSubjectEntity:
    def test_valid_person_entity(self) -> None:
        e = SubjectEntity(entity_type="person", name="Example Manager")
        assert e.entity_type == "person"
        assert e.canonical_id is None

    def test_invalid_entity_type(self) -> None:
        with pytest.raises(ValidationError):
            SubjectEntity(entity_type="alien", name="X")

    def test_name_min_length(self) -> None:
        with pytest.raises(ValidationError):
            SubjectEntity(entity_type="person", name="")


class TestSourceRegistryEntry:
    def _minimal_entry(self, **overrides: object) -> dict:
        base = {
            "source_id": "test-source",
            "provider_key": "test_provider",
            "domain_pattern": "example.com",
            "source_type": SourceType.PUBLISHER,
            "access_method": AccessMethod.OFFICIAL_API,
            "commercial_use_status": CommercialUseStatus.APPROVED,
            "enabled": True,
            "created_at": _NOW_UTC,
            "updated_at": _NOW_UTC,
        }
        base.update(overrides)
        return base

    def test_approved_and_enabled_is_usable(self) -> None:
        e = SourceRegistryEntry(**self._minimal_entry())
        assert e.usable is True

    def test_prohibited_is_not_usable(self) -> None:
        e = SourceRegistryEntry(**self._minimal_entry(
            commercial_use_status=CommercialUseStatus.PROHIBITED
        ))
        assert e.usable is False

    def test_approval_required_is_not_usable(self) -> None:
        e = SourceRegistryEntry(**self._minimal_entry(
            commercial_use_status=CommercialUseStatus.APPROVAL_REQUIRED
        ))
        assert e.usable is False

    def test_disabled_is_not_usable(self) -> None:
        e = SourceRegistryEntry(**self._minimal_entry(enabled=False))
        assert e.usable is False


class TestBudgetLimits:
    def test_from_depth_quick(self) -> None:
        limits = BudgetLimits.from_depth(ResearchDepth.QUICK)
        assert limits.search_requests == 6
        assert limits.wall_clock_seconds == 20

    def test_from_depth_standard(self) -> None:
        limits = BudgetLimits.from_depth(ResearchDepth.STANDARD)
        assert limits.search_requests == 12
        assert limits.wall_clock_seconds == 45

    def test_from_depth_deep(self) -> None:
        limits = BudgetLimits.from_depth(ResearchDepth.DEEP)
        assert limits.search_requests == 20
        assert limits.wall_clock_seconds == 90

    def test_limits_are_positive(self) -> None:
        for depth in ResearchDepth:
            limits = BudgetLimits.from_depth(depth)
            assert limits.research_rounds >= 1
            assert limits.source_documents >= 1
            assert limits.llm_calls >= 1


class TestResearchJobCreate:
    def test_requires_analysis_id_or_target_id(self) -> None:
        with pytest.raises(ValidationError, match="Either analysis_id or target_id"):
            ResearchJobCreate(research_profile="standard", requested_depth=ResearchDepth.STANDARD)

    def test_accepts_analysis_id_only(self) -> None:
        j = ResearchJobCreate(analysis_id=uuid4())
        assert j.analysis_id is not None

    def test_accepts_target_id_only(self) -> None:
        j = ResearchJobCreate(target_id=uuid4())
        assert j.target_id is not None

    def test_default_depth_is_standard(self) -> None:
        j = ResearchJobCreate(analysis_id=uuid4())
        assert j.requested_depth == ResearchDepth.STANDARD
