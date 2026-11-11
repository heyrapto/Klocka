"""Server-side research budget enforcement.

A prompt that says "do not exceed 30 calls" is not enforcement. Every provider,
document, model, byte, and wall-clock unit is authorized here before the work
happens. Exhaustion produces a typed error so the runtime can stop and return a
partial evidence pack or an abstention.
"""

from __future__ import annotations

from datetime import UTC, datetime

from prediction_ai.research.enums import BudgetCategory, ResearchDepth
from prediction_ai.research.models import BudgetLimits, BudgetUsage
from prediction_ai.research.time import Clock

_COUNTER_FIELDS: dict[BudgetCategory, str] = {
    BudgetCategory.RESEARCH_ROUNDS: "research_rounds",
    BudgetCategory.SEARCH_REQUESTS: "search_requests",
    BudgetCategory.SOURCE_DOCUMENTS: "source_documents",
    BudgetCategory.BROWSER_PAGES: "browser_pages",
    BudgetCategory.LLM_CALLS: "llm_calls",
    BudgetCategory.STRUCTURED_OUTPUT_REPAIRS: "structured_output_repairs",
    BudgetCategory.PDF_PAGES: "pdf_pages",
    BudgetCategory.OCR_PAGES: "ocr_pages",
    BudgetCategory.REDIRECTS: "redirects",
    BudgetCategory.RETRIES: "retries",
}


class BudgetExceededError(Exception):
    """Raised before a tool call that would exceed a server-side budget."""

    def __init__(
        self,
        category: BudgetCategory,
        *,
        limit: int,
        used: int,
        provider: str | None = None,
    ) -> None:
        self.category = category
        self.limit = limit
        self.used = used
        self.provider = provider
        scope = f" for provider {provider}" if provider else ""
        super().__init__(
            f"Research budget exhausted for {category.value}{scope}: "
            f"used {used}, limit {limit}."
        )


class BudgetLedger:
    """Mutable per-job ledger with a deterministic usage snapshot."""

    def __init__(
        self,
        limits: BudgetLimits,
        clock: Clock,
        *,
        usage: BudgetUsage | None = None,
        started_at: datetime | None = None,
    ) -> None:
        self.limits = limits
        self._clock = clock
        self._counters: dict[str, int] = dict(usage.counters) if usage else {}
        self._per_provider: dict[str, int] = dict(usage.per_provider) if usage else {}
        self._started_at = started_at or clock.now()
        if self._started_at.tzinfo is None:
            raise ValueError("Budget start time must be timezone-aware.")
        self._wall_clock_seconds = usage.wall_clock_seconds if usage else 0.0
        self._exhausted = False

    @property
    def exhausted(self) -> bool:
        return self._exhausted

    def _limit_for(self, category: BudgetCategory) -> int:
        if category is BudgetCategory.MAX_LINK_DEPTH:
            return self.limits.max_link_depth
        if category is BudgetCategory.BYTES_DOWNLOADED:
            return self.limits.max_bytes_downloaded
        field_name = _COUNTER_FIELDS.get(category)
        if field_name is None:
            raise ValueError(f"No budget limit is defined for {category.value}.")
        return int(getattr(self.limits, field_name))

    def _used_for(self, category: BudgetCategory) -> int:
        if category is BudgetCategory.MAX_LINK_DEPTH:
            return 0
        if category is BudgetCategory.BYTES_DOWNLOADED:
            return self._counters.get("bytes_downloaded", 0)
        field_name = _COUNTER_FIELDS[category]
        return self._counters.get(field_name, 0)

    def check_wall_clock(self) -> None:
        elapsed = (self._clock.now() - self._started_at).total_seconds()
        self._wall_clock_seconds = max(self._wall_clock_seconds, elapsed)
        if elapsed > self.limits.wall_clock_seconds:
            self._exhausted = True
            raise BudgetExceededError(
                BudgetCategory.RESEARCH_ROUNDS,
                limit=self.limits.wall_clock_seconds,
                used=int(elapsed),
            )

    def authorize(
        self,
        category: BudgetCategory,
        *,
        units: int = 1,
        provider: str | None = None,
    ) -> None:
        """Reserve budget or raise before the caller performs the work."""

        if units < 0:
            raise ValueError("Budget units cannot be negative.")
        self.check_wall_clock()

        if category is BudgetCategory.MAX_LINK_DEPTH:
            if units > self.limits.max_link_depth:
                self._exhausted = True
                raise BudgetExceededError(
                    category, limit=self.limits.max_link_depth, used=units
                )
            return

        limit = self._limit_for(category)
        used = self._used_for(category)
        if used + units > limit:
            self._exhausted = True
            raise BudgetExceededError(category, limit=limit, used=used)

        if provider and category in {
            BudgetCategory.SEARCH_REQUESTS,
            BudgetCategory.SOURCE_DOCUMENTS,
            BudgetCategory.BROWSER_PAGES,
        }:
            provider_used = self._per_provider.get(provider, 0)
            if provider_used + units > self.limits.max_requests_per_provider:
                self._exhausted = True
                raise BudgetExceededError(
                    category,
                    limit=self.limits.max_requests_per_provider,
                    used=provider_used,
                    provider=provider,
                )

        self._increment(category, units, provider)

    def record(
        self,
        category: BudgetCategory,
        *,
        units: int = 1,
        provider: str | None = None,
    ) -> None:
        """Record usage that was authorized elsewhere (for example bytes read)."""

        if units < 0:
            raise ValueError("Budget units cannot be negative.")
        self._increment(category, units, provider)

    def _increment(self, category: BudgetCategory, units: int, provider: str | None) -> None:
        if category is BudgetCategory.BYTES_DOWNLOADED:
            self._counters["bytes_downloaded"] = (
                self._counters.get("bytes_downloaded", 0) + units
            )
        elif category is not BudgetCategory.MAX_LINK_DEPTH:
            field_name = _COUNTER_FIELDS[category]
            self._counters[field_name] = self._counters.get(field_name, 0) + units
        if provider:
            self._per_provider[provider] = self._per_provider.get(provider, 0) + units

    def snapshot(self) -> BudgetUsage:
        elapsed = (self._clock.now() - self._started_at).total_seconds()
        self._wall_clock_seconds = max(self._wall_clock_seconds, elapsed)
        return BudgetUsage(
            counters=dict(self._counters),
            per_provider=dict(self._per_provider),
            wall_clock_seconds=round(self._wall_clock_seconds, 3),
        )


def limits_for_depth(depth: ResearchDepth) -> BudgetLimits:
    """Starting guardrails from the runtime plan; validate against measured quotas."""

    return BudgetLimits.from_depth(depth)


def utc_started_at(clock: Clock) -> datetime:
    started = clock.now()
    if started.tzinfo is None:
        raise ValueError("Clock must return a timezone-aware UTC timestamp.")
    return started.astimezone(UTC)
