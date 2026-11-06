"""Deterministic coverage gate for evidence sufficiency (§8).

Walks the ResearchPlan.required_facts checklist against extracted Claims,
applies freshness windows, authority requirements, and source class checks.
Never fabricates a confidence percentage. Returns explicit ReadinessStatus
with per-fact diagnostics.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from prediction_ai.research.enums import (
    ClaimCategory,
    EligibilityState,
    EpistemicState,
    FactStatus,
    MarketType,
    ReadinessStatus,
    RelationType,
    SourceType,
)
from prediction_ai.research.models import (
    Claim,
    ClaimRelation,
    CoverageFactResult,
    RequiredFact,
    ResearchPlan,
    SourceRegistryEntry,
)
from prediction_ai.research.time import is_stale, version_is_eligible


@dataclass
class CoverageReport:
    """Result of a deterministic coverage gate evaluation."""

    readiness: ReadinessStatus
    fact_results: list[CoverageFactResult] = field(default_factory=list)
    abstention_reasons: list[str] = field(default_factory=list)
    mandatory_facts_total: int = 0
    mandatory_facts_satisfied: int = 0
    optional_facts_satisfied: int = 0
    unresolved_contradictions: int = 0
    eligible_claim_count: int = 0
    newest_eligible_at: datetime | None = None
    independent_source_group_count: int = 0


def _filter_eligible_claims(
    claims: list[Claim],
    cutoff_at: datetime,
) -> list[Claim]:
    """Return claims that are marked ELIGIBLE_AT_CUTOFF and available before cutoff."""
    return [
        c
        for c in claims
        if c.eligibility_state == EligibilityState.ELIGIBLE_AT_CUTOFF
        and c.available_at is not None
        and c.available_at <= cutoff_at
    ]


def _count_unresolved_contradictions(claim_relations: list[ClaimRelation]) -> int:
    """Count contradiction relations that have not been resolved."""
    return sum(
        1
        for rel in claim_relations
        if rel.relation_type == RelationType.CONTRADICTS
        and rel.resolution_status == "unresolved"
    )


def _has_contradiction_on_category(
    claim: Claim,
    claim_relations: list[ClaimRelation],
    eligible_claim_ids: set,
) -> bool:
    """Return True if any unresolved contradiction touches this claim's category."""
    for rel in claim_relations:
        if rel.relation_type != RelationType.CONTRADICTS:
            continue
        if rel.resolution_status != "unresolved":
            continue
        # Only consider contradictions between eligible claims
        if rel.from_claim_id not in eligible_claim_ids or rel.to_claim_id not in eligible_claim_ids:
            continue
        if rel.from_claim_id == claim.claim_id or rel.to_claim_id == claim.claim_id:
            return True
    return False


def _evaluate_fact(
    fact: RequiredFact,
    eligible_claims: list[Claim],
    claim_relations: list[ClaimRelation],
    source_registry: dict[str, SourceRegistryEntry],
    cutoff_at: datetime,
    now: datetime,
    eligible_claim_ids: set,
) -> CoverageFactResult:
    """Evaluate a single RequiredFact against eligible claims and return a CoverageFactResult."""

    # Filter claims by category (empty list means accept all)
    if fact.claim_categories:
        matching_claims = [
            c for c in eligible_claims if c.claim_category in fact.claim_categories
        ]
    else:
        matching_claims = list(eligible_claims)

    if not matching_claims:
        return CoverageFactResult(
            fact_key=fact.fact_key,
            status=FactStatus.MISSING,
            detail=f"No eligible claims found for fact '{fact.fact_key}'. "
                   f"Required categories: {[c.value for c in fact.claim_categories] or 'any'}.",
            claim_ids=[],
        )

    # Check authority requirement: source must have authority_tier <= 2
    if fact.authoritative_required:
        authoritative_claims = []
        for claim in matching_claims:
            entry = source_registry.get(claim.source_id)
            if entry is not None and entry.authority_tier <= 2:
                authoritative_claims.append(claim)
        if not authoritative_claims:
            return CoverageFactResult(
                fact_key=fact.fact_key,
                status=FactStatus.MISSING,
                detail=(
                    f"Fact '{fact.fact_key}' requires an authoritative source "
                    f"(authority_tier <= 2) but none of the {len(matching_claims)} "
                    "matching claim(s) have an authoritative source registered."
                ),
                claim_ids=[c.claim_id for c in matching_claims],
            )
        matching_claims = authoritative_claims

    # Check freshness: claim's available_at must be within the freshness window
    if fact.freshness_seconds:
        fresh_claims = [
            c
            for c in matching_claims
            if not is_stale(c.available_at, now, fact.freshness_seconds)
        ]
        if not fresh_claims:
            return CoverageFactResult(
                fact_key=fact.fact_key,
                status=FactStatus.STALE,
                detail=(
                    f"Fact '{fact.fact_key}': {len(matching_claims)} matching claim(s) found "
                    f"but all are stale (freshness window: {fact.freshness_seconds}s)."
                ),
                claim_ids=[c.claim_id for c in matching_claims],
            )
        matching_claims = fresh_claims

    # Check for unresolved contradictions on this fact's category
    contradiction_found = any(
        _has_contradiction_on_category(c, claim_relations, eligible_claim_ids)
        for c in matching_claims
    )
    if contradiction_found:
        return CoverageFactResult(
            fact_key=fact.fact_key,
            status=FactStatus.CONFLICTED,
            detail=(
                f"Fact '{fact.fact_key}' has eligible claims but unresolved contradiction(s) "
                "exist among them. Human review required before this fact can be satisfied."
            ),
            claim_ids=[c.claim_id for c in matching_claims],
        )

    # All checks passed — fact is satisfied
    return CoverageFactResult(
        fact_key=fact.fact_key,
        status=FactStatus.SATISFIED,
        detail=(
            f"Fact '{fact.fact_key}' satisfied by {len(matching_claims)} eligible, "
            f"fresh, non-contradicted claim(s)."
        ),
        claim_ids=[c.claim_id for c in matching_claims],
    )


