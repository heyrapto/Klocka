"""Deterministic research planning by proposition type.

The planner is template- and rule-driven. An optional LLM may suggest candidate
search phrases, but suggestions are rejected if they contain URLs, control
characters, or exceed the configured query budget. The LLM cannot expand source
permissions or select an arbitrary destination.
"""

from __future__ import annotations

import re

from prediction_ai.research.enums import (
    ClaimCategory,
    MarketType,
    ResearchDepth,
    SourceType,
)
from prediction_ai.research.models import (
    RequiredFact,
    ResearchPlan,
    ResearchTarget,
    SubjectEntity,
)
from prediction_ai.research.registry import SourceRegistry

_MAX_QUERY_LENGTH = 300
_MAX_QUERY_SUGGESTIONS = 10
_URL_LIKE = re.compile(r"https?://|www\.", re.IGNORECASE)
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def classify_market_type(target: ResearchTarget) -> MarketType:
    text = f"{target.market_type} {target.proposition}".lower()
    if any(word in text for word in ("appoint", "appointed", "named as", "hire", "successor")):
        return MarketType.APPOINTMENT_BY_DEADLINE
    if any(
        word in text
        for word in (
            "leave",
            "leaves",
            "depart",
            "departure",
            "sacked",
            "dismissed",
            "axed",
            "resign",
            "step down",
            "manager",
            "head coach",
            "part ways",
        )
    ):
        return MarketType.MANAGER_DEPARTURE_BY_DEADLINE
    if any(
        word in text
        for word in (
            "lineup",
            "line-up",
            "starting xi",
            "starting eleven",
            "appearance",
            "minutes",
            "starts",
            "play in",
        )
    ):
        return MarketType.PLAYER_PARTICIPATION
    if any(
        word in text
        for word in (
            "match",
            "fixture",
            "beat",
            "defeat",
            "win",
            "draw",
            "result",
            "score",
            "qualify",
            "advance",
        )
    ):
        return MarketType.MATCH_RESULT
    if any(
        word in text
        for word in (
            "policy",
            "executive order",
            "legislation",
            "regulation",
            "government",
            "parliament",
            "announce",
            "gazette",
        )
    ):
        return MarketType.POLICY_ANNOUNCEMENT
    return MarketType.UNKNOWN


def _entity_of_type(target: ResearchTarget, entity_type: str) -> list[SubjectEntity]:
    return [entity for entity in target.subject_entities if entity.entity_type == entity_type]


def _primary_names(target: ResearchTarget) -> tuple[str, str, str]:
    people = _entity_of_type(target, "person")
    organizations = _entity_of_type(target, "organization")
    competitions = _entity_of_type(target, "competition")
    person = people[0].name if people else ""
    organization = organizations[0].name if organizations else ""
    competition = competitions[0].name if competitions else ""
    return person, organization, competition


def _query_terms(target: ResearchTarget) -> list[str]:
    person, organization, competition = _primary_names(target)
    terms = [item for item in (person, organization, competition) if item]
    if not terms:
        terms = [
            token
            for token in re.findall(r"[A-Za-z][A-Za-z0-9'\-]{3,}", target.proposition)[:6]
        ]
    return terms


def _queries_for(target: ResearchTarget, market_type: MarketType) -> list[str]:
    person, organization, competition = _primary_names(target)
    subject = " ".join(item for item in (person, organization) if item) or target.proposition[:120]
    queries: list[str] = []
    if market_type is MarketType.MANAGER_DEPARTURE_BY_DEADLINE:
        queries = [
            f"{subject} official statement manager",
            f"{subject} departure sacked dismissed",
            f"{subject} contract future latest news",
            f"{organization or subject} manager replacement",
        ]
    elif market_type is MarketType.APPOINTMENT_BY_DEADLINE:
        queries = [
            f"{subject} appointment official announcement",
            f"{subject} named manager",
            f"{organization or subject} head coach appointment",
        ]
    elif market_type is MarketType.MATCH_RESULT:
        teams = " ".join(
            entity.name for entity in target.subject_entities if entity.entity_type == "organization"
        )
        queries = [
            f"{teams or subject} fixture kickoff",
            f"{teams or subject} official lineup team news",
            f"{teams or subject} injury suspension",
            f"{competition} {teams} match preview".strip(),
        ]
    elif market_type is MarketType.PLAYER_PARTICIPATION:
        queries = [
            f"{subject} official lineup",
            f"{subject} team news injury availability",
            f"{organization or subject} starting lineup",
        ]
    elif market_type is MarketType.POLICY_ANNOUNCEMENT:
        queries = [
            f"{subject} official announcement",
            f"{subject} government regulator statement",
            f"{subject} effective date legislation",
        ]
    else:
        queries = [f"{subject} latest official news"]

    seen: set[str] = set()
    unique: list[str] = []
    for query in queries:
        normalized = " ".join(query.split())[:_MAX_QUERY_LENGTH]
        if normalized and normalized.lower() not in seen:
            seen.add(normalized.lower())
            unique.append(normalized)
    return unique[:8]


