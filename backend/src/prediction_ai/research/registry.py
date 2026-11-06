"""Approved source registry.

A source is disabled until its access method, commercial rights, terms, robots
review, rate limits, retention, and permitted uses have been recorded. A
successful HTTP request is never treated as permission.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import structlog

from prediction_ai.research.enums import (
    AccessMethod,
    ClaimCategory,
    CommercialUseStatus,
    SourceType,
)
from prediction_ai.research.models import SourceRegistryEntry
from prediction_ai.research.ssrf import hostname_allowed

logger = structlog.get_logger(__name__)

DEFAULT_REGISTRY_VERSION = "sources-v1"
_REGISTRY_CREATED_AT = datetime(2026, 11, 5, tzinfo=UTC)


class SourceNotApprovedError(Exception):
    def __init__(self, source_id: str, reason: str) -> None:
        self.source_id = source_id
        self.reason = reason
        super().__init__(f"Source '{source_id}' is not approved for research: {reason}")


def _default_entries() -> list[SourceRegistryEntry]:
    """Safe defaults: every external source starts disabled or prohibited."""

    return [
        SourceRegistryEntry(
            source_id="gdelt_project_doc",
            provider_key="gdelt_project",
            domain_pattern="api.gdeltproject.org",
            source_type=SourceType.AGGREGATOR,
            allowed_market_types=[],
            claim_categories=[ClaimCategory.REPORTED_NEWS, ClaimCategory.OTHER],
            access_method=AccessMethod.OFFICIAL_API,
            commercial_use_status=CommercialUseStatus.UNDER_REVIEW,
            terms_url="https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/",
            license_or_contract_ref=None,
            robots_reviewed_at=None,
            terms_reviewed_at=None,
            retention_policy="metadata-only",
            max_requests_per_minute=10,
            max_concurrency=1,
            max_response_bytes=2_000_000,
            default_freshness_seconds=86_400,
            source_authority_ruleset_id="authority-v1",
            authority_tier=3,
            enabled=False,
            registry_version=DEFAULT_REGISTRY_VERSION,
            created_at=_REGISTRY_CREATED_AT,
            updated_at=_REGISTRY_CREATED_AT,
        ),
        SourceRegistryEntry(
            source_id="google_news_rss",
            provider_key="google_news",
            domain_pattern="news.google.com",
            source_type=SourceType.AGGREGATOR,
            allowed_market_types=[],
            claim_categories=[ClaimCategory.REPORTED_NEWS],
            access_method=AccessMethod.RSS,
            commercial_use_status=CommercialUseStatus.PROHIBITED,
            terms_url="https://www.about.google/policies/terms/",
            license_or_contract_ref=None,
            robots_reviewed_at=None,
            terms_reviewed_at=None,
            retention_policy="prohibited",
            max_requests_per_minute=1,
            max_concurrency=1,
            max_response_bytes=1_000_000,
            default_freshness_seconds=86_400,
            source_authority_ruleset_id="authority-v1",
            authority_tier=3,
            enabled=False,
            registry_version=DEFAULT_REGISTRY_VERSION,
            created_at=_REGISTRY_CREATED_AT,
            updated_at=_REGISTRY_CREATED_AT,
        ),
        SourceRegistryEntry(
            source_id="reddit_public",
            provider_key="reddit",
            domain_pattern="www.reddit.com",
            source_type=SourceType.SOCIAL,
            allowed_market_types=[],
            claim_categories=[ClaimCategory.REPORTED_NEWS, ClaimCategory.OTHER],
            access_method=AccessMethod.PUBLIC_PAGE,
            commercial_use_status=CommercialUseStatus.PROHIBITED,
            terms_url="https://redditinc.com/policies/data-api-terms",
            license_or_contract_ref=None,
            robots_reviewed_at=None,
            terms_reviewed_at=None,
            retention_policy="prohibited",
            max_requests_per_minute=1,
            max_concurrency=1,
            max_response_bytes=1_000_000,
            default_freshness_seconds=86_400,
            source_authority_ruleset_id="authority-v1",
            authority_tier=3,
            enabled=False,
            registry_version=DEFAULT_REGISTRY_VERSION,
            created_at=_REGISTRY_CREATED_AT,
            updated_at=_REGISTRY_CREATED_AT,
        ),
    ]


class SourceRegistry:
    """Versioned collection of reviewed sources with claim-specific authority."""

    def __init__(self, entries: list[SourceRegistryEntry], version: str) -> None:
        self._entries = {entry.source_id: entry for entry in entries}
        self.version = version

    @classmethod
    def default(cls) -> "SourceRegistry":
        return cls(_default_entries(), DEFAULT_REGISTRY_VERSION)

    @classmethod
    def from_file(cls, path: str | Path) -> "SourceRegistry":
        registry_path = Path(path)
        if not registry_path.exists():
            logger.warning(
                "Research source registry file missing; using disabled defaults",
                path=str(registry_path),
            )
            return cls.default()

        try:
            payload = json.loads(registry_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as err:
            logger.error(
                "Research source registry file is unreadable; using disabled defaults",
                path=str(registry_path),
                error=str(err),
            )
            return cls.default()

        if not isinstance(payload, dict) or not isinstance(payload.get("sources"), list):
            logger.error(
                "Research source registry file has an invalid shape; using disabled defaults",
                path=str(registry_path),
            )
            return cls.default()

        entries: list[SourceRegistryEntry] = []
        for raw in payload["sources"]:
            try:
                entries.append(SourceRegistryEntry.model_validate(raw))
            except ValueError as err:
                logger.error(
                    "Rejected invalid source registry entry",
                    error=str(err),
                )
        version = str(payload.get("registry_version") or DEFAULT_REGISTRY_VERSION)
        if not entries:
            return cls(_default_entries(), version)
        return cls(entries, version)

    @classmethod
    def from_entries(
        cls, entries: list[SourceRegistryEntry], *, version: str = DEFAULT_REGISTRY_VERSION
    ) -> "SourceRegistry":
        return cls(entries, version)

    def all(self) -> list[SourceRegistryEntry]:
        return sorted(self._entries.values(), key=lambda entry: entry.source_id)

    def get(self, source_id: str) -> SourceRegistryEntry | None:
        return self._entries.get(source_id)

    def entry_for_url(self, url: str) -> SourceRegistryEntry | None:
        host = ""
        try:
            from urllib.parse import urlsplit

            host = urlsplit(url).hostname or ""
        except ValueError:
            return None
        if not host:
            return None
        matches = [
            entry
            for entry in self._entries.values()
            if hostname_allowed(host, [entry.domain_pattern])
        ]
        if not matches:
            return None
        return max(matches, key=lambda entry: len(entry.domain_pattern))

    def approved_for(
        self,
        market_type: str,
        claim_category: ClaimCategory | None = None,
    ) -> list[SourceRegistryEntry]:
        entries = []
        for entry in self._entries.values():
            if not entry.usable:
                continue
            if entry.allowed_market_types and market_type not in entry.allowed_market_types:
                continue
            if claim_category is not None and entry.claim_categories:
                if claim_category not in entry.claim_categories:
                    continue
            entries.append(entry)
        return sorted(entries, key=lambda entry: (entry.authority_tier, entry.source_id))

    def require_usable(
        self,
        source_id: str,
        *,
        market_type: str,
        claim_category: ClaimCategory | None = None,
    ) -> SourceRegistryEntry:
        entry = self._entries.get(source_id)
        if entry is None:
            raise SourceNotApprovedError(source_id, "not registered")
        if not entry.usable:
            raise SourceNotApprovedError(
                source_id,
                f"commercial_use_status={entry.commercial_use_status.value}, enabled={entry.enabled}",
            )
        if entry.allowed_market_types and market_type not in entry.allowed_market_types:
            raise SourceNotApprovedError(
                source_id, f"not allowed for market type {market_type}"
            )
        if claim_category is not None and entry.claim_categories:
            if claim_category not in entry.claim_categories:
                raise SourceNotApprovedError(
                    source_id, f"not allowed for claim category {claim_category.value}"
                )
        return entry

    def require_usable_for_url(self, url: str, *, market_type: str) -> SourceRegistryEntry:
        entry = self.entry_for_url(url)
        if entry is None:
            raise SourceNotApprovedError(url, "domain is not registered")
        return self.require_usable(entry.source_id, market_type=market_type)

    def authority_tier(self, source_id: str) -> int:
        entry = self._entries.get(source_id)
        return entry.authority_tier if entry else 3

    def registry_version(self) -> str:
        return self.version
