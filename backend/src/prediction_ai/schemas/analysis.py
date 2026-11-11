from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from prediction_ai.schemas.decision import DecisionMetrics, RecommendationType
from prediction_ai.schemas.forecast import ForecastSummary, MarketSummary
from prediction_ai.schemas.market import MarketMatchCandidate


class ExtractedOutcome(BaseModel):
    """Outcome label and displayed price/probability extracted from image."""

    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=128)
    displayed_price: Decimal | None = Field(default=None, ge=0, le=1)
    displayed_probability: Decimal | None = Field(default=None, ge=0, le=1)
    text_confidence: float = Field(ge=0.0, le=1.0)


class EvidenceRegion(BaseModel):
    """Bounding box coordinates and OCR text provenance in original screenshot."""

    model_config = ConfigDict(extra="forbid")

    field: str = Field(min_length=1, max_length=128)
    text: str = Field(max_length=1000)
    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    width: float = Field(ge=0.0, le=1.0)
    height: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)


class ScreenshotChartObservation(BaseModel):
    """Qualitative chart state visible in a screenshot."""

    model_config = ConfigDict(extra="forbid")

    time_range_text: str | None = Field(default=None, max_length=128)
    direction: Literal["rising", "falling", "flat", "mixed", "unknown"] = "unknown"
    target_line_visible: bool | None = None
    observation_confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class ScreenshotMarketCandidate(BaseModel):
    """Raw candidate market extracted from a screenshot before verification."""

    model_config = ConfigDict(extra="forbid")

    platform: Literal["polymarket", "bayse", "unknown"] = "unknown"
    market_title: str | None = Field(default=None, max_length=1000)
    market_url: str | None = Field(default=None, max_length=1000)
    market_id: str | None = Field(default=None, max_length=255)
    market_slug: str | None = Field(default=None, max_length=255)
    asset_or_entity: str | None = Field(default=None, max_length=255)
    category: Literal["crypto", "sports", "news", "finance", "politics", "other", "unknown"] = (
        "unknown"
    )
    market_type: str | None = Field(default=None, max_length=128)
    outcomes: list[ExtractedOutcome] = Field(default_factory=list, max_length=10)
    target_price: Decimal | None = Field(default=None, gt=0)
    target_currency: str | None = Field(default=None, max_length=16)
    comparison_operator: Literal[">", ">=", "<", "<=", "touch", "unknown"] = "unknown"
    displayed_underlying_price: Decimal | None = Field(default=None, gt=0)
    displayed_probability: Decimal | None = Field(default=None, ge=0, le=1)
    displayed_expiry: datetime | None = None
    countdown_text: str | None = Field(default=None, max_length=64)
    current_round_label: str | None = Field(default=None, max_length=128)
    previous_round_labels: list[str] = Field(default_factory=list, max_length=20)
    chart_observation: ScreenshotChartObservation | None = None
    visible_trade_count: int | None = Field(default=None, ge=0)
    visible_timestamp_text: str | None = Field(default=None, max_length=128)
    displayed_resolution_text: str | None = Field(default=None, max_length=4000)
    evidence_regions: list[EvidenceRegion] = Field(default_factory=list, max_length=50)
    needs_user_confirmation: bool = False
    extraction_warnings: list[str] = Field(default_factory=list, max_length=20)
    extraction_source: Literal["vision", "hint", "none"] = "vision"
    overall_confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    @field_validator("displayed_expiry", mode="before")
    @classmethod
    def coerce_expiry_string(cls, v: object) -> datetime | None:
        """Accept ISO-8601 strings, natural-language dates, or None.

        Gemini VLM reads the expiry text verbatim from the screenshot and may
        return a human-readable string such as ``"8:45 PM GMT on November 4, 2026"``
        rather than an ISO-8601 timestamp.  Pydantic cannot parse that natively,
        so we coerce it here using ``dateutil.parser.parse`` and fall back to
        ``None`` (with no exception raised) when the string is unrecognisable.
        This keeps the extraction pipeline alive even when the displayed expiry
        cannot be normalised.
        """
        if v is None or isinstance(v, datetime):
            return v
        if not isinstance(v, str) or not v.strip():
            return None
        try:
            from dateutil import parser as dateutil_parser

            parsed = dateutil_parser.parse(v, fuzzy=True)
            # Ensure timezone-aware UTC if no tzinfo was present in the string
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed
        except Exception:
            # Unrecognisable text — treat as absent rather than hard-failing
            return None

    @field_validator("target_price", "displayed_underlying_price", mode="before")
    @classmethod
    def coerce_missing_positive_price(cls, v: object) -> object:
        """Treat Gemini's zero sentinel as missing for optional price fields."""
        if v == 0 or v == 0.0 or v == "0" or v == "0.0":
            return None
        return v