def _should_abstain(
    fact_results: list[CoverageFactResult],
    unresolved_contradictions: int,
    plan: ResearchPlan,
    budget_exhausted: bool = False,
) -> tuple[bool, list[str]]:
    """Check mandatory abstention conditions (§8.2).

    Returns (should_abstain, list_of_reasons).
    """
    reasons: list[str] = []

    # Market type UNKNOWN → always abstain
    if plan.market_type == MarketType.UNKNOWN:
        reasons.append(
            "Market type is UNKNOWN; cannot safely research or forecast an unidentified proposition."
        )

    # Any mandatory fact is MISSING
    missing_mandatory = [
        r
        for r in fact_results
        if r.status == FactStatus.MISSING
    ]
    if missing_mandatory:
        keys = [r.fact_key for r in missing_mandatory]
        reasons.append(
            f"Mandatory fact(s) are missing and cannot be satisfied: {keys}."
        )

    # Any mandatory fact's source has unresolved contradiction
    contradicted_mandatory = [
        r
        for r in fact_results
        if r.status == FactStatus.CONFLICTED
    ]
    if contradicted_mandatory:
        keys = [r.fact_key for r in contradicted_mandatory]
        reasons.append(
            f"Mandatory fact(s) have unresolved source contradictions: {keys}."
        )

    # Budget exhausted before required coverage reached
    if budget_exhausted:
        reasons.append(
            "Research budget was exhausted before required fact coverage was achieved."
        )

    # Stale mandatory facts
    stale_mandatory = [
        r
        for r in fact_results
        if r.status == FactStatus.STALE
    ]
    if stale_mandatory:
        keys = [r.fact_key for r in stale_mandatory]
        reasons.append(
            f"Mandatory fact(s) have only stale evidence: {keys}. Evidence too old for this claim type."
        )

    should_abstain = len(reasons) > 0
    return should_abstain, reasons


def _newest_eligible_at(eligible_claims: list[Claim]) -> datetime | None:
    """Return the most recent available_at among eligible claims, or None."""
    timestamps = [c.available_at for c in eligible_claims if c.available_at is not None]
    return max(timestamps) if timestamps else None


def _independent_source_group_count(eligible_claims: list[Claim]) -> int:
    """Count distinct independence_group_ids across eligible claims."""
    return len({c.independence_group_id for c in eligible_claims})


