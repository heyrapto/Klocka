"""Initial database schema for prediction intelligence platform.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-11-04 15:30:00.000000+00:00

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Profiles
    op.create_table(
        "profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("display_name", sa.String(), nullable=True),
        sa.Column("tier", sa.String(), server_default="standard", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    # 2. Uploaded Assets
    op.create_table(
        "uploaded_assets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "owner_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("bucket", sa.String(128), server_default="screenshots", nullable=False),
        sa.Column("object_path", sa.String(512), unique=True, nullable=False),
        sa.Column("mime_type", sa.String(128), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("asset_type", sa.String(32), server_default="screenshot", nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_uploaded_assets_owner_created", "uploaded_assets", ["owner_id", "created_at"])

    # 3. Prediction Markets
    op.create_table(
        "prediction_markets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("platform", sa.String(32), nullable=False),
        sa.Column("platform_market_id", sa.String(128), nullable=False),
        sa.Column("platform_event_id", sa.String(128), nullable=True),
        sa.Column("slug", sa.String(255), nullable=True),
        sa.Column("url", sa.String(1024), nullable=True),
        sa.Column("title", sa.String(1024), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("market_type", sa.String(64), nullable=False),
        sa.Column("underlying_symbol", sa.String(64), nullable=True),
        sa.Column("outcomes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("opens_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closes_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolves_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_source", sa.String(), nullable=True),
        sa.Column("resolution_criteria", sa.Text(), nullable=True),
        sa.Column("status", sa.String(32), server_default="active", nullable=False),
        sa.Column("order_book_enabled", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("raw_metadata", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("platform", "platform_market_id", name="uq_platform_market"),
    )
    op.create_index("ix_prediction_markets_platform_status", "prediction_markets", ["platform", "status"])

    # 4. Market Snapshots
    op.create_table(
        "market_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "market_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("prediction_markets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("outcome_quotes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("liquidity", sa.Numeric(20, 4), nullable=True),
        sa.Column("spread", sa.Numeric(10, 6), nullable=True),
        sa.Column("provider_sequence", sa.String(255), nullable=True),
        sa.Column("quality_flags", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
    )
    op.create_index("ix_market_snapshots_market_observed", "market_snapshots", ["market_id", "observed_at"])

    # 5. Analyses
    op.create_table(
        "analyses",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(48), server_default="RECEIVED", nullable=False),
        sa.Column("platform", sa.String(32), nullable=True),
        sa.Column(
            "market_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("prediction_markets.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "screenshot_asset_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("uploaded_assets.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("hint", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.String(128), nullable=True),
        sa.Column("request_hash", sa.String(64), nullable=True),
        sa.Column("extraction_result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("verification_result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_analyses_user_idempotency"),
    )
    op.create_index("ix_analyses_user_created", "analyses", ["user_id", "created_at"])
    op.create_index("ix_analyses_status_created", "analyses", ["status", "created_at"])
    op.create_index("ix_analyses_market_id", "analyses", ["market_id"])

    # 6. Analysis Events
    op.create_table(
        "analysis_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column(
            "analysis_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("analyses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("stage", sa.String(48), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("analysis_id", "sequence_number", name="uq_analysis_events_sequence"),
    )
    op.create_index("ix_analysis_events_analysis_id", "analysis_events", ["analysis_id", "id"])

    # 7. Forecasts
    op.create_table(
        "forecasts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "analysis_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("analyses.id", ondelete="RESTRICT"),
            unique=True,
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "market_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("prediction_markets.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("forecasted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("target_definition", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("probability_yes", sa.Numeric(6, 5), nullable=False),
        sa.Column("probability_no", sa.Numeric(6, 5), nullable=False),
        sa.Column("uncertainty_interval", postgresql.ARRAY(sa.Numeric(6, 5)), nullable=True),
        sa.Column("recommendation", sa.String(16), nullable=False),
        sa.Column("decision_metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("data_quality", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("model_version", sa.String(64), nullable=False),
        sa.Column("calibration_version", sa.String(64), nullable=True),
        sa.Column("prompt_version", sa.String(64), nullable=False),
        sa.Column("decision_policy_version", sa.String(64), nullable=False),
        sa.Column("input_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("explanation", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_forecasts_user_created", "forecasts", ["user_id", "created_at"])
    op.create_index("ix_forecasts_market_forecasted", "forecasts", ["market_id", "forecasted_at"])

    # 8. Forecast Evidence
    op.create_table(
        "forecast_evidence",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "forecast_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("forecasts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("evidence_type", sa.String(48), nullable=False),
        sa.Column("stance", sa.String(32), server_default="neutral", nullable=False),
        sa.Column("relevance_score", sa.Numeric(4, 3), server_default="0.5", nullable=False),
        sa.Column("snippet", sa.Text(), nullable=False),
        sa.Column("source_citation", sa.String(1024), nullable=False),
        sa.Column("source_url", sa.String(1024), nullable=True),
        sa.Column("source_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_forecast_evidence_forecast", "forecast_evidence", ["forecast_id"])


def downgrade() -> None:
    op.drop_table("forecast_evidence")
    op.drop_table("forecasts")
    op.drop_table("analysis_events")
    op.drop_table("analyses")
    op.drop_table("market_snapshots")
    op.drop_table("prediction_markets")
    op.drop_table("uploaded_assets")
    op.drop_table("profiles")