def _manager_departure_facts() -> list[RequiredFact]:
    return [
        RequiredFact(
            fact_key="resolution_definition",
            question="Is the exact resolution rule captured verbatim from the platform?",
            required=True,
            completion_rule="Target has non-empty resolution rules and a matching rules hash.",
        ),
        RequiredFact(
            fact_key="deadline_semantics",
            question="Are the deadline, timezone, and inclusive/exclusive boundary understood?",
            required=True,
            completion_rule="Target has a timezone-aware deadline and a named timezone.",
        ),
        RequiredFact(
            fact_key="current_role_status",
            question="Is the person currently in the defined role?",
            preferred_source_types=[SourceType.OFFICIAL, SourceType.SPORTS_AUTHORITY],
            claim_categories=[
                ClaimCategory.CURRENT_ROLE_STATUS,
                ClaimCategory.OFFICIAL_STATEMENT,
            ],
            required=True,
            freshness_seconds=604_800,
            min_independent_groups=1,
            authoritative_required=True,
            completion_rule="At least one eligible authoritative claim about current role status.",
        ),
        RequiredFact(
            fact_key="departure_or_denial_evidence",
            question="Is there an official departure announcement or official denial?",
            preferred_source_types=[SourceType.OFFICIAL, SourceType.SPORTS_AUTHORITY],
            claim_categories=[
                ClaimCategory.MANAGER_DEPARTURE,
                ClaimCategory.OFFICIAL_STATEMENT,
            ],
            required=True,
            freshness_seconds=604_800,
            min_independent_groups=1,
            authoritative_required=True,
            completion_rule="An eligible authoritative departure/denial claim, not a rumor.",
        ),
        RequiredFact(
            fact_key="corroboration",
            question="Is the departure or denial corroborated by an independent chain?",
            preferred_source_types=[SourceType.PUBLISHER, SourceType.WIRE_SERVICE],
            claim_categories=[
                ClaimCategory.MANAGER_DEPARTURE,
                ClaimCategory.CURRENT_ROLE_STATUS,
            ],
            required=True,
            freshness_seconds=604_800,
            min_independent_groups=2,
            authoritative_required=False,
            authoritative_satisfies=True,
            completion_rule="Authoritative primary source, or at least two independent groups.",
        ),
        RequiredFact(
            fact_key="no_unresolved_contradictions",
            question="Are material contradictions resolved or absent?",
            required=True,
            completion_rule="Zero unresolved contradiction relations.",
        ),
        RequiredFact(
            fact_key="freshness",
            question="Is the newest eligible evidence current for this claim type?",
            required=True,
            freshness_seconds=604_800,
            completion_rule="Newest eligible evidence is within seven days of the cutoff.",
        ),
    ]


def _appointment_facts() -> list[RequiredFact]:
    return [
        RequiredFact(
            fact_key="resolution_definition",
            question="Is the exact appointment definition captured verbatim?",
            required=True,
            completion_rule="Target has non-empty resolution rules and a matching rules hash.",
        ),
        RequiredFact(
            fact_key="deadline_semantics",
            question="Are the deadline, timezone, and boundary understood?",
            required=True,
            completion_rule="Target has a timezone-aware deadline and a named timezone.",
        ),
        RequiredFact(
            fact_key="official_appointment",
            question="Has the appointment been officially announced?",
            preferred_source_types=[SourceType.OFFICIAL, SourceType.SPORTS_AUTHORITY],
            claim_categories=[
                ClaimCategory.APPOINTMENT,
                ClaimCategory.OFFICIAL_STATEMENT,
            ],
            required=True,
            freshness_seconds=604_800,
            authoritative_required=True,
            completion_rule="At least one eligible authoritative appointment claim.",
        ),
        RequiredFact(
            fact_key="no_unresolved_contradictions",
            question="Are material contradictions resolved or absent?",
            required=True,
            completion_rule="Zero unresolved contradiction relations.",
        ),
        RequiredFact(
            fact_key="freshness",
            question="Is the newest eligible evidence current?",
            required=True,
            freshness_seconds=604_800,
            completion_rule="Newest eligible evidence is within seven days of the cutoff.",
        ),
    ]