class AnalysisCreateRequest(BaseModel):
    """Payload to trigger an analysis from an existing uploaded asset."""

    model_config = ConfigDict(extra="forbid")

    screenshot_asset_id: UUID | None = None
    hint: str | None = Field(default=None, max_length=500)
    idempotency_key: str | None = Field(default=None, max_length=128)


class MarketConfirmationRequest(BaseModel):
    """User selection when market identification requires confirmation."""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(min_length=1, max_length=255)


class AnalysisCreateResponse(BaseModel):
    """Accepted asynchronous analysis job."""

    model_config = ConfigDict(extra="forbid")

    analysis_id: UUID
    status: str
    status_url: str
    events_url: str


AnalysisResponse = AnalysisCreateResponse


class StageEventData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analysis_id: UUID
    stage: str
    status: Literal["in_progress", "completed", "failed"]
    message: str
    timestamp: datetime


class WarningEventData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analysis_id: UUID
    warning_code: str
    message: str
    timestamp: datetime


class ConfirmationCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    platform: str
    title: str
    url: str | None = None
    resolves_at: datetime | None = None
    similarity_score: float = Field(ge=0.0, le=1.0)


class ConfirmationRequiredEventData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analysis_id: UUID
    status: Literal["NEEDS_USER_CONFIRMATION"]
    extracted_title: str | None = None
    candidates: list[ConfirmationCandidate] = Field(default_factory=list)
    message: str
    timestamp: datetime


class AnalysisCompletedEventData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analysis_id: UUID
    status: Literal["COMPLETED"]
    forecast_id: UUID
    recommendation: RecommendationType
    probability_yes: Decimal
    probability_no: Decimal
    market_title: str
    result_url: str
    timestamp: datetime


class AnalysisFailedEventData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analysis_id: UUID
    status: str
    error_code: str
    message: str
    can_retry: bool = False
    timestamp: datetime


class EventEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    event: str
    stage: str
    data: dict
    created_at: datetime


class VerificationSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    score: float = Field(ge=0.0, le=1.0)
    selected_market: MarketSummary | None = None
    candidates: list[MarketMatchCandidate] = Field(default_factory=list)
    discrepancies: list[str] = Field(default_factory=list)


class AnalysisErrorInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str


class AnalysisDetailResponse(BaseModel):
    """Durable analysis state with extraction, verification and forecast links."""

    model_config = ConfigDict(extra="forbid")

    analysis_id: UUID
    status: str
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    extraction: dict | None = None
    verification: dict | None = None
    forecast: ForecastSummary | None = None
    error: AnalysisErrorInfo | None = None
    events: list[EventEnvelope] = Field(default_factory=list)


class ForecastDecisionSummary(BaseModel):
    """Small decision block used in API responses without full metrics."""

    model_config = ConfigDict(extra="forbid")

    recommendation: RecommendationType
    reason: str
    is_abstention: bool
    gating_flags: list[str]
    metrics: DecisionMetrics


class AnalysisFeedbackRequest(BaseModel):
    """User-submitted actual outcome and post-settlement feedback for continuous learning."""

    model_config = ConfigDict(extra="forbid")

    actual_outcome: Literal[
        "YES",
        "NO",
        "UP",
        "DOWN",
        "HIGHER",
        "LOWER",
        "BUY UP",
        "BUY DOWN",
    ]
    notes: str | None = Field(default=None, max_length=2000)
    settled_price: Decimal | None = Field(default=None, ge=0, le=1)


class AnalysisFeedbackResponse(BaseModel):
    """Response containing AI postmortem reflection and updated accuracy tracking."""

    model_config = ConfigDict(extra="forbid")

    feedback_id: UUID
    analysis_id: UUID
    actual_outcome: str
    was_correct: bool
    evaluation_eligible: bool = True
    prediction_made: str
    postmortem_analysis: str
    key_learnings: list[str]
    discrepancy_classification: str | None = None
    visual_observations: str | None = None
    created_at: datetime


class AssetCalibrationResponse(BaseModel):
    """Calibrated statistical parameters and learning weights for an asset."""

    model_config = ConfigDict(extra="forbid")

    symbol: str
    category: str
    sample_count: int
    correct_count: int
    win_rate: float | None = None
    brier_score: float
    vol_multiplier: float
    momentum_weight: float
    order_flow_weight: float
    uncertainty_margin: float
    last_discrepancy_reason: str | None = None
    updated_at: datetime
