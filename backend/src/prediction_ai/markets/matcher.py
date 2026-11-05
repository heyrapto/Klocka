from difflib import SequenceMatcher
from typing import Literal

from prediction_ai.schemas.analysis import ScreenshotMarketCandidate
from prediction_ai.schemas.market import NormalizedMarket

MatchStatus = Literal[
    "VERIFIED_EXACT", "VERIFIED_WITH_NONCRITICAL_DIFFERENCES", "NEEDS_CONFIRMATION", "UNVERIFIED"
]


def compute_string_similarity(a: str, b: str) -> float:
    """Compute normalized character sequence similarity [0, 1]."""
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def match_candidate_to_platform_market(
    candidate: ScreenshotMarketCandidate,
    platform_market: NormalizedMarket,
) -> tuple[MatchStatus, float, list[str]]:
    """Match extracted screenshot candidate against official platform market metadata."""
    discrepancies: list[str] = []

    if not candidate.market_title:
        return "UNVERIFIED", 0.0, ["Missing market title in candidate extraction."]

    title_similarity = compute_string_similarity(candidate.market_title, platform_market.title)

    # Asset or entity match
    asset_score = 1.0
    if candidate.asset_or_entity and platform_market.underlying_symbol:
        if candidate.asset_or_entity.upper() not in platform_market.underlying_symbol.upper():
            asset_score = 0.0
            discrepancies.append(
                f"Asset mismatch: {candidate.asset_or_entity} vs {platform_market.underlying_symbol}"
            )

    # Expiry verification
    expiry_score = 1.0
    if candidate.displayed_expiry and platform_market.resolves_at:
        # Check if date matches within same UTC calendar day
        if candidate.displayed_expiry.date() != platform_market.resolves_at.date():
            expiry_score = 0.0
            discrepancies.append("Expiration date mismatch.")

    # Weighted match formula (from technical plan Section 7.4)
    total_score = 0.40 * title_similarity + 0.30 * asset_score + 0.30 * expiry_score

    # Veto rule: Critical asset or expiry conflict vetos match
    if asset_score == 0.0 or expiry_score == 0.0:
        return "NEEDS_CONFIRMATION", total_score, discrepancies

    if total_score >= 0.90:
        return "VERIFIED_EXACT", total_score, []
    elif total_score >= 0.75:
        return "VERIFIED_WITH_NONCRITICAL_DIFFERENCES", total_score, discrepancies
    elif total_score >= 0.50:
        return "NEEDS_CONFIRMATION", total_score, discrepancies

    return "UNVERIFIED", total_score, ["Match score below minimum threshold."]
