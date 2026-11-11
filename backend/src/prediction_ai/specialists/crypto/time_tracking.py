"""Real-time temporal engine for interval markets.

Synchronizes countdown timestamps from screenshot capture through backend pipeline
latency to resolution time, eliminating temporal drift and feeding accurate T
into the log-normal digital option pricing formula.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class MarketTimingContext:
    """Exact point-in-time temporal snapshot for an interval contract."""

    initial_seconds_remaining: float
    exact_resolves_at: datetime
    decision_seconds_remaining: float
    pipeline_latency_seconds: float
    interval_phase: str  # "CAPITAL_ACCUMULATION", "MOMENTUM_FORMATION", "LATE_DECAY", "PIN_RISK", "EXPIRED"

    def to_dict(self) -> dict[str, Any]:
        return {
            "initial_seconds_remaining": round(self.initial_seconds_remaining, 1),
            "exact_resolves_at": self.exact_resolves_at.isoformat(),
            "decision_seconds_remaining": round(self.decision_seconds_remaining, 1),
            "pipeline_latency_seconds": round(self.pipeline_latency_seconds, 2),
            "interval_phase": self.interval_phase,
        }


def parse_countdown_to_seconds(text: str | None) -> float | None:
    """
    Parse a human-readable or digital countdown string into total seconds.

    Supported formats:
      - "07:43" or "7:43" (MM:SS) -> 463.0
      - "01:15:20" (HH:MM:SS) -> 4520.0
      - "7m 43s" or "7m43s" -> 463.0
      - "45s" or "45 sec" -> 45.0
      - "12m" or "12 min" -> 720.0
      - "2h" or "2 hours" -> 7200.0
      - "Ends in 05:12" or "Closes in 3m 15s" -> cleaned & parsed
    """
    if not text or not isinstance(text, str):
        return None

    cleaned = text.strip().lower()
    # Strip common prefix noise
    cleaned = re.sub(r"^(?:ends?\s+in|closes?\s+in|time\s+left|round\s+ends?|countdown:?)\s*", "", cleaned)

    # 1. Digital clock format: HH:MM:SS or MM:SS
    clock_match = re.search(r"\b(?:(\d{1,2}):)?(\d{1,2}):(\d{2})\b", cleaned)
    if clock_match:
        hours_str, mins_str, secs_str = clock_match.groups()
        hours = int(hours_str) if hours_str else 0
        mins = int(mins_str)
        secs = int(secs_str)
        return float(hours * 3600 + mins * 60 + secs)

    # 2. Textual components: e.g. "1h 30m 15s", "7m 43s", "45s"
    total_seconds = 0.0
    found_any = False

    hours_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:h|hr|hours?)\b", cleaned)
    if hours_match:
        total_seconds += float(hours_match.group(1)) * 3600
        found_any = True

    mins_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:m|min|mins|minutes?)\b", cleaned)
    if mins_match:
        total_seconds += float(mins_match.group(1)) * 60
        found_any = True

    secs_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:s|sec|secs|seconds?)\b", cleaned)
    if secs_match:
        total_seconds += float(secs_match.group(1))
        found_any = True

    if found_any:
        return total_seconds

    # 3. Bare single number fallback: e.g. "45" in context of seconds
    bare_match = re.search(r"^(\d{1,4})$", cleaned)
    if bare_match:
        val = float(bare_match.group(1))
        # If <= 3600 treat as seconds
        if val <= 3600:
            return val

    return None


def classify_interval_phase(seconds_remaining: float, total_duration: float = 900.0) -> str:
    """Classify the quantitative trading regime based on seconds to resolution."""
    if seconds_remaining <= 0:
        return "EXPIRED"
    if seconds_remaining < 120.0:  # < 2 min
        return "PIN_RISK"
    if seconds_remaining < 300.0:  # 2 - 5 min
        return "LATE_DECAY"
    if seconds_remaining < 600.0:  # 5 - 10 min
        return "MOMENTUM_FORMATION"
    return "CAPITAL_ACCUMULATION"


def align_interval_boundary(dt: datetime, tolerance_seconds: float = 90.0) -> datetime:
    """Snap an interval resolution timestamp to the exact 5m/15m candle boundary.

    Interval crypto markets (15m, 5m, 1h) resolve on clean clock boundaries
    (:00, :05, :10, :15, :20, :25, :30, :35, :40, :45, :50, :55). Screenshot
    upload delay or platform oracle latency offsets (e.g. Bayse's +60s resolutionDate
    or 14:59.999 closes_at) can place the raw timestamp 30-90 seconds away from
    the true candle close.
    """
    if dt.microsecond >= 500_000:
        dt = (dt + timedelta(seconds=1)).replace(microsecond=0)
    else:
        dt = dt.replace(microsecond=0)

    seconds_in_hour = dt.minute * 60 + dt.second

    # Priority 1: Check 15-minute boundaries (:00, :15, :30, :45)
    for target in (0, 900, 1800, 2700, 3600):
        if abs(seconds_in_hour - target) <= tolerance_seconds:
            base_hour = dt.replace(minute=0, second=0)
            return base_hour + timedelta(seconds=target)

    # Priority 2: Check 5-minute boundaries (every 300s)
    for target in range(0, 3900, 300):
        if abs(seconds_in_hour - target) <= min(tolerance_seconds, 60.0):
            base_hour = dt.replace(minute=0, second=0)
            return base_hour + timedelta(seconds=target)

    return dt


def resolve_market_timing(
    workflow_started_at: datetime,
    countdown_text: str | None = None,
    displayed_expiry: datetime | None = None,
    market_closes_at: datetime | None = None,
    current_time: datetime | None = None,
) -> MarketTimingContext:
    """
    Establish an immutable resolution anchor and compute point-in-time seconds.

    Args:
        workflow_started_at: When the screenshot was uploaded / workflow began (UTC).
        countdown_text: Visible countdown string (e.g. "07:43").
        displayed_expiry: OCR parsed expiry datetime.
        market_closes_at: Expiry timestamp from verified platform API.
        current_time: Moment when decision/feature computation occurs.
    """
    now = current_time or datetime.now(UTC)
    pipeline_latency = max(0.0, (now - workflow_started_at).total_seconds())

    parsed_countdown = parse_countdown_to_seconds(countdown_text)

    # Priority 1: If countdown was explicitly visible in screenshot, anchor to workflow_started_at
    if parsed_countdown is not None and parsed_countdown > 0:
        raw_resolves_at = workflow_started_at + timedelta(seconds=parsed_countdown)
        exact_resolves_at = align_interval_boundary(raw_resolves_at, tolerance_seconds=90.0)
        initial_seconds = max(0.0, (exact_resolves_at - workflow_started_at).total_seconds())
    # Priority 2: Verified platform API closes_at
    elif market_closes_at is not None:
        exact_resolves_at = align_interval_boundary(market_closes_at, tolerance_seconds=90.0)
        initial_seconds = max(0.0, (exact_resolves_at - workflow_started_at).total_seconds())
    # Priority 3: Displayed OCR expiry date
    elif displayed_expiry is not None:
        exact_resolves_at = align_interval_boundary(displayed_expiry, tolerance_seconds=90.0)
        initial_seconds = max(0.0, (exact_resolves_at - workflow_started_at).total_seconds())
    # Default fallback: 15-minute interval from start time
    else:
        initial_seconds = 900.0
        exact_resolves_at = align_interval_boundary(workflow_started_at + timedelta(seconds=900.0), tolerance_seconds=90.0)

    # Compute remaining time at this exact decision moment
    remaining = max(0.0, (exact_resolves_at - now).total_seconds())
    phase = classify_interval_phase(remaining, total_duration=initial_seconds)

    logger.debug(
        "Resolved market timing context",
        initial_seconds=initial_seconds,
        pipeline_latency=pipeline_latency,
        remaining_at_decision=remaining,
        phase=phase,
        resolves_at=exact_resolves_at.isoformat(),
    )

    return MarketTimingContext(
        initial_seconds_remaining=initial_seconds,
        exact_resolves_at=exact_resolves_at,
        decision_seconds_remaining=remaining,
        pipeline_latency_seconds=pipeline_latency,
        interval_phase=phase,
    )
