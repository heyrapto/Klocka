"""Unit tests for the deterministic research planner."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from prediction_ai.research.enums import MarketType, ResearchDepth, TargetVerificationStatus
from prediction_ai.research.models import ResearchTarget, SubjectEntity
from prediction_ai.research.planner import (
    build_plan,
    classify_market_type,
    validate_query_suggestions,
)
from prediction_ai.research.registry import SourceRegistry

_NOW = datetime(2026, 11, 5, 18, 0, 0, tzinfo=UTC)


def _target(proposition: str, market_type: str = "unknown") -> ResearchTarget:
    return ResearchTarget(
        target_id=uuid4(),
        platform="polymarket",
        platform_market_id="mkt-001",
        proposition=proposition,
        market_type=market_type,
        verification_status=TargetVerificationStatus.TARGET_VERIFIED,
        created_at=_NOW,
        subject_entities=[
            SubjectEntity(entity_type="person", name="Test Manager"),
            SubjectEntity(entity_type="organization", name="Test Club"),
        ],
        deadline_at=_NOW,
        timezone="Europe/London",
    )


class TestClassifyMarketType:
    def test_manager_departure(self) -> None:
        t = _target("Will the manager leave Test Club before the deadline?")
        assert classify_market_type(t) == MarketType.MANAGER_DEPARTURE_BY_DEADLINE

    def test_sacked_keyword(self) -> None:
        t = _target("Will Pep be sacked by Man City?")
        assert classify_market_type(t) == MarketType.MANAGER_DEPARTURE_BY_DEADLINE

    def test_resign_keyword(self) -> None:
        t = _target("Will the coach resign before January?")
        assert classify_market_type(t) == MarketType.MANAGER_DEPARTURE_BY_DEADLINE

    def test_appointment(self) -> None:
        t = _target("Will a new manager be appointed at Club X?")
        assert classify_market_type(t) == MarketType.APPOINTMENT_BY_DEADLINE

    def test_match_result(self) -> None:
        t = _target("Will Team A beat Team B in the final?")
        assert classify_market_type(t) == MarketType.MATCH_RESULT

    def test_player_participation(self) -> None:
        t = _target("Will Player X start in the lineup vs Team B?")
        assert classify_market_type(t) == MarketType.PLAYER_PARTICIPATION

    def test_policy_announcement(self) -> None:
        t = _target("Will the government announce new legislation before December?")
        assert classify_market_type(t) == MarketType.POLICY_ANNOUNCEMENT

    def test_unknown_returns_unknown(self) -> None:
        t = _target("Something completely unrecognizable xyz zyx")
        assert classify_market_type(t) == MarketType.UNKNOWN


class TestBuildPlan:
    def _registry_with_no_sources(self) -> SourceRegistry:
        return SourceRegistry.from_entries([])

    def test_unknown_market_type_produces_abstention(self) -> None:
        t = _target("xyz unrecognizable proposition")
        plan = build_plan(t, registry=self._registry_with_no_sources())
        assert "UNSUPPORTED_MARKET_TYPE" in plan.abstention_requirements

    def test_manager_departure_plan_has_required_facts(self) -> None:
        t = _target("Will the manager leave before the deadline?")
        plan = build_plan(t, registry=self._registry_with_no_sources())
        fact_keys = [f.fact_key for f in plan.required_facts]
        assert "current_manager_status" in fact_keys
        assert "resolution_definition" in fact_keys
        assert "deadline_semantics" in fact_keys

    def test_match_result_plan_has_fixture_fact(self) -> None:
        t = _target("Will Team A beat Team B?")
        plan = build_plan(t, registry=self._registry_with_no_sources())
        fact_keys = [f.fact_key for f in plan.required_facts]
        assert "fixture_status" in fact_keys

    def test_plan_queries_are_non_empty_for_known_market(self) -> None:
        t = _target("Will the manager leave before the deadline?")
        plan = build_plan(t, registry=self._registry_with_no_sources())
        assert len(plan.queries) > 0

    def test_plan_queries_contain_entity_names(self) -> None:
        t = _target("Will Test Manager leave Test Club?")
        plan = build_plan(t, registry=self._registry_with_no_sources())
        # At least one query should contain part of the entity names
        combined = " ".join(plan.queries).lower()
        assert "test manager" in combined or "test club" in combined

    def test_plan_stop_conditions_present(self) -> None:
        t = _target("Will the manager leave before the deadline?")
        plan = build_plan(t, registry=self._registry_with_no_sources())
        assert len(plan.stop_conditions) > 0

    def test_depth_propagates_to_budget_profile(self) -> None:
        t = _target("Will the manager leave before the deadline?")
        plan_quick = build_plan(t, registry=self._registry_with_no_sources(), depth=ResearchDepth.QUICK)
        plan_deep = build_plan(t, registry=self._registry_with_no_sources(), depth=ResearchDepth.DEEP)
        assert plan_quick.budget_profile == ResearchDepth.QUICK
        assert plan_deep.budget_profile == ResearchDepth.DEEP


class TestValidateQuerySuggestions:
    def test_accepts_clean_queries(self) -> None:
        suggestions = ["Manchester United manager news", "Erik ten Hag contract"]
        result = validate_query_suggestions(suggestions)
        assert result == suggestions

    def test_rejects_url_in_query(self) -> None:
        with pytest.raises(ValueError, match="URLs"):
            validate_query_suggestions(["https://example.com search"])

    def test_rejects_www_in_query(self) -> None:
        with pytest.raises(ValueError, match="URLs"):
            validate_query_suggestions(["www.example.com news"])

    def test_rejects_control_characters(self) -> None:
        with pytest.raises(ValueError, match="control"):
            validate_query_suggestions(["query\x00inject"])

    def test_rejects_empty_query(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            validate_query_suggestions([""])

    def test_rejects_oversized_query(self) -> None:
        with pytest.raises(ValueError, match="long"):
            validate_query_suggestions(["a" * 301])

    def test_caps_at_max_queries(self) -> None:
        suggestions = ["query " + str(i) for i in range(50)]
        result = validate_query_suggestions(suggestions, max_queries=5)
        assert len(result) <= 5

    def test_rejects_non_list_input(self) -> None:
        with pytest.raises(ValueError, match="list"):
            validate_query_suggestions("not a list")  # type: ignore[arg-type]

    def test_rejects_non_string_items(self) -> None:
        with pytest.raises(ValueError, match="strings"):
            validate_query_suggestions([123])  # type: ignore[list-item]
