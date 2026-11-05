from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ForecastEvidenceItem(BaseModel):
    """Documented evidence citation backing the probabilistic assessment."""

    model_config = ConfigDict(extra="forbid")

    evidence_type: str = Field(description="Type of evidence e.g. rule_analysis, market_microstructure, external_fact")
    stance: Literal["supports_yes", "supports_no", "neutral"] = "neutral"
    relevance_score: Decimal = Field(default=Decimal("0.5"), ge=0, le=1)
    snippet: str = Field(max_length=2000)
    source_citation: str = Field(max_length=500)
    source_url: str | None = None


class GeminiForecastOutput(BaseModel):
    """Raw structured output schema requested from Gemini reasoning model."""

    model_config = ConfigDict(extra="forbid")

    raw_probability_yes: float = Field(ge=0.01, le=0.99, description="Subjective probability P(YES) based on resolution criteria and facts")
    uncertainty_lower: float = Field(ge=0.0, le=1.0, description="Lower bound of 90% credible interval")
    uncertainty_upper: float = Field(ge=0.0, le=1.0, description="Upper bound of 90% credible interval")
    resolution_risk_flags: list[str] = Field(default_factory=list, description="Potential contract ambiguities, edge cases, or resolution risks")
    key_drivers: list[str] = Field(default_factory=list, description="Primary causal factors determining the outcome")
    reasoning_summary: str = Field(max_length=3000, description="Structured analytic argument")
    evidence: list[ForecastEvidenceItem] = Field(default_factory=list)


class ForecastResult(BaseModel):
    """Final validated and calibrated forecast result."""

    model_config = ConfigDict(extra="forbid")

    probability_yes: Decimal = Field(ge=0, le=1)
    probability_no: Decimal = Field(ge=0, le=1)
    uncertainty_interval: list[Decimal] = Field(default_factory=list, min_length=2, max_length=2)
    model_version: str
    prompt_version: str
    calibration_version: str | None = None
    market_implied_probability: Decimal | None = None
    reasoning: str
    key_drivers: list[str] = Field(default_factory=list)
    resolution_risk_flags: list[str] = Field(default_factory=list)
    evidence: list[ForecastEvidenceItem] = Field(default_factory=list)
    input_snapshot: dict = Field(default_factory=dict)