def _match_result_facts() -> list[RequiredFact]:
    return [
        RequiredFact(
            fact_key="resolution_definition",
            question="Does the rule define 90 minutes vs extra time, overtime, or abandonment?",
            required=True,
            completion_rule="Target rules define the exact match outcome semantics.",
        ),
        RequiredFact(
            fact_key="deadline_semantics",
            question="Are kickoff time, venue timezone, and cutoff understood?",
            required=True,
            completion_rule="Target has a timezone-aware deadline and a named timezone.",
        ),
        RequiredFact(
            fact_key="fixture_status",
            question="Is the fixture scheduled, delayed, live, completed, or abandoned?",
            preferred_source_types=[SourceType.SPORTS_AUTHORITY, SourceType.OFFICIAL],
            claim_categories=[ClaimCategory.FIXTURE_STATUS],
            required=True,
            freshness_seconds=3_600,
            authoritative_required=True,
            completion_rule="At least one eligible authoritative fixture-status claim.",
        ),
        RequiredFact(
            fact_key="official_lineup",
            question="Are official lineups published?",
            preferred_source_types=[SourceType.SPORTS_AUTHORITY, SourceType.OFFICIAL],
            claim_categories=[ClaimCategory.TEAM_LINEUP],
            required=True,
            freshness_seconds=3_600,
            authoritative_required=True,
            completion_rule="At least one eligible authoritative lineup claim.",
        ),
        RequiredFact(
            fact_key="availability",
            question="Are material injuries or suspensions known?",
            preferred_source_types=[SourceType.OFFICIAL, SourceType.SPORTS_AUTHORITY],
            claim_categories=[
                ClaimCategory.PLAYER_AVAILABILITY,
                ClaimCategory.INJURY_SUSPENSION,
            ],
            required=True,
            freshness_seconds=86_400,
            authoritative_required=True,
            completion_rule="At least one eligible authoritative availability claim.",
        ),
        RequiredFact(
            fact_key="no_unresolved_contradictions",
            question="Are material contradictions resolved or absent?",
            required=True,
            completion_rule="Zero unresolved contradiction relations.",
        ),
        RequiredFact(
            fact_key="freshness",
            question="Is the newest eligible evidence current near kickoff?",
            required=True,
            freshness_seconds=3_600,
            completion_rule="Newest eligible evidence is within one hour of the cutoff.",
        ),
    ]


def _player_participation_facts() -> list[RequiredFact]:
    return [
        RequiredFact(
            fact_key="resolution_definition",
            question="Does the rule define what counts as an appearance or start?",
            required=True,
            completion_rule="Target rules define participation semantics exactly.",
        ),
        RequiredFact(
            fact_key="deadline_semantics",
            question="Are kickoff time and cutoff understood?",
            required=True,
            completion_rule="Target has a timezone-aware deadline and a named timezone.",
        ),
        RequiredFact(
            fact_key="official_lineup",
            question="Is the official lineup published?",
            preferred_source_types=[SourceType.SPORTS_AUTHORITY, SourceType.OFFICIAL],
            claim_categories=[ClaimCategory.TEAM_LINEUP],
            required=True,
            freshness_seconds=3_600,
            authoritative_required=True,
            completion_rule="At least one eligible authoritative lineup claim.",
        ),
        RequiredFact(
            fact_key="player_availability",
            question="Is the player fit and available?",
            preferred_source_types=[SourceType.OFFICIAL, SourceType.SPORTS_AUTHORITY],
            claim_categories=[
                ClaimCategory.PLAYER_AVAILABILITY,
                ClaimCategory.INJURY_SUSPENSION,
            ],
            required=True,
            freshness_seconds=86_400,
            authoritative_required=True,
            completion_rule="At least one eligible authoritative availability claim.",
        ),
        RequiredFact(
            fact_key="no_unresolved_contradictions",
            question="Are material contradictions resolved or absent?",
            required=True,
            completion_rule="Zero unresolved contradiction relations.",
        ),
        RequiredFact(
            fact_key="freshness",
            question="Is the newest eligible evidence current near kickoff?",
            required=True,
            freshness_seconds=3_600,
            completion_rule="Newest eligible evidence is within one hour of the cutoff.",
        ),
    ]


