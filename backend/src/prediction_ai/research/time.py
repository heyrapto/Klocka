"""Timestamp parsing and point-in-time eligibility.

The central rule is deliberately narrow: a content version is eligible for a
forecast only when the runtime can prove that this exact version was available
at or before the frozen cutoff. An original publication timestamp alone is not
proof of page content at an earlier time.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from email.utils import parsedate_to_datetime
from typing import Protocol

from prediction_ai.research.models import DocumentVersion


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    """Trusted UTC clock boundary; injectable for deterministic tests."""

    def now(self) -> datetime:
        return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class ParsedTimestamp:
    raw: str
    value: datetime | None
    precision: str
    had_timezone: bool


_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_GDELT_COMPACT = re.compile(r"^\d{14}$")
_EPOCH = re.compile(r"^\d{10}$")


def parse_timestamp(raw: str | None) -> ParsedTimestamp | None:
    """Parse a source timestamp without assuming the server timezone.

    Returns ``None`` for empty input. A date-only value is normalized to midnight
    UTC but marked ``precision="date"`` so callers can treat it as uncertain for
    minute-level claims. A value without a timezone is marked
    ``had_timezone=False`` and normalized to UTC for storage while retaining the
    uncertainty flag in the document version.
    """

    if raw is None:
        return None
    value = raw.strip()
    if not value:
        return None

    if _GDELT_COMPACT.match(value):
        try:
            return ParsedTimestamp(
                raw=raw,
                value=datetime.strptime(value, "%Y%m%d%H%M%S").replace(tzinfo=UTC),
                precision="datetime",
                had_timezone=True,
            )
        except ValueError:
            return ParsedTimestamp(raw=raw, value=None, precision="unknown", had_timezone=False)

    if _EPOCH.match(value):
        try:
            return ParsedTimestamp(
                raw=raw,
                value=datetime.fromtimestamp(int(value), tz=UTC),
                precision="datetime",
                had_timezone=True,
            )
        except (OverflowError, OSError, ValueError):
            return ParsedTimestamp(raw=raw, value=None, precision="unknown", had_timezone=False)

    if _DATE_ONLY.match(value):
        try:
            parsed_date = date.fromisoformat(value)
            return ParsedTimestamp(
                raw=raw,
                value=datetime.combine(parsed_date, time.min, tzinfo=UTC),
                precision="date",
                had_timezone=False,
            )
        except ValueError:
            return ParsedTimestamp(raw=raw, value=None, precision="unknown", had_timezone=False)

    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
        return ParsedTimestamp(
            raw=raw,
            value=parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC),
            precision="datetime",
            had_timezone=parsed.tzinfo is not None,
        )
    except ValueError:
        pass

    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return ParsedTimestamp(raw=raw, value=None, precision="unknown", had_timezone=False)

    if parsed is None:
        return ParsedTimestamp(raw=raw, value=None, precision="unknown", had_timezone=False)
    return ParsedTimestamp(
        raw=raw,
        value=parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC),
        precision="datetime",
        had_timezone=parsed.tzinfo is not None,
    )


def version_is_eligible(version: DocumentVersion, cutoff_at: datetime) -> bool:
    """Apply the v2 point-in-time eligibility rule.

    A version must have a trustworthy availability timestamp, must have been
    captured and available at or before the cutoff, must be structurally valid,
    and must not declare a publication time after the cutoff.
    """

    if not version.version_is_valid:
        return False
    if version.availability_source == "unknown":
        return False
    if version.available_at is None or version.captured_at is None:
        return False
    if version.available_at > cutoff_at or version.captured_at > cutoff_at:
        return False
    if version.published_at is not None and version.published_at > cutoff_at:
        return False
    return True


def is_stale(available_at: datetime | None, now: datetime, freshness_seconds: int) -> bool:
    if available_at is None:
        return True
    return (now - available_at).total_seconds() > freshness_seconds


def freeze_cutoff(clock: Clock) -> datetime:
    """Freeze one cutoff instant for the whole job. Retries never extend it."""

    cutoff = clock.now()
    if cutoff.tzinfo is None:
        raise ValueError("Clock must return a timezone-aware UTC timestamp.")
    return cutoff.astimezone(UTC)


def assert_cutoff_unchanged(original: datetime, candidate: datetime) -> None:
    if original != candidate:
        raise ValueError("The forecast cutoff is immutable for a research job.")
