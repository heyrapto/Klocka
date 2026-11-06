"""Research runtime schema: 13 tables for jobs, evidence, claims, and packs.

Revision ID: 0002_research_runtime_schema
Revises: 0001_initial_schema
Create Date: 2026-11-06 00:00:00.000000+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0002_research_runtime_schema"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# All 18 ResearchJobStatus values (single source of truth: research/enums.py)
_JOB_STATUS_VALUES = (
    "QUEUED",
    "TARGET_VALIDATION",
    "RESEARCH_PLANNING",
    "RESEARCH_SEARCHING",
    "RESEARCH_READING",
    "DOCUMENT_EXTRACTION",
    "CLAIM_EXTRACTION",
    "DEDUPLICATION",
    "CONTRADICTION_REVIEW",
    "EVIDENCE_QUALITY_CHECK",
    "RESEARCH_COMPLETE",
    "READY_FOR_FORECAST",
    "PARTIAL_EVIDENCE",
    "ABSTAINED",
    "NEEDS_HUMAN_REVIEW",
    "CANCELLING",
    "CANCELLED",
    "FAILED",
)


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # 1. research_sources                                                  #
    # ------------------------------------------------------------------ #
    op.create_table(
        "research_sources",
        sa.Column("source_id", sa.String(128), primary_key=True),
        sa.Column("provider_key", sa.String(128), nullable=False),
        sa.Column("domain_pattern", sa.String(255), nullable=False),
        sa.Column("source_type", sa.String(64), nullable=False),
        sa.Column(
            "allowed_market_types",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column(
            "claim_categories",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column("access_method", sa.String(64), nullable=False),
        sa.Column("commercial_use_status", sa.String(64), nullable=False),
        sa.Column("terms_url", sa.String(1024), nullable=True),
        sa.Column("license_or_contract_ref", sa.String(255), nullable=True),
        sa.Column("robots_reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("terms_reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "retention_policy",
            sa.String(128),
            server_default="metadata-only",
            nullable=False,
        ),
        sa.Column(
            "max_requests_per_minute",
            sa.Integer(),
            server_default="10",
            nullable=False,
        ),
        sa.Column(
            "max_concurrency",
            sa.Integer(),
            server_default="1",
            nullable=False,
        ),
        sa.Column(
            "max_response_bytes",
            sa.Integer(),
            server_default="1048576",
            nullable=False,
        ),
        sa.Column(
            "default_freshness_seconds",
            sa.Integer(),
            server_default="3600",
            nullable=False,
        ),
        sa.Column(
            "authority_tier",
            sa.Integer(),
            server_default="3",
            nullable=False,
        ),
        sa.Column("source_authority_ruleset_id", sa.String(128), nullable=True),
        sa.Column(
            "enabled",
            sa.Boolean(),
            server_default="false",
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_research_sources_enabled_commercial",
        "research_sources",
        ["enabled", "commercial_use_status"],
    )
    op.create_index(
        "ix_research_sources_provider_key",
        "research_sources",
        ["provider_key"],
    )

    # ------------------------------------------------------------------ #
    # 2. research_jobs                                                     #
    # ------------------------------------------------------------------ #
    op.create_table(
        "research_jobs",
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("analysis_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "status",
            sa.String(64),
            server_default="QUEUED",
            nullable=False,
        ),
        sa.Column(
            "research_profile",
            sa.String(32),
            server_default="standard",
            nullable=False,
        ),
        sa.Column(
            "requested_depth",
            sa.String(32),
            server_default="standard",
            nullable=False,
        ),
        sa.Column("forecast_cutoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("readiness", sa.String(64), nullable=True),
        sa.Column(
            "readiness_reasons",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column("pack_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_message", sa.String(2000), nullable=True),
        sa.Column(
            "budget_limits",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column(
            "budget_usage",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ({})".format(
                ", ".join(f"'{v}'" for v in _JOB_STATUS_VALUES)
            ),
            name="ck_research_jobs_status",
        ),
    )
    op.create_index(
        "ix_research_jobs_tenant_created",
        "research_jobs",
        ["tenant_id", "created_at"],
    )
    op.create_index(
        "ix_research_jobs_analysis_id",
        "research_jobs",
        ["analysis_id"],
    )
    op.create_index(
        "ix_research_jobs_target_id",
        "research_jobs",
        ["target_id"],
    )
    op.create_index(
        "ix_research_jobs_status_created",
        "research_jobs",
        ["status", "created_at"],
    )

    # ------------------------------------------------------------------ #
    # 3. research_job_outbox                                               #
    # ------------------------------------------------------------------ #
    op.create_table(
        "research_job_outbox",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(32),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_reason", sa.String(500), nullable=True),
    )
    op.create_index(
        "ix_research_job_outbox_status_created",
        "research_job_outbox",
        ["status", "created_at"],
    )
    op.create_index(
        "ix_research_job_outbox_job_id",
        "research_job_outbox",
        ["job_id"],
    )

    # ------------------------------------------------------------------ #
    # 4. research_job_events  (append-only; no UPDATE ever)               #
    # ------------------------------------------------------------------ #
    op.create_table(
        "research_job_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event", sa.String(128), nullable=False),
        sa.Column("stage", sa.String(64), nullable=False),
        sa.Column(
            "data",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_research_job_events_job_created",
        "research_job_events",
        ["job_id", "created_at"],
    )
    op.create_index(
        "ix_research_job_events_job_id",
        "research_job_events",
        ["job_id", "id"],
    )

    # ------------------------------------------------------------------ #
    # 5. research_queries                                                  #
    # ------------------------------------------------------------------ #
    op.create_table(
        "research_queries",
        sa.Column(
            "query_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider_key", sa.String(128), nullable=False),
        sa.Column("query_text", sa.String(2000), nullable=True),
        sa.Column("request_hash", sa.String(80), nullable=True),
        sa.Column("response_id", sa.String(255), nullable=True),
        sa.Column(
            "result_count",
            sa.Integer(),
            server_default="0",
            nullable=True,
        ),
        sa.Column(
            "budget_consumed",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_research_queries_job_created",
        "research_queries",
        ["job_id", "created_at"],
    )
    op.create_index(
        "ix_research_queries_provider_created",
        "research_queries",
        ["provider_key", "created_at"],
    )

    # ------------------------------------------------------------------ #
    # 6. research_documents                                                #
    # ------------------------------------------------------------------ #
    op.create_table(
        "research_documents",
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("canonical_url", sa.String(2048), nullable=False),
        sa.Column("normalized_url", sa.String(2048), nullable=True),
        sa.Column("publisher", sa.String(255), nullable=True),
        sa.Column("author", sa.String(255), nullable=True),
        sa.Column("wire_attribution", sa.String(255), nullable=True),
        sa.Column(
            "independence_group_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_research_documents_job_id",
        "research_documents",
        ["job_id"],
    )
    op.create_index(
        "ix_research_documents_canonical_url",
        "research_documents",
        ["canonical_url"],
    )
    op.create_index(
        "ix_research_documents_independence_group_id",
        "research_documents",
        ["independence_group_id"],
    )

    # ------------------------------------------------------------------ #
    # 7. research_document_versions                                        #
    # ------------------------------------------------------------------ #
    op.create_table(
        "research_document_versions",
        sa.Column(
            "version_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "research_documents.document_id", ondelete="CASCADE"
            ),
            nullable=False,
        ),
        sa.Column(
            "source_id",
            sa.String(128),
            sa.ForeignKey("research_sources.source_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("content_hash", sa.String(80), nullable=False),
        sa.Column("content_object_key", sa.String(1024), nullable=True),
        sa.Column("capture_method", sa.String(64), nullable=False),
        sa.Column(
            "extraction_version",
            sa.String(64),
            server_default="html-extractor-1.0",
            nullable=False,
        ),
        sa.Column("retention_policy_id", sa.String(128), nullable=True),
        sa.Column("event_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "availability_source",
            sa.String(64),
            server_default="capture",
            nullable=False,
        ),
        sa.Column(
            "version_is_valid",
            sa.Boolean(),
            server_default="true",
            nullable=False,
        ),
        sa.Column(
            "quality_flags",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column("title", sa.String(1000), nullable=True),
        sa.Column("content_type", sa.String(128), nullable=True),
        sa.Column("language", sa.String(32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        # A changed content hash creates a new version row
        sa.UniqueConstraint(
            "document_id",
            "content_hash",
            name="uq_document_version_content_hash",
        ),
    )
    op.create_index(
        "ix_research_document_versions_doc_captured",
        "research_document_versions",
        ["document_id", "captured_at"],
    )
    op.create_index(
        "ix_research_document_versions_available_at",
        "research_document_versions",
        ["available_at"],
    )
    op.create_index(
        "ix_research_document_versions_source_id",
        "research_document_versions",
        ["source_id"],
    )

    # ------------------------------------------------------------------ #
    # 8. research_source_groups                                            #
    # ------------------------------------------------------------------ #
    op.create_table(
        "research_source_groups",
        sa.Column(
            "group_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("label", sa.String(500), nullable=True),
        sa.Column(
            "member_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column("canonical_source_id", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_research_source_groups_job_id",
        "research_source_groups",
        ["job_id"],
    )

    # ------------------------------------------------------------------ #
    # 9. research_claims                                                   #
    # ------------------------------------------------------------------ #
    op.create_table(
        "research_claims",
        sa.Column(
            "claim_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "document_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "research_document_versions.version_id", ondelete="SET NULL"
            ),
            nullable=True,
        ),
        sa.Column(
            "source_id",
            sa.String(128),
            sa.ForeignKey("research_sources.source_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "source_group_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "research_source_groups.group_id", ondelete="SET NULL"
            ),
            nullable=True,
        ),
        sa.Column("claim_text", sa.String(5000), nullable=False),
        sa.Column("claim_category", sa.String(64), nullable=False),
        sa.Column(
            "subject_entity_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column("predicate", sa.String(255), nullable=True),
        sa.Column("object", sa.String(500), nullable=True),
        sa.Column("stance_on_proposition", sa.String(32), nullable=False),
        sa.Column("claim_modality", sa.String(64), nullable=False),
        sa.Column("epistemic_state", sa.String(64), nullable=False),
        sa.Column(
            "support_state",
            sa.String(64),
            server_default="single_source",
            nullable=False,
        ),
        sa.Column(
            "eligibility_state",
            sa.String(64),
            server_default="timestamp_uncertain",
            nullable=False,
        ),
        sa.Column(
            "evidence_span",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "independence_group_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("extraction_model_version", sa.String(128), nullable=True),
        sa.Column("extraction_prompt_version", sa.String(128), nullable=True),
        sa.Column(
            "validation_state",
            sa.String(64),
            server_default="pending_review",
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "stance_on_proposition IN ('supports_yes','supports_no','neutral','unresolved')",
            name="ck_research_claims_stance",
        ),
    )
    op.create_index(
        "ix_research_claims_job_created",
        "research_claims",
        ["job_id", "created_at"],
    )
    op.create_index(
        "ix_research_claims_target_available",
        "research_claims",
        ["target_id", "available_at"],
    )
    op.create_index(
        "ix_research_claims_document_version_id",
        "research_claims",
        ["document_version_id"],
    )
    op.create_index(
        "ix_research_claims_claim_category",
        "research_claims",
        ["claim_category"],
    )
    op.create_index(
        "ix_research_claims_stance_on_proposition",
        "research_claims",
        ["stance_on_proposition"],
    )

    # ------------------------------------------------------------------ #
    # 10. research_claim_relations                                         #
    # ------------------------------------------------------------------ #
    op.create_table(
        "research_claim_relations",
        sa.Column(
            "relation_id",
            sa.BigInteger(),
            autoincrement=True,
            primary_key=True,
        ),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "from_claim_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("research_claims.claim_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "to_claim_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("research_claims.claim_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("relation_type", sa.String(32), nullable=False),
        sa.Column("contradiction_kind", sa.String(32), nullable=True),
        sa.Column(
            "resolved",
            sa.Boolean(),
            server_default="false",
            nullable=False,
        ),
        sa.Column("reviewer_notes", sa.String(2000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "relation_type IN ('contradicts','supports','duplicate','quotes','supersedes')",
            name="ck_research_claim_relations_type",
        ),
    )
    op.create_index(
        "ix_research_claim_relations_job_id",
        "research_claim_relations",
        ["job_id"],
    )
    op.create_index(
        "ix_research_claim_relations_from_claim_id",
        "research_claim_relations",
        ["from_claim_id"],
    )
    op.create_index(
        "ix_research_claim_relations_to_claim_id",
        "research_claim_relations",
        ["to_claim_id"],
    )

    # ------------------------------------------------------------------ #
    # 11. evidence_packs  (immutable; never UPDATE)                        #
    # ------------------------------------------------------------------ #
    op.create_table(
        "evidence_packs",
        sa.Column(
            "pack_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("research_jobs.job_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pack_hash", sa.String(80), nullable=False),
        sa.Column(
            "manifest",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("readiness", sa.String(64), nullable=False),
        sa.Column(
            "readiness_reasons",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column("cutoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        # Immutable: pack_hash uniqueness enforces content-addressability
        sa.UniqueConstraint("pack_hash", name="uq_evidence_packs_hash"),
    )
    op.create_index(
        "ix_evidence_packs_job_id",
        "evidence_packs",
        ["job_id"],
    )
    op.create_index(
        "ix_evidence_packs_target_cutoff",
        "evidence_packs",
        ["target_id", "cutoff_at"],
    )

    # ------------------------------------------------------------------ #
    # 12. evidence_pack_items  (immutable; never UPDATE)                   #
    # ------------------------------------------------------------------ #
    op.create_table(
        "evidence_pack_items",
        sa.Column(
            "item_id",
            sa.BigInteger(),
            autoincrement=True,
            primary_key=True,
        ),
        sa.Column(
            "pack_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("evidence_packs.pack_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("item_type", sa.String(32), nullable=False),
        sa.Column("item_ref_id", sa.String(128), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "pack_id",
            "item_type",
            "item_ref_id",
            name="uq_evidence_pack_items_ref",
        ),
        sa.CheckConstraint(
            "item_type IN ('document_version','claim','source_group')",
            name="ck_evidence_pack_items_type",
        ),
    )

    # ------------------------------------------------------------------ #
    # 13. forecast_input_snapshots                                         #
    # ------------------------------------------------------------------ #
    op.create_table(
        "forecast_input_snapshots",
        sa.Column(
            "snapshot_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "evidence_pack_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("evidence_packs.pack_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("evidence_pack_hash", sa.String(80), nullable=True),
        sa.Column(
            "forecast_cutoff_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "target_schema_version",
            sa.String(64),
            server_default="target-v2",
            nullable=False,
        ),
        sa.Column(
            "source_ruleset_version",
            sa.String(64),
            server_default="sources-v1",
            nullable=False,
        ),
        sa.Column(
            "claim_schema_version",
            sa.String(64),
            server_default="claims-v1",
            nullable=False,
        ),
        sa.Column(
            "required_facts",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column(
            "eligible_claims",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column(
            "contradictions",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column("market_snapshot_ref", sa.String(128), nullable=True),
        sa.Column("feature_snapshot_ref", sa.String(128), nullable=True),
        sa.Column("readiness", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_forecast_input_snapshots_job_id",
        "forecast_input_snapshots",
        ["job_id"],
    )
    op.create_index(
        "ix_forecast_input_snapshots_target_cutoff",
        "forecast_input_snapshots",
        ["target_id", "forecast_cutoff_at"],
    )

    # ------------------------------------------------------------------ #
    # Deferred FK: research_jobs.pack_id → evidence_packs.pack_id         #
    # Added after evidence_packs is created to avoid a circular reference  #
    # ------------------------------------------------------------------ #
    op.create_foreign_key(
        "fk_research_jobs_pack_id",
        "research_jobs",
        "evidence_packs",
        ["pack_id"],
        ["pack_id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    # Remove deferred FK first
    op.drop_constraint(
        "fk_research_jobs_pack_id",
        "research_jobs",
        type_="foreignkey",
    )

    # Drop tables in reverse dependency order
    op.drop_table("forecast_input_snapshots")
    op.drop_table("evidence_pack_items")
    op.drop_table("evidence_packs")
    op.drop_table("research_claim_relations")
    op.drop_table("research_claims")
    op.drop_table("research_source_groups")
    op.drop_table("research_document_versions")
    op.drop_table("research_documents")
    op.drop_table("research_queries")
    op.drop_table("research_job_events")
    op.drop_table("research_job_outbox")
    op.drop_table("research_jobs")
    op.drop_table("research_sources")
