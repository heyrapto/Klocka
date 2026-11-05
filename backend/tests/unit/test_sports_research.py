from prediction_ai.specialists.research import SpecialistResearchService
from prediction_ai.core.workflow import AnalysisStatus, TERMINAL_STATUSES


def test_research_failed_is_a_terminal_analysis_status():
    assert AnalysisStatus.RESEARCH_FAILED.value == "RESEARCH_FAILED"
    assert AnalysisStatus.RESEARCH_FAILED in TERMINAL_STATUSES


def test_sports_research_abstains_without_verified_sources():
    pack = SpecialistResearchService()._gather_sports_research(
        "When Will Michael Carrick Leave Manchester United?"
    )

    assert pack["recommendation_gate"] == "abstain"
    assert pack["resolution_definition_verified"] is False
    assert pack["deadline_verified"] is False
    assert "NO_VERIFIED_EVIDENCE" in pack["quality_flags"]
    assert pack["claims"] == []
