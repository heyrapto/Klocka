"""Build a typed, auditable contract specification from platform metadata.

The platform APIs expose free-text resolution rules.  Free text is not safe to
feed directly into a probability model: the oracle, the settlement statistic,
the comparator, and the strike source all change the meaning of the contract.
This module converts the available metadata into a ``ContractSpec`` and records
every unresolved ambiguity explicitly.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from prediction_ai.schemas.analysis import ScreenshotMarketCandidate
from prediction_ai.schemas.contract import (
    Comparator,
    ContractAmbiguity,
    ContractDirection,
    ContractSpec,
    ResolutionSource,
    StrikeSource,
)
from prediction_ai.schemas.market import NormalizedMarket, NormalizedOutcome

_AFFIRMATIVE_LABELS = frozenset(
    {"yes", "up", "higher", "over", "above", "buy up", "buy yes"}
)
_NEGATIVE_LABELS = frozenset(
    {"no", "down", "lower", "under", "below", "buy down", "buy no"}
)

_PREFIX_SCALE = {"k": Decimal("1000"), "m": Decimal("1000000")}

_STRIKE_PATTERN = re.compile(
    r"(?:above|below|over|under|reach|hit|at|to)\s+\$?"
    r"([0-9][0-9,]*(?:\.[0-9]+)?)\s*([kKmM])?",
    re.IGNORECASE,
)
_BARE_STRIKE_PATTERN = re.compile(
    r"\$?([0-9]{4,7}(?:\.[0-9]+)?)\s*([kKmM])?\b",
    re.IGNORECASE,
)
_TWAP_PATTERN = re.compile(
    r"(\d+)\s*[- ]?\s*(second|seconds|sec|secs|s|minute|minutes|min|mins|m)\s+"
    r"(?:chainlink\s+)?twap",
    re.IGNORECASE,
)


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        parsed = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed > 0 else None


def _normalise_label(label: str) -> str:
    return re.sub(r"\s+", " ", label.strip().lower())


def _split_outcomes(
    outcomes: list[NormalizedOutcome],
) -> tuple[NormalizedOutcome | None, NormalizedOutcome | None, list[ContractAmbiguity]]:
    if len(outcomes) != 2:
        return None, None, [
            ContractAmbiguity(
                code="BINARY_OUTCOMES_REQUIRED",
                message=f"Expected exactly two outcomes, found {len(outcomes)}.",
                severity="critical",
            )
        ]

    labels = [_normalise_label(item.label) for item in outcomes]
    affirmative_index: int | None = None
    negative_index: int | None = None
    for index, label in enumerate(labels):
        if label in _AFFIRMATIVE_LABELS:
            affirmative_index = index
        elif label in _NEGATIVE_LABELS:
            negative_index = index

    ambiguities: list[ContractAmbiguity] = []
    if affirmative_index is None and negative_index is None:
        # Preserve platform ordering but make the assumption explicit.
        affirmative_index, negative_index = 0, 1
        ambiguities.append(
            ContractAmbiguity(
                code="OUTCOME_LABELS_UNRECOGNISED",
                message=(
                    "Neither outcome label matched YES/UP/HIGHER. Using platform "
                    "ordering for the affirmative side."
                ),
                severity="warning",
            )
        )
    elif affirmative_index is None:
        assert negative_index is not None
        affirmative_index = 1 - negative_index
        ambiguities.append(
            ContractAmbiguity(
                code="AFFIRMATIVE_LABEL_INFERRED",
                message="Affirmative outcome inferred as the opposite of the recognised negative label.",
                severity="warning",
            )
        )
    elif negative_index is None:
        negative_index = 1 - affirmative_index
        ambiguities.append(
            ContractAmbiguity(
                code="NEGATIVE_LABEL_INFERRED",
                message="Negative outcome inferred as the opposite of the recognised affirmative label.",
                severity="warning",
            )
        )

    if affirmative_index == negative_index:
        return None, None, [
            ContractAmbiguity(
                code="OUTCOME_LABEL_COLLISION",
                message="Affirmative and negative outcomes resolved to the same index.",
                severity="critical",
            )
        ]

    assert affirmative_index is not None and negative_index is not None
    return (
        outcomes[affirmative_index],
        outcomes[negative_index],
        ambiguities,
    )


def _parse_direction(title: str, labels: list[str]) -> ContractDirection:
    text = " ".join([title, *labels]).lower()
    if "up or down" in text:
        return "up"
    if any(label in ("up", "higher", "buy up") for label in labels):
        return "up"
    if any(label in ("down", "lower", "buy down") for label in labels):
        return "down"
    if any(token in text for token in (" below ", " less than ", " under ", " lower than ")):
        return "below"
    if any(
        token in text
        for token in (" above ", " greater than ", " over ", " higher than ", " reach ", " hit ")
    ):
        return "above"
    return "unknown"


def _parse_strike(
    title: str,
    candidate: ScreenshotMarketCandidate | None,
    market: NormalizedMarket,
    *,
    strike_override: Decimal | None = None,
) -> tuple[Decimal | None, StrikeSource]:
    if strike_override is not None and strike_override > 0:
        return strike_override, "platform_field"
    if candidate is not None and candidate.target_price is not None:
        return candidate.target_price, "fixed_price"

    for key in ("event_threshold", "market_threshold", "threshold", "strike"):
        parsed = _decimal(market.raw_metadata.get(key))
        if parsed is not None:
            return parsed, "platform_field"

    normalised_title = title.replace(",", "").replace("$", "")
    match = _STRIKE_PATTERN.search(normalised_title)
    if match is None:
        match = _BARE_STRIKE_PATTERN.search(normalised_title)
    if match is not None:
        value = _decimal(match.group(1))
        if value is not None:
            scale = _PREFIX_SCALE.get((match.group(2) or "").lower())
            return (value * scale if scale else value), "fixed_price"

    # Up/down interval markets are struck at the price observed at the start of
    # the interval.  The value may still be available from the screenshot; if
    # not, the caller must resolve it rather than fabricating a strike.
    if any(token in title.lower() for token in ("up or down", "higher or lower")):
        return None, "start_price"
    return None, "unknown"


def _parse_twap_window(text: str) -> int | None:
    match = _TWAP_PATTERN.search(text)
    if match is None:
        return None
    value = int(match.group(1))
    unit = match.group(2).lower()
    if unit.startswith("m"):
        value *= 60
    return value if value > 0 else None


def _parse_source(text: str, resolution_source: str | None) -> tuple[ResolutionSource, str | None]:
    lowered = f"{text} {resolution_source or ''}".lower()
    is_chainlink = "chainlink" in lowered or "chain.link" in lowered
    if is_chainlink and "twap" in lowered:
        return "chainlink_twap", resolution_source
    if is_chainlink:
        return "chainlink_data_stream", resolution_source
    if "uma" in lowered:
        return "uma", resolution_source
    if resolution_source and resolution_source.startswith(("http://", "https://")):
        return "platform_api", resolution_source
    if resolution_source:
        return "platform_api", resolution_source
    return "unknown", resolution_source


def _parse_comparator(
    text: str,
    candidate: ScreenshotMarketCandidate | None,
) -> Comparator:
    lowered = text.lower()
    if any(
        phrase in lowered
        for phrase in (
            "greater than or equal",
            "higher than or equal",
            "at or above",
            "at least",
            ">=",
        )
    ):
        return "gte"
    if any(
        phrase in lowered
        for phrase in (
            "less than or equal",
            "lower than or equal",
            "at or below",
            "at most",
            "<=",
        )
    ):
        return "lte"
    if any(phrase in lowered for phrase in ("greater than", "higher than", " above ", " over ")):
        return "gt"
    if any(phrase in lowered for phrase in ("less than", "lower than", " below ", " under ")):
        return "lt"

    if candidate is not None:
        comparison_map: dict[str, Comparator] = {
            ">": "gt",
            ">=": "gte",
            "<": "lt",
            "<=": "lte",
        }
        return comparison_map.get(candidate.comparison_operator, "unknown")
    return "unknown"


def _tie_rule(comparator: Comparator) -> str | None:
    if comparator == "gte":
        return "affirmative_on_tie"
    if comparator == "lte":
        return "negative_on_tie"
    return None


def _is_crypto_threshold(market: NormalizedMarket) -> bool:
    if market.category != "crypto":
        return False
    return market.market_type in {"binary_threshold", "crypto_threshold"} or bool(
        market.underlying_symbol
    )


def build_contract_spec(
    market: NormalizedMarket,
    candidate: ScreenshotMarketCandidate | None = None,
    *,
    settlement_timestamp: datetime | None = None,
    strike_override: Decimal | None = None,
) -> ContractSpec:
    """Build a typed spec and enumerate every unresolved contract ambiguity."""
    resolution_text = (
        market.resolution_criteria
        or (candidate.displayed_resolution_text if candidate is not None else None)
        or market.description
        or ""
    )
    resolution_source = market.resolution_source
    combined_text = " ".join(
        part
        for part in (
            market.title,
            market.description or "",
            resolution_text,
            resolution_source or "",
        )
        if part
    )

    affirmative_outcome, negative_outcome, ambiguities = _split_outcomes(market.outcomes)
    affirmative_id = affirmative_outcome.outcome_id if affirmative_outcome is not None else None
    negative_id = negative_outcome.outcome_id if negative_outcome is not None else None
    affirmative_token_id = (
        affirmative_outcome.token_id if affirmative_outcome is not None else None
    )
    negative_token_id = negative_outcome.token_id if negative_outcome is not None else None
    labels = [_normalise_label(item.label) for item in market.outcomes]
    direction = _parse_direction(market.title, labels)
    strike_value, strike_source = _parse_strike(
        market.title, candidate, market, strike_override=strike_override
    )
    settlement_source, settlement_source_url = _parse_source(combined_text, resolution_source)
    twap_window = _parse_twap_window(combined_text)
    comparator = _parse_comparator(combined_text, candidate)
    resolved_at = settlement_timestamp or market.resolves_at or market.closes_at

    if not market.platform_market_id.strip():
        ambiguities.append(
            ContractAmbiguity(
                code="MISSING_PLATFORM_MARKET_ID",
                message="Platform market ID is missing; the contract cannot be uniquely identified.",
                severity="critical",
            )
        )
    if any(not outcome.token_id.strip() for outcome in market.outcomes):
        ambiguities.append(
            ContractAmbiguity(
                code="MISSING_TOKEN_ID",
                message="One or more outcome token IDs are missing; executable quotes cannot be mapped safely.",
                severity="critical",
            )
        )
    if resolved_at is None:
        ambiguities.append(
            ContractAmbiguity(
                code="MISSING_SETTLEMENT_TIMESTAMP",
                message="No settlement timestamp is available from the platform or screenshot.",
                severity="critical",
            )
        )
    if not resolution_text.strip():
        ambiguities.append(
            ContractAmbiguity(
                code="RESOLUTION_TEXT_MISSING",
                message="The platform did not provide resolution rules; oracle and tie handling are unconfirmed.",
                severity="warning",
            )
        )

    crypto_threshold = _is_crypto_threshold(market)
    if crypto_threshold:
        if settlement_source == "unknown":
            ambiguities.append(
                ContractAmbiguity(
                    code="UNKNOWN_SETTLEMENT_SOURCE",
                    message="The crypto contract does not identify its settlement oracle.",
                    severity="critical",
                )
            )
        if comparator == "unknown":
            ambiguities.append(
                ContractAmbiguity(
                    code="UNKNOWN_COMPARATOR",
                    message="The settlement comparator (>=, >, <=, <) is unknown.",
                    severity="critical",
                )
            )
        if strike_value is None:
            ambiguities.append(
                ContractAmbiguity(
                    code="UNKNOWN_STRIKE",
                    message=(
                        "The price-to-beat/strike is unknown. For up/down markets it must "
                        "come from the platform or a verified screenshot field."
                    ),
                    severity="critical",
                )
            )
        if settlement_source == "chainlink_twap" and twap_window is None:
            ambiguities.append(
                ContractAmbiguity(
                    code="TWAP_WINDOW_UNKNOWN",
                    message="The contract resolves on a TWAP but the averaging window is not stated.",
                    severity="warning",
                )
            )

    critical = [item for item in ambiguities if item.severity == "critical"]
    tradeable = len(critical) == 0
    reason = "; ".join(item.message for item in critical) if critical else None

    provenance = {
        "market_id": market.platform_market_id,
        "condition_id": market.raw_metadata.get("condition_id"),
        "resolution_source_raw": resolution_source,
        "resolution_text_raw": resolution_text[:2000] if resolution_text else None,
        "candidate_extraction_source": candidate.extraction_source if candidate is not None else None,
        "candidate_confidence": candidate.overall_confidence if candidate is not None else None,
    }

    spec_payload = {
        "platform": market.platform,
        "platform_market_id": market.platform_market_id,
        "title": market.title,
        "asset": market.underlying_symbol,
        "direction": direction,
        "affirmative_outcome_id": affirmative_id,
        "negative_outcome_id": negative_id,
        "affirmative_token_id": affirmative_token_id,
        "negative_token_id": negative_token_id,
        "strike_value": str(strike_value) if strike_value is not None else None,
        "strike_source": strike_source,
        "settlement_source": settlement_source,
        "settlement_timestamp": resolved_at.isoformat() if resolved_at else None,
        "twap_window_seconds": twap_window,
        "comparator": comparator,
    }
    spec_hash = hashlib.sha256(
        json.dumps(spec_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    return ContractSpec(
        platform=market.platform,
        platform_market_id=market.platform_market_id,
        platform_event_id=market.platform_event_id,
        slug=market.slug,
        title=market.title,
        category=market.category,
        market_type=market.market_type,
        asset=market.underlying_symbol,
        direction=direction,
        outcomes=market.outcomes,
        affirmative_outcome_id=affirmative_id,
        negative_outcome_id=negative_id,
        affirmative_token_id=affirmative_token_id,
        negative_token_id=negative_token_id,
        strike_value=strike_value,
        strike_source=strike_source,
        settlement_source=settlement_source,
        settlement_source_url=settlement_source_url,
        settlement_timestamp=resolved_at,
        twap_window_seconds=twap_window,
        comparator=comparator,
        tie_rule=_tie_rule(comparator),
        resolution_text=resolution_text[:4000] if resolution_text else None,
        ambiguities=ambiguities,
        provenance=provenance,
        spec_hash=spec_hash,
        tradeable=tradeable,
        tradeability_reason=reason,
    )