def _policy_facts() -> list[RequiredFact]:
    return [
        RequiredFact(
            fact_key="resolution_definition",
            question="Is the exact policy action and resolution source captured?",
            required=True,
            completion_rule="Target has non-empty resolution rules and a matching rules hash.",
        ),
        RequiredFact(
            fact_key="deadline_semantics",
            question="Are the deadline, timezone, and effective-date semantics understood?",
            required=True,
            completion_rule="Target has a timezone-aware deadline and a named timezone.",
        ),
        RequiredFact(
            fact_key="official_policy_action",
            question="Has a government, regulator, or official body taken the defined action?",
            preferred_source_types=[
                SourceType.GOVERNMENT,
                SourceType.REGULATOR,
                SourceType.OFFICIAL,
            ],
            claim_categories=[
                ClaimCategory.POLICY_ANNOUNCEMENT,
                ClaimCategory.OFFICIAL_STATEMENT,
            ],
            required=True,
            freshness_seconds=86_400,
            authoritative_required=True,
            completion_rule="At least one eligible authoritative policy-action claim.",
        ),
        RequiredFact(
            fact_key="effective_date",
            question="Is the effective date or implementation status documented?",
            preferred_source_types=[SourceType.GOVERNMENT, SourceType.REGULATOR],
            claim_categories=[ClaimCategory.POLICY_ANNOUNCEMENT],
            required=False,
            freshness_seconds=604_800,
            authoritative_required=True,
            completion_rule="Optional supporting effective-date claim.",
        ),
        RequiredFact(
            fact_key="no_unresolved_contradictions",
            question="Are material contradictions resolved or absent?",
            required=True,
            completion_rule="Zero unresolved contradiction relations.",
        ),
        RequiredFact(
            fact_key="freshness",
            question="Is the newest eligible evidence current?",
            required=True,
            freshness_seconds=86_400,
            completion_rule="Newest eligible evidence is within one day of the cutoff.",
        ),
    ]


_FACT_BUILDERS = {
    MarketType.MANAGER_DEPARTURE_BY_DEADLINE: _manager_departure_facts,
    MarketType.APPOINTMENT_BY_DEADLINE: _appointment_facts,
    MarketType.MATCH_RESULT: _match_result_facts,
    MarketType.PLAYER_PARTICIPATION: _player_participation_facts,
    MarketType.POLICY_ANNOUNCEMENT: _policy_facts,
}


def build_plan(
    target: ResearchTarget,
    *,
    registry: SourceRegistry,
    depth: ResearchDepth = ResearchDepth.STANDARD,
) -> ResearchPlan:
    market_type = classify_market_type(target)
    if market_type is MarketType.UNKNOWN:
        return ResearchPlan(
            market_type=market_type,
            proposition_type=target.market_type or "unknown",
            budget_profile=depth,
            abstention_requirements=["UNSUPPORTED_MARKET_TYPE"],
        )

    facts = _FACT_BUILDERS[market_type]()
    approved = registry.approved_for(market_type.value)
    return ResearchPlan(
        market_type=market_type,
        proposition_type=target.market_type or market_type.value,
        required_facts=facts,
        queries=_queries_for(target, market_type),
        approved_source_ids=[entry.source_id for entry in approved],
        budget_profile=depth,
        stop_conditions=[
            "all mandatory facts satisfied by eligible authoritative evidence",
            "budget exhausted",
            "cancellation requested",
            "unresolved material contradiction requires review",
        ],
        abstention_requirements=[
            "resolution definition or deadline semantics missing",
            "no eligible authoritative evidence for a mandatory fact",
            "unresolved contradiction on a material fact",
            "no approved source available for a required source class",
            "evidence timestamp cannot be proven eligible at cutoff",
        ],
    )


def validate_query_suggestions(
    suggestions: list[str],
    *,
    max_queries: int = _MAX_QUERY_SUGGESTIONS,
) -> list[str]:
    """Validate optional LLM query suggestions against the approved tool contract."""

    if not isinstance(suggestions, list):
        raise ValueError("Query suggestions must be a list.")
    validated: list[str] = []
    for suggestion in suggestions[: max(1, min(max_queries, 20))]:
        if not isinstance(suggestion, str):
            raise ValueError("Query suggestions must be strings.")
        value = " ".join(suggestion.split())
        if not value or len(value) > _MAX_QUERY_LENGTH:
            raise ValueError("Query suggestion is empty or too long.")
        if _CONTROL.search(value) or _URL_LIKE.search(value):
            raise ValueError("Query suggestions may not contain URLs or control characters.")
        validated.append(value)
    return validated
