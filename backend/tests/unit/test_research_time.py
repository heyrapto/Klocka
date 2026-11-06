"""Point-in-time leakage tests (§17.2 — release blockers) and timestamp parsing."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from prediction_ai.research.enums import EligibilityState
from prediction_ai.research.models import DocumentVersion
from prediction_ai.research.time import (
    ParsedTimestamp,
    assert_cutoff_unchanged,
    freeze_cutoff,
    is_stale,
    parse_timestamp,
    version_is_eligible,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_T = datetime(2026, 11, 5, 18, 0, 0, tzinfo=UTC)   # forecast cutoff
_BEFORE = _T - timedelta(hours=2)
_AFTER  = _T + timedelta(hours=2)

_DOC_ID = uuid4()


def _version(
    *,
    captured_at: datetime,
    available_at: datetime,
    published_at: datetime | None = None,
    source_updated_at: datetime | None = None,
    version_is_valid: bool = True,
    availability_source: str = "capture",
) -> DocumentVersion:
    return DocumentVersion(
        version_id=uuid4(),
        document_id=_DOC_ID,
        source_id="test-source",
        content_hash="sha256:" + "a" * 64,
        capture_method="approved_publisher_page",
        extraction_version="html-extractor-1.0",
        captured_at=captured_at,
        available_at=available_at,
        availability_source=availability_source,
        published_at=published_at,
        source_updated_at=source_updated_at,
        version_is_valid=version_is_valid,
        created_at=captured_at,
    )


# ===========================================================================
# §17.2 Point-in-time leakage test fixtures (RELEASE BLOCKERS)
# ===========================================================================

class TestPointInTimeLeakage:
    """Each scenario from §17.2 must pass. These are release blockers."""

    def test_fixture_1_published_before_cutoff_captured_after_must_be_excluded(self) -> None:
        """Article published before cutoff, first captured AFTER cutoff → MUST be excluded.

        published_at=08:00, cutoff=18:00, captured_at=20:00
        Even though the original publication precedes the cutoff, we first
        retrieved the page AFTER the cutoff — we cannot know what the page
        said at 10:30 based on what we read at 20:00.
        """
        v = _version(
            published_at=_BEFORE,
            captured_at=_AFTER,       # <- captured AFTER cutoff
            available_at=_AFTER,       # <- became available AFTER cutoff
        )
        assert version_is_eligible(v, cutoff_at=_T) is False

    def test_fixture_2_published_and_captured_before_cutoff_updated_after_must_use_old_version(self) -> None:
        """Article published before cutoff, captured before cutoff, then updated AFTER cutoff.

        Only the old captured version (captured_at < cutoff) is eligible.
        A new version captured after the cutoff must NOT be eligible for the original cutoff.
        """
        old_version = _version(
            published_at=_BEFORE,
            captured_at=_BEFORE,   # captured BEFORE cutoff
            available_at=_BEFORE,
            source_updated_at=_BEFORE,
        )
        new_version = _version(
            published_at=_BEFORE,
            captured_at=_AFTER,    # new capture AFTER cutoff
            available_at=_AFTER,
            source_updated_at=_AFTER,
        )
        assert version_is_eligible(old_version, cutoff_at=_T) is True
        assert version_is_eligible(new_version, cutoff_at=_T) is False

    def test_fixture_3_provider_backfill_after_cutoff_with_earlier_event_time_must_be_excluded(self) -> None:
        """Provider backfills a stat after cutoff with an earlier event timestamp.

        event_at may be before cutoff, but created_at / available_at is AFTER cutoff.
        The rule: available_at must be <= cutoff_at. The event timestamp alone
        does not prove the data was available at cutoff.
        """
        v = _version(
            captured_at=_AFTER,       # created/available AFTER cutoff
            available_at=_AFTER,
            published_at=_BEFORE,     # event time is before cutoff
        )
        assert version_is_eligible(v, cutoff_at=_T) is False

    def test_fixture_4_original_timestamp_predates_cutoff_update_after_no_old_version(self) -> None:
        """Original pub timestamp before cutoff, update timestamp after cutoff, no old content version.

        Without a captured version that predates the cutoff, we cannot use this content.
        The version must be excluded or marked timestamp-uncertain.
        """
        # Captured only once — after the cutoff; availability is post-cutoff
        v = _version(
            published_at=_BEFORE,
            source_updated_at=_AFTER,   # <- page was updated AFTER cutoff
            captured_at=_AFTER,          # <- only capture is AFTER cutoff
            available_at=_AFTER,
        )
        assert version_is_eligible(v, cutoff_at=_T) is False

    def test_fixture_5_data_received_after_cutoff_provider_event_before_no_snapshot(self) -> None:
        """Data received after cutoff with a provider event time before cutoff.

        Without a verifiable as-of snapshot that proves the data was available
        before the cutoff, the record must be excluded.
        availability_source='unknown' means no trustworthy availability timestamp.
        """
        v = _version(
            captured_at=_AFTER,
            available_at=_AFTER,
            published_at=_BEFORE,
            availability_source="unknown",  # <- no trustworthy availability proof
        )
        assert version_is_eligible(v, cutoff_at=_T) is False

    def test_fixture_6_retry_after_cutoff_does_not_move_cutoff(self) -> None:
        """Retries must NOT extend the forecast cutoff or add post-cutoff evidence."""
        original_cutoff = _T
        # Simulating: a retry happened at cutoff + 1 hour → still uses original cutoff
        post_retry_time = _T + timedelta(hours=1)

        # Version captured during the retry (after cutoff) must be excluded
        v_retry = _version(captured_at=post_retry_time, available_at=post_retry_time)
        assert version_is_eligible(v_retry, cutoff_at=original_cutoff) is False

        # The original cutoff must remain unchanged
        assert_cutoff_unchanged(original_cutoff, original_cutoff)  # no error
        with pytest.raises(ValueError, match="immutable"):
            assert_cutoff_unchanged(original_cutoff, post_retry_time)


# ===========================================================================
# Eligible version tests
# ===========================================================================

class TestVersionIsEligible:
    def test_valid_before_cutoff_is_eligible(self) -> None:
        v = _version(captured_at=_BEFORE, available_at=_BEFORE, published_at=_BEFORE)
        assert version_is_eligible(v, cutoff_at=_T) is True

    def test_at_cutoff_boundary_is_eligible(self) -> None:
        v = _version(captured_at=_T, available_at=_T)
        assert version_is_eligible(v, cutoff_at=_T) is True

    def test_one_second_after_cutoff_is_not_eligible(self) -> None:
        just_after = _T + timedelta(seconds=1)
        v = _version(captured_at=just_after, available_at=just_after)
        assert version_is_eligible(v, cutoff_at=_T) is False

    def test_invalid_version_is_not_eligible(self) -> None:
        v = _version(captured_at=_BEFORE, available_at=_BEFORE, version_is_valid=False)
        assert version_is_eligible(v, cutoff_at=_T) is False

    def test_unknown_availability_source_is_not_eligible(self) -> None:
        v = _version(
            captured_at=_BEFORE, available_at=_BEFORE, availability_source="unknown"
        )
        assert version_is_eligible(v, cutoff_at=_T) is False

    def test_missing_available_at_is_not_eligible(self) -> None:
        v = _version(captured_at=_BEFORE, available_at=_BEFORE)
        # Manually clear available_at to simulate missing value
        object.__setattr__(v, "available_at", None)
        assert version_is_eligible(v, cutoff_at=_T) is False


# ===========================================================================
# Timestamp parsing tests
# ===========================================================================

class TestParseTimestamp:
    def test_iso8601_utc(self) -> None:
        p = parse_timestamp("2026-11-05T18:00:00Z")
        assert p is not None
        assert p.value is not None
        assert p.had_timezone is True
        assert p.value.tzinfo is not None

    def test_iso8601_offset(self) -> None:
        p = parse_timestamp("2026-11-05T19:00:00+01:00")
        assert p is not None
        assert p.had_timezone is True
        assert p.value.hour == 18  # normalized to UTC

    def test_rfc2822(self) -> None:
        p = parse_timestamp("Thu, 05 Nov 2026 18:00:00 +0000")
        assert p is not None
        assert p.value is not None
        assert p.had_timezone is True

    def test_gdelt_compact_format(self) -> None:
        p = parse_timestamp("20261105180000")
        assert p is not None
        assert p.precision == "datetime"
        assert p.had_timezone is True

    def test_unix_epoch(self) -> None:
        p = parse_timestamp("1762358400")  # a valid epoch
        assert p is not None
        assert p.precision == "datetime"
        assert p.had_timezone is True

    def test_date_only_marked_as_uncertain(self) -> None:
        p = parse_timestamp("2026-11-05")
        assert p is not None
        assert p.precision == "date"
        assert p.had_timezone is False  # date-only has no timezone info

    def test_naive_datetime_marked_as_uncertain(self) -> None:
        p = parse_timestamp("2026-11-05T18:00:00")
        assert p is not None
        assert p.had_timezone is False

    def test_none_returns_none(self) -> None:
        assert parse_timestamp(None) is None

    def test_empty_string_returns_none(self) -> None:
        assert parse_timestamp("") is None

    def test_garbage_returns_unparsed(self) -> None:
        p = parse_timestamp("not-a-date")
        assert p is not None
        assert p.value is None


# ===========================================================================
# Staleness and cutoff utilities
# ===========================================================================

class TestIsStale:
    def test_fresh_data_not_stale(self) -> None:
        available = _T - timedelta(minutes=5)
        now = _T
        assert is_stale(available, now, freshness_seconds=3600) is False

    def test_stale_data_is_stale(self) -> None:
        available = _T - timedelta(hours=25)
        now = _T
        assert is_stale(available, now, freshness_seconds=3600) is True

    def test_none_available_at_is_stale(self) -> None:
        assert is_stale(None, _T, freshness_seconds=3600) is True


class TestFreezeCutoff:
    def test_freeze_returns_utc(self) -> None:
        from prediction_ai.research.time import SystemClock
        clock = SystemClock()
        cutoff = freeze_cutoff(clock)
        assert cutoff.tzinfo is not None

    def test_assert_cutoff_unchanged_raises_on_change(self) -> None:
        with pytest.raises(ValueError, match="immutable"):
            assert_cutoff_unchanged(_T, _T + timedelta(seconds=1))

    def test_assert_cutoff_unchanged_passes_on_equal(self) -> None:
        assert_cutoff_unchanged(_T, _T)  # must not raise
