import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Index, Numeric, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from prediction_ai.db.base import Base


class Forecast(Base):
    __tablename__ = "forecasts"
    __table_args__ = (
        Index("ix_forecasts_user_created", "user_id", "created_at"),
        Index("ix_forecasts_market_forecasted", "market_id", "forecasted_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    analysis_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("analyses.id", ondelete="RESTRICT"),
        unique=True,
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False
    )
    market_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("prediction_markets.id", ondelete="RESTRICT"), nullable=False
    )
    forecasted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    target_definition: Mapped[dict] = mapped_column(JSONB, nullable=False)
    probability_yes: Mapped[Decimal] = mapped_column(Numeric(6, 5), nullable=False)
    probability_no: Mapped[Decimal] = mapped_column(Numeric(6, 5), nullable=False)
    uncertainty_interval: Mapped[list[Decimal] | None] = mapped_column(
        ARRAY(Numeric(6, 5)), nullable=True
    )
    recommendation: Mapped[str] = mapped_column(String(16), nullable=False)
    decision_metrics: Mapped[dict] = mapped_column(JSONB, nullable=False)
    data_quality: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    model_version: Mapped[str] = mapped_column(String(64), nullable=False)
    calibration_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    decision_policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    input_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    explanation: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )


class ForecastEvidence(Base):
    __tablename__ = "forecast_evidence"
    __table_args__ = (Index("ix_forecast_evidence_forecast", "forecast_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    forecast_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("forecasts.id", ondelete="CASCADE"), nullable=False
    )
    evidence_type: Mapped[str] = mapped_column(String(48), nullable=False)
    stance: Mapped[str] = mapped_column(String(32), default="neutral", nullable=False)
    relevance_score: Mapped[Decimal] = mapped_column(Numeric(4, 3), default=Decimal("0.5"))
    snippet: Mapped[str] = mapped_column(Text, nullable=False)
    source_citation: Mapped[str] = mapped_column(String(1024), nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    source_timestamp: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