def evaluate_coverage(
    plan: ResearchPlan,
    claims: list[Claim],
    claim_relations: list[ClaimRelation],
    source_registry: dict[str, SourceRegistryEntry],
    cutoff_at: datetime,
    now: datetime,
    budget_exhausted: bool = False,
) -> CoverageReport:
    """Deterministically evaluate whether the evidence pack is sufficient to forecast.

    Parameters
    ----------
    plan:
        The ResearchPlan with the required_facts checklist and market type.
    claims:
        All extracted claims for this job.
    claim_relations:
        All ClaimRelation records (used for contradiction checking).
    source_registry:
        Map of source_id → SourceRegistryEntry for authority-tier lookups.
    cutoff_at:
        The immutable forecast cutoff datetime.
    now:
        Current time used for freshness window evaluation.
    budget_exhausted:
        True if the research budget ran out before coverage was complete.

    Returns
    -------
    CoverageReport
        A fully populated, deterministic report with per-fact diagnostics.
    """
    # Step 1: Filter eligible claims
    eligible_claims = _filter_eligible_claims(claims, cutoff_at)
    eligible_claim_ids: set = {c.claim_id for c in eligible_claims}

    # Step 2: Count unresolved contradictions
    unresolved_contradictions = _count_unresolved_contradictions(claim_relations)

    # Step 3: Evaluate each required fact
    fact_results: list[CoverageFactResult] = []
    for required_fact in plan.required_facts:
        result = _evaluate_fact(
            fact=required_fact,
            eligible_claims=eligible_claims,
            claim_relations=claim_relations,
            source_registry=source_registry,
            cutoff_at=cutoff_at,
            now=now,
            eligible_claim_ids=eligible_claim_ids,
        )
        fact_results.append(result)

    # Tally mandatory vs optional
    mandatory_facts_total = sum(1 for f in plan.required_facts if f.required)
    mandatory_fact_keys = {f.fact_key for f in plan.required_facts if f.required}
    optional_fact_keys = {f.fact_key for f in plan.required_facts if not f.required}

    mandatory_results = [r for r in fact_results if r.fact_key in mandatory_fact_keys]
    optional_results = [r for r in fact_results if r.fact_key in optional_fact_keys]

    mandatory_facts_satisfied = sum(
        1 for r in mandatory_results if r.status == FactStatus.SATISFIED
    )
    optional_facts_satisfied = sum(
        1 for r in optional_results if r.status == FactStatus.SATISFIED
    )

    # Step 4: Determine ReadinessStatus
    should_abstain, abstention_reasons = _should_abstain(
        mandatory_results, unresolved_contradictions, plan, budget_exhausted
    )

    any_mandatory_missing = any(
        r.status == FactStatus.MISSING for r in mandatory_results
    )
    any_mandatory_stale = any(
        r.status == FactStatus.STALE for r in mandatory_results
    )
    any_mandatory_conflicted = any(
        r.status == FactStatus.CONFLICTED for r in mandatory_results
    )

    if any_mandatory_conflicted and not (any_mandatory_missing or any_mandatory_stale):
        # Contradictions on mandatory facts → human review
        readiness = ReadinessStatus.NEEDS_HUMAN_REVIEW
    elif should_abstain:
        # Missing or stale mandatory facts, budget exhausted, unknown market type
        if any_mandatory_missing or any_mandatory_stale or budget_exhausted:
            # If there are some eligible claims we have partial evidence
            if eligible_claims:
                readiness = ReadinessStatus.PARTIAL_EVIDENCE
            else:
                readiness = ReadinessStatus.ABSTAINED
        else:
            readiness = ReadinessStatus.ABSTAINED
    elif mandatory_facts_satisfied == mandatory_facts_total and not plan.required_facts:
        # No required facts defined — research is complete but we can't confirm forecast-ready
        readiness = ReadinessStatus.RESEARCH_COMPLETE
    elif mandatory_facts_satisfied == mandatory_facts_total:
        # All mandatory facts satisfied — ready for forecast if the market type has a model.
        # Market type UNKNOWN is already handled in _should_abstain; here we handle
        # the "no validated model" case per §14.3.
        if plan.market_type == MarketType.UNKNOWN:
            readiness = ReadinessStatus.ABSTAINED
        else:
            readiness = ReadinessStatus.READY_FOR_FORECAST
    else:
        # Some mandatory facts unsatisfied but no explicit abstention trigger yet
        readiness = ReadinessStatus.PARTIAL_EVIDENCE

    return CoverageReport(
        readiness=readiness,
        fact_results=fact_results,
        abstention_reasons=abstention_reasons,
        mandatory_facts_total=mandatory_facts_total,
        mandatory_facts_satisfied=mandatory_facts_satisfied,
        optional_facts_satisfied=optional_facts_satisfied,
        unresolved_contradictions=unresolved_contradictions,
        eligible_claim_count=len(eligible_claims),
        newest_eligible_at=_newest_eligible_at(eligible_claims),
        independent_source_group_count=_independent_source_group_count(eligible_claims),
    )
