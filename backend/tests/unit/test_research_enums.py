"""Unit tests for research runtime enums and state-machine invariants."""

from __future__ import annotations

import pytest

from prediction_ai.research.enums import (
    ACTIVE_JOB_STATUSES,
    TERMINAL_JOB_STATUSES,
    OutboxStatus,
    ReadinessStatus,
    ResearchDepth,
    ResearchErrorCode,
    ResearchJobStatus,
    SourceFailureCode,
    TargetVerificationStatus,
)


class TestTerminalStatuses:
    def test_terminal_set_is_frozen(self) -> None:
        assert isinstance(TERMINAL_JOB_STATUSES, frozenset)

    def test_active_set_is_frozen(self) -> None:
        assert isinstance(ACTIVE_JOB_STATUSES, frozenset)

    def test_terminal_and_active_are_disjoint(self) -> None:
        overlap = TERMINAL_JOB_STATUSES & ACTIVE_JOB_STATUSES
        assert overlap == frozenset(), f"States appear in both sets: {overlap}"

    def test_terminal_and_active_cover_all_statuses(self) -> None:
        all_statuses = frozenset(ResearchJobStatus)
        covered = TERMINAL_JOB_STATUSES | ACTIVE_JOB_STATUSES
        assert covered == all_statuses

    def test_queued_is_active(self) -> None:
        assert ResearchJobStatus.QUEUED in ACTIVE_JOB_STATUSES

    def test_failed_is_terminal(self) -> None:
        assert ResearchJobStatus.FAILED in TERMINAL_JOB_STATUSES

    def test_cancelled_is_terminal(self) -> None:
        assert ResearchJobStatus.CANCELLED in TERMINAL_JOB_STATUSES

    def test_cancelling_is_active(self) -> None:
        # CANCELLING is a transient state, not yet terminal
        assert ResearchJobStatus.CANCELLING in ACTIVE_JOB_STATUSES

    def test_ready_for_forecast_is_terminal(self) -> None:
        assert ResearchJobStatus.READY_FOR_FORECAST in TERMINAL_JOB_STATUSES

    def test_abstained_is_terminal(self) -> None:
        assert ResearchJobStatus.ABSTAINED in TERMINAL_JOB_STATUSES

    def test_needs_human_review_is_terminal(self) -> None:
        assert ResearchJobStatus.NEEDS_HUMAN_REVIEW in TERMINAL_JOB_STATUSES

    def test_partial_evidence_is_terminal(self) -> None:
        assert ResearchJobStatus.PARTIAL_EVIDENCE in TERMINAL_JOB_STATUSES


class TestStatusValues:
    def test_all_progress_states_are_strings(self) -> None:
        for status in ResearchJobStatus:
            assert isinstance(status.value, str)
            assert status.value == status.value.upper()

    def test_target_verification_values(self) -> None:
        values = {s.value for s in TargetVerificationStatus}
        assert "TARGET_VERIFIED" in values
        assert "TARGET_NEEDS_REVIEW" in values
        assert "TARGET_UNSUPPORTED" in values
        assert "TARGET_ALREADY_RESOLVED" in values
        assert "TARGET_RULES_UNAVAILABLE" in values

    def test_readiness_covers_plan_states(self) -> None:
        values = {s.value for s in ReadinessStatus}
        assert "READY_FOR_FORECAST" in values
        assert "PARTIAL_EVIDENCE" in values
        assert "ABSTAINED" in values
        assert "NEEDS_HUMAN_REVIEW" in values
        assert "FAILED" in values

    def test_research_depth_values(self) -> None:
        assert ResearchDepth.QUICK.value == "quick"
        assert ResearchDepth.STANDARD.value == "standard"
        assert ResearchDepth.DEEP.value == "deep"

    def test_outbox_status_values(self) -> None:
        values = {s.value for s in OutboxStatus}
        assert "pending" in values
        assert "published" in values
        assert "failed" in values

    def test_source_failure_codes_are_uppercase(self) -> None:
        for code in SourceFailureCode:
            assert code.value == code.value.upper()

    def test_research_error_codes_are_uppercase(self) -> None:
        for code in ResearchErrorCode:
            assert code.value == code.value.upper()
