"""Unit tests for the versioned source registry."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from prediction_ai.research.enums import (
    AccessMethod,
    ClaimCategory,
    CommercialUseStatus,
    SourceType,
)
from prediction_ai.research.models import SourceRegistryEntry
from prediction_ai.research.registry import (
    DEFAULT_REGISTRY_VERSION,
    SourceNotApprovedError,
    SourceRegistry,
)

_NOW = datetime(2026, 11, 5, 18, 0, 0, tzinfo=UTC)


def _entry(
    source_id: str = "test-source",
    *,
    commercial_use_status: CommercialUseStatus = CommercialUseStatus.APPROVED,
    enabled: bool = True,
    allowed_market_types: list[str] | None = None,
    claim_categories: list[ClaimCategory] | None = None,
    authority_tier: int = 2,
    domain_pattern: str = "example.com",
) -> SourceRegistryEntry:
    return SourceRegistryEntry(
        source_id=source_id,
        provider_key="test",
        domain_pattern=domain_pattern,
        source_type=SourceType.PUBLISHER,
        access_method=AccessMethod.OFFICIAL_API,
        commercial_use_status=commercial_use_status,
        allowed_market_types=allowed_market_types or [],
        claim_categories=claim_categories or [],
        enabled=enabled,
        authority_tier=authority_tier,
        created_at=_NOW,
        updated_at=_NOW,
    )


class TestRegistryDefault:
    def test_default_registry_has_no_enabled_entries(self) -> None:
        reg = SourceRegistry.default()
        enabled = [e for e in reg.all() if e.enabled]
        # All default entries must be disabled; none pre-approved without review
        assert all(not e.usable for e in reg.all()), \
            "Default registry must have no usable (enabled+approved) entries"

    def test_default_registry_has_version(self) -> None:
        reg = SourceRegistry.default()
        assert reg.registry_version() == DEFAULT_REGISTRY_VERSION


class TestRegistryApproval:
    def test_approved_and_enabled_returned(self) -> None:
        entry = _entry(allowed_market_types=["match_result"])
        reg = SourceRegistry.from_entries([entry])
        results = reg.approved_for("match_result")
        assert any(e.source_id == "test-source" for e in results)

    def test_prohibited_not_returned(self) -> None:
        entry = _entry(commercial_use_status=CommercialUseStatus.PROHIBITED)
        reg = SourceRegistry.from_entries([entry])
        results = reg.approved_for("match_result")
        assert not any(e.source_id == "test-source" for e in results)

    def test_approval_required_not_returned(self) -> None:
        entry = _entry(commercial_use_status=CommercialUseStatus.APPROVAL_REQUIRED)
        reg = SourceRegistry.from_entries([entry])
        results = reg.approved_for("match_result")
        assert not any(e.source_id == "test-source" for e in results)

    def test_disabled_not_returned(self) -> None:
        entry = _entry(enabled=False)
        reg = SourceRegistry.from_entries([entry])
        results = reg.approved_for("match_result")
        assert not any(e.source_id == "test-source" for e in results)

    def test_market_type_filter(self) -> None:
        entry = _entry(allowed_market_types=["match_result"])
        reg = SourceRegistry.from_entries([entry])
        # Wrong market type
        results = reg.approved_for("policy_announcement")
        assert not any(e.source_id == "test-source" for e in results)

    def test_no_market_type_restriction_matches_all(self) -> None:
        entry = _entry(allowed_market_types=[])  # empty = no restriction
        reg = SourceRegistry.from_entries([entry])
        results = reg.approved_for("any_market_type")
        assert any(e.source_id == "test-source" for e in results)

    def test_claim_category_filter(self) -> None:
        entry = _entry(claim_categories=[ClaimCategory.MATCH_RESULT])
        reg = SourceRegistry.from_entries([entry])
        # Wrong category
        results = reg.approved_for("match_result", claim_category=ClaimCategory.MANAGER_DEPARTURE)
        assert not any(e.source_id == "test-source" for e in results)
        # Correct category
        results = reg.approved_for("match_result", claim_category=ClaimCategory.MATCH_RESULT)
        assert any(e.source_id == "test-source" for e in results)


class TestRequireUsable:
    def test_require_usable_passes_for_approved(self) -> None:
        entry = _entry(source_id="ok-source", allowed_market_types=["match_result"])
        reg = SourceRegistry.from_entries([entry])
        result = reg.require_usable("ok-source", market_type="match_result")
        assert result.source_id == "ok-source"

    def test_require_usable_raises_for_unknown(self) -> None:
        reg = SourceRegistry.from_entries([])
        with pytest.raises(SourceNotApprovedError):
            reg.require_usable("unknown-source", market_type="match_result")

    def test_require_usable_raises_for_prohibited(self) -> None:
        entry = _entry(
            source_id="reddit",
            commercial_use_status=CommercialUseStatus.PROHIBITED,
        )
        reg = SourceRegistry.from_entries([entry])
        with pytest.raises(SourceNotApprovedError):
            reg.require_usable("reddit", market_type="match_result")

    def test_require_usable_raises_for_wrong_market_type(self) -> None:
        entry = _entry(source_id="narrow", allowed_market_types=["match_result"])
        reg = SourceRegistry.from_entries([entry])
        with pytest.raises(SourceNotApprovedError, match="market type"):
            reg.require_usable("narrow", market_type="policy_announcement")


class TestUrlMatching:
    def test_entry_for_url_matches_exact_domain(self) -> None:
        entry = _entry(source_id="gdelt", domain_pattern="api.gdeltproject.org")
        reg = SourceRegistry.from_entries([entry])
        found = reg.entry_for_url("https://api.gdeltproject.org/api/v2/doc/doc")
        assert found is not None
        assert found.source_id == "gdelt"

    def test_entry_for_url_no_match(self) -> None:
        entry = _entry(domain_pattern="example.com")
        reg = SourceRegistry.from_entries([entry])
        found = reg.entry_for_url("https://evil.com/path")
        assert found is None

    def test_require_usable_for_url_unregistered_domain(self) -> None:
        reg = SourceRegistry.from_entries([])
        with pytest.raises(SourceNotApprovedError, match="not registered"):
            reg.require_usable_for_url("https://unknown.com/", market_type="match_result")
