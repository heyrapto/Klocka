from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from prediction_ai.schemas.decision import DecisionMetrics, RecommendationType
from prediction_ai.schemas.market import NormalizedOutcome


class EvidenceItem(BaseModel):
    """Structured, point-in-time evidence citation."""

    model_config = ConfigDict(extra="forbid")

    evidence_id: str
    evidence_type: str
    stance: str
    relevance_score: Decimal = Field(ge=Decimal("0.0"), le=Decimal("1.0"))
    snippet: str
    source_citation: str
    source_url: str | None = None
    source_timestamp: datetime | None = None


class MarketSummary(BaseModel):
    """Verified market identity returned with a forecast."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    platform: str
    platform_market_id: str
    slug: str | None = None
    url: str | None = None
    title: str
    category: str
    market_type: str
    underlying_symbol: str | None = None
    outcomes: list[NormalizedOutcome]
    resolves_at: datetime | None = None
    resolution_source: str | None = None
    resolution_criteria: str | None = None
    status: str


class ProbabilityForecast(BaseModel):
    """Internal forecast output before the decision layer runs."""

    model_config = ConfigDict(extra="forbid")

    probability_yes: Decimal = Field(gt=Decimal("0.0"), lt=Decimal("1.0"))
    probability_no: Decimal = Field(gt=Decimal("0.0"), lt=Decimal("1.0"))
    uncertainty_interval: list[Decimal] = Field(min_length=2, max_length=2)
    model_version: str
    calibration_version: str | None = None
    method: str
    diagnostics: dict = Field(default_factory=dict)


class ForecastSummary(BaseModel):
    """Compact forecast payload used by the analysis screen and SSE."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    analysis_id: UUID
    market: MarketSummary
    forecasted_at: datetime
    probability_yes: Decimal
    probability_no: Decimal
    uncertainty_interval: list[Decimal]
    recommendation: RecommendationType
    decision_metrics: DecisionMetrics
    data_quality: dict
    model_version: str
    prompt_version: str
    decision_policy_version: str
    explanation: dict


class ForecastResponse(ForecastSummary):
    """Full immutable forecast snapshot."""

    calibration_version: str | None = None
    evidence: list[EvidenceItem] = Field(default_factory=list)
    input_snapshot: dict = Field(default_factory=dict)
