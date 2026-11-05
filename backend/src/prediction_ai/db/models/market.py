import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from prediction_ai.db.base import Base


class PredictionMarket(Base):
    __tablename__ = "prediction_markets"
    __table_args__ = (
        UniqueConstraint("platform", "platform_market_id", name="uq_platform_market"),
        Index("ix_prediction_markets_platform_status", "platform", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    platform_market_id: Mapped[str] = mapped_column(String(128), nullable=False)
    platform_event_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    slug: Mapped[str | None] = mapped_column(String(255), nullable=True)
    url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    title: Mapped[str] = mapped_column(String(1024), nullable=False)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    market_type: Mapped[str] = mapped_column(String(64), nullable=False)
    underlying_symbol: Mapped[str | None] = mapped_column(String(64), nullable=True)
    outcomes: Mapped[dict] = mapped_column(JSONB, nullable=False)
    opens_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closes_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolves_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_source: Mapped[str | None] = mapped_column(String, nullable=True)
    resolution_criteria: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    order_book_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    raw_metadata: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )


class MarketSnapshot(Base):
    __tablename__ = "market_snapshots"
    __table_args__ = (Index("ix_market_snapshots_market_observed", "market_id", "observed_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    market_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("prediction_markets.id", ondelete="CASCADE"), nullable=False
    )
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    outcome_quotes: Mapped[dict] = mapped_column(JSONB, nullable=False)
    liquidity: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)
    spread: Mapped[Decimal | None] = mapped_column(Numeric(10, 6), nullable=True)
    provider_sequence: Mapped[str | None] = mapped_column(String(255), nullable=True)
    quality_flags: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
