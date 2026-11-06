"""Unit tests for the server-side research budget ledger."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from prediction_ai.research.budget import BudgetExceededError, BudgetLedger
from prediction_ai.research.enums import BudgetCategory, ResearchDepth
from prediction_ai.research.models import BudgetLimits, BudgetUsage


class FixedClock:
    def __init__(self, t: datetime) -> None:
        self._t = t

    def now(self) -> datetime:
        return self._t

    def advance(self, seconds: float) -> None:
        self._t = self._t + timedelta(seconds=seconds)


_START = datetime(2026, 11, 5, 18, 0, 0, tzinfo=UTC)


def _ledger(depth: ResearchDepth = ResearchDepth.STANDARD, clock: FixedClock | None = None) -> BudgetLedger:
    limits = BudgetLimits.from_depth(depth)
    c = clock or FixedClock(_START)
    return BudgetLedger(limits, c)


class TestBudgetAuthorization:
    def test_first_call_succeeds(self) -> None:
        ledger = _ledger()
        ledger.authorize(BudgetCategory.SEARCH_REQUESTS)  # no exception

    def test_exceeding_limit_raises(self) -> None:
        ledger = _ledger(ResearchDepth.QUICK)
        for _ in range(ledger.limits.search_requests):
            ledger.authorize(BudgetCategory.SEARCH_REQUESTS)
        with pytest.raises(BudgetExceededError) as exc_info:
            ledger.authorize(BudgetCategory.SEARCH_REQUESTS)
        assert exc_info.value.category == BudgetCategory.SEARCH_REQUESTS

    def test_negative_units_raises_value_error(self) -> None:
        ledger = _ledger()
        with pytest.raises(ValueError, match="negative"):
            ledger.authorize(BudgetCategory.SEARCH_REQUESTS, units=-1)

    def test_per_provider_tracking(self) -> None:
        ledger = _ledger()
        ledger.authorize(BudgetCategory.SOURCE_DOCUMENTS, provider="gdelt")
        ledger.authorize(BudgetCategory.SOURCE_DOCUMENTS, provider="gdelt")
        snapshot = ledger.snapshot()
        assert snapshot.per_provider.get("gdelt", 0) == 2

    def test_bytes_downloaded_accumulate(self) -> None:
        ledger = _ledger()
        ledger.authorize(BudgetCategory.BYTES_DOWNLOADED, units=1_000_000)
        ledger.authorize(BudgetCategory.BYTES_DOWNLOADED, units=1_000_000)
        snapshot = ledger.snapshot()
        assert snapshot.counters.get("bytes_downloaded", 0) == 2_000_000

    def test_bytes_exceeding_limit_raises(self) -> None:
        ledger = _ledger(ResearchDepth.QUICK)
        with pytest.raises(BudgetExceededError):
            ledger.authorize(BudgetCategory.BYTES_DOWNLOADED, units=ledger.limits.max_bytes_downloaded + 1)

    def test_llm_calls_limited(self) -> None:
        ledger = _ledger(ResearchDepth.QUICK)
        for _ in range(ledger.limits.llm_calls):
            ledger.authorize(BudgetCategory.LLM_CALLS)
        with pytest.raises(BudgetExceededError):
            ledger.authorize(BudgetCategory.LLM_CALLS)

    def test_structured_output_repairs_limited(self) -> None:
        ledger = _ledger()
        for _ in range(ledger.limits.structured_output_repairs):
            ledger.authorize(BudgetCategory.STRUCTURED_OUTPUT_REPAIRS)
        with pytest.raises(BudgetExceededError):
            ledger.authorize(BudgetCategory.STRUCTURED_OUTPUT_REPAIRS)

    def test_exhausted_flag_set_after_error(self) -> None:
        ledger = _ledger(ResearchDepth.QUICK)
        for _ in range(ledger.limits.search_requests):
            ledger.authorize(BudgetCategory.SEARCH_REQUESTS)
        with pytest.raises(BudgetExceededError):
            ledger.authorize(BudgetCategory.SEARCH_REQUESTS)
        assert ledger.exhausted is True

    def test_snapshot_wall_clock(self) -> None:
        clock = FixedClock(_START)
        ledger = BudgetLedger(BudgetLimits.from_depth(ResearchDepth.STANDARD), clock)
        clock.advance(10)
        ledger.authorize(BudgetCategory.SEARCH_REQUESTS)  # triggers wall clock check
        snap = ledger.snapshot()
        assert snap.wall_clock_seconds >= 10.0

    def test_wall_clock_exhaustion(self) -> None:
        clock = FixedClock(_START)
        ledger = BudgetLedger(BudgetLimits.from_depth(ResearchDepth.QUICK), clock)
        clock.advance(100)  # QUICK budget is 20s
        with pytest.raises(BudgetExceededError):
            ledger.check_wall_clock()

    def test_naive_start_time_rejected(self) -> None:
        from datetime import datetime
        naive = datetime(2026, 11, 5, 18, 0, 0)
        with pytest.raises(ValueError, match="timezone"):
            BudgetLedger(BudgetLimits.from_depth(ResearchDepth.STANDARD), FixedClock(_START), started_at=naive)


class TestBudgetSnapshot:
    def test_snapshot_is_copy(self) -> None:
        ledger = _ledger()
        snap1 = ledger.snapshot()
        ledger.authorize(BudgetCategory.LLM_CALLS)
        snap2 = ledger.snapshot()
        # snap1 should not reflect the new authorization
        assert snap1.counters.get("llm_calls", 0) != snap2.counters.get("llm_calls", 0)
