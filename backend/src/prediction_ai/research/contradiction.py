"""Contradiction detection across extracted claims (§7.5).

Finds pairs of claims that assert opposite stances on the same proposition
for the same subject entities. Classifies contradiction kind and creates
ClaimRelation records. Never averages away contradictions.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from prediction_ai.research.enums import (
    ClaimCategory,
    ClaimModality,
    ClaimStance,
    ContradictionKind,
    EligibilityState,
    RelationType,
)
from prediction_ai.research.models import Claim, ClaimRelation

# Stances that form an opposing pair
_OPPOSING_STANCES: frozenset[frozenset[ClaimStance]] = frozenset(
    {
        frozenset({ClaimStance.SUPPORTS_YES, ClaimStance.SUPPORTS_NO}),
    }
)

# 24 hours threshold for temporal classification
_TEMPORAL_THRESHOLD = timedelta(hours=24)

# Categories that trigger DEFINITIONAL classification when stances differ on scope
_DEFINITIONAL_CATEGORIES: frozenset[ClaimCategory] = frozenset(
    {
        ClaimCategory.FIXTURE_STATUS,
        ClaimCategory.MATCH_RESULT,
    }
)

# Modality pairs that signal wording vs factual contradiction
_SPECULATIVE_MODALITIES: frozenset[ClaimModality] = frozenset(
    {
        ClaimModality.SPECULATED,
        ClaimModality.CONDITIONAL,
        ClaimModality.QUOTED_FROM_OTHER_SOURCE,
    }
)
_OFFICIAL_MODALITIES: frozenset[ClaimModality] = frozenset(
    {
        ClaimModality.OFFICIAL_STATEMENT,
        ClaimModality.ASSERTED,
    }
)


def _are_opposite_stances(a: Claim, b: Claim) -> bool:
    """Return True if the two claims hold opposing stances on the proposition."""
    pair = frozenset({a.stance_on_proposition, b.stance_on_proposition})
    return pair in _OPPOSING_STANCES


def _share_entity(a: Claim, b: Claim) -> bool:
    """Return True if the two claims share at least one subject entity."""
    set_a = set(a.subject_entity_ids)
    set_b = set(b.subject_entity_ids)
    return bool(set_a & set_b)


def _same_category(a: Claim, b: Claim) -> bool:
    """Return True if both claims belong to the same claim category."""
    return a.claim_category == b.claim_category


def _classify_contradiction(a: Claim, b: Claim) -> ContradictionKind:
    """Classify the kind of contradiction between two opposing claims.

    Priority order:
    1. TEMPORAL  — one claim's available_at is > 24 h earlier than the other.
    2. WORDING   — one claim uses an official modality while the other is speculative,
                   suggesting a language/framing difference rather than a factual conflict.
    3. DEFINITIONAL — both claims share a scope-sensitive category (FIXTURE_STATUS,
                       MATCH_RESULT) where the conflict likely arises from different
                       interpretational scope.
    4. FACTUAL   — default for opposing factual claims.
    """
    # Temporal: availability difference > 24 h
    if a.available_at is not None and b.available_at is not None:
        diff = abs((a.available_at - b.available_at).total_seconds())
        if diff > _TEMPORAL_THRESHOLD.total_seconds():
            return ContradictionKind.TEMPORAL

    # Wording: one is speculative/quoted, the other is official/asserted
    a_official = a.claim_modality in _OFFICIAL_MODALITIES
    b_official = b.claim_modality in _OFFICIAL_MODALITIES
    a_speculative = a.claim_modality in _SPECULATIVE_MODALITIES
    b_speculative = b.claim_modality in _SPECULATIVE_MODALITIES

    if (a_official and b_speculative) or (a_speculative and b_official):
        return ContradictionKind.WORDING

    # Definitional: scope-sensitive category
    if a.claim_category in _DEFINITIONAL_CATEGORIES and _same_category(a, b):
        return ContradictionKind.DEFINITIONAL

    return ContradictionKind.FACTUAL


def detect_contradictions(claims: list[Claim], job_id: UUID) -> list[ClaimRelation]:
    """Detect opposing claim pairs and create ClaimRelation records.

    Parameters
    ----------
    claims:
        All extracted claims to compare pairwise.
    job_id:
        The research job ID, included in every ClaimRelation.

    Returns
    -------
    list[ClaimRelation]
        One ClaimRelation(type=CONTRADICTS) for each detected pair,
        ordered so ``from_claim_id < to_claim_id`` (lexicographic on UUID str)
        to avoid duplicates.
    """
    relations: list[ClaimRelation] = []

    # Build a deduplicated list; lexicographic ordering ensures we process each pair once
    eligible = [
        c
        for c in claims
        if c.eligibility_state != EligibilityState.POST_CUTOFF
    ]

    n = len(eligible)
    for i in range(n):
        for j in range(i + 1, n):
            a = eligible[i]
            b = eligible[j]

            if a.claim_id == b.claim_id:
                continue

            # Only flag genuine opposing stances
            if not _are_opposite_stances(a, b):
                continue

            # Must share at least one subject entity
            if not _share_entity(a, b):
                continue

            # Must share the same claim category
            if not _same_category(a, b):
                continue

            # Enforce consistent ordering: smaller UUID str is always from_claim_id
            if str(a.claim_id) > str(b.claim_id):
                a, b = b, a

            kind = _classify_contradiction(a, b)

            relation = ClaimRelation(
                relation_id=uuid4(),
                job_id=job_id,
                from_claim_id=a.claim_id,
                to_claim_id=b.claim_id,
                relation_type=RelationType.CONTRADICTS,
                contradiction_kind=kind,
                resolution_status="unresolved",
                detail={
                    "from_stance": a.stance_on_proposition,
                    "to_stance": b.stance_on_proposition,
                    "category": a.claim_category,
                    "contradiction_kind": kind,
                },
            )
            relations.append(relation)

    return relations


def group_by_independence(claims: list[Claim]) -> dict[str, list[Claim]]:
    """Group claims by independence_group_id.

    Claims with the same independence_group_id originate from the same reporting
    chain (e.g., syndicated wire stories). Claims whose independence_group_id is
    unique to that claim form their own group.

    Parameters
    ----------
    claims:
        Any list of Claim objects.

    Returns
    -------
    dict[str, list[Claim]]
        Keys are independence_group_id strings; values are lists of claims
        sharing that group.
    """
    groups: dict[str, list[Claim]] = {}
    for claim in claims:
        group_key = claim.independence_group_id
        groups.setdefault(group_key, []).append(claim)
    return groups
