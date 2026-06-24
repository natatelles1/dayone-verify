"""create_schema

Revision ID: 0001
Revises:
Create Date: 2026-06-24

Revisão manual do autogenerate:
  + CREATE EXTENSION IF NOT EXISTS pgcrypto
  + FK cross-schema user_profiles(id) → auth.users(id) ON DELETE CASCADE
  + Índice funcional uq_companies_state_entity_number (upper/btrim)
  + Função set_updated_at() + triggers em 8 tabelas mutáveis
  + Função check_merge_same_state() + trigger em company_merges
  + RLS habilitado em todas as 15 tabelas
  + Correção nullable: candidate_count, found_count, ready_count → NOT NULL
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# ─── raw SQL blocks ───────────────────────────────────────────────────────────

_SET_UPDATED_AT_FN = """
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;
"""

_CHECK_MERGE_STATE_FN = """
CREATE OR REPLACE FUNCTION check_merge_same_state()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    c_state VARCHAR(2);
    m_state VARCHAR(2);
BEGIN
    SELECT source_state INTO c_state FROM companies WHERE id = NEW.canonical_id;
    SELECT source_state INTO m_state FROM companies WHERE id = NEW.merged_id;
    IF c_state IS DISTINCT FROM m_state THEN
        RAISE EXCEPTION
            'Merge cross-state proibido: canonical=%  merged=%',
            c_state, m_state;
    END IF;
    RETURN NEW;
END;
$$;
"""

# Tabelas que têm coluna updated_at e recebem trigger
_UPDATED_AT_TABLES = [
    "companies",
    "company_documents",
    "search_runs",
    "jobs",
    "inventory_targets",
    "replenishment_jobs",
    "app_settings",
    "user_profiles",
]

# Todas as tabelas do schema public (RLS)
_ALL_TABLES = [
    "user_profiles",
    "companies",
    "company_addresses",
    "company_field_evidence",
    "company_documents",
    "website_snapshots",
    "search_runs",
    "search_run_candidates",
    "jobs",
    "provider_attempts",
    "company_events",
    "company_merges",
    "inventory_targets",
    "replenishment_jobs",
    "app_settings",
]


def upgrade() -> None:
    # ── 0. Extension ──────────────────────────────────────────────────────────
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    # ── 1. Tabelas (ordem FK) ─────────────────────────────────────────────────
    op.create_table(
        "companies",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("source_state", sa.VARCHAR(length=2), nullable=False),
        sa.Column("commercial_name", sa.Text(), nullable=True),
        sa.Column("legal_name", sa.Text(), nullable=True),
        sa.Column("entity_number", sa.Text(), nullable=True),
        sa.Column("legacy_ein", sa.Text(), nullable=True),
        sa.Column("entity_type", sa.Text(), nullable=True),
        sa.Column("formation_jurisdiction", sa.Text(), nullable=True),
        sa.Column("entity_status", sa.Text(), nullable=True),
        sa.Column("niche", sa.Text(), nullable=True),
        sa.Column("domain", sa.Text(), nullable=True),
        sa.Column("website_url", sa.Text(), nullable=True),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("phone_e164", sa.Text(), nullable=True),
        sa.Column("place_id", sa.Text(), nullable=True),
        sa.Column(
            "dossier_status",
            sa.Enum("DISCOVERED", "MATCHED", "DOSSIER_BUILDING", "READY", "PARTIAL", name="dossier_status"),
            server_default="DISCOVERED",
            nullable=False,
        ),
        sa.Column(
            "usage_status",
            sa.Enum("AVAILABLE", "IN_USE", "FINALIZED", name="usage_status"),
            server_default="AVAILABLE",
            nullable=False,
        ),
        sa.Column(
            "verification_status",
            sa.Enum("NOT_STARTED", "IN_PROGRESS", "PASSED", "FAILED", name="verification_status"),
            server_default="NOT_STARTED",
            nullable=False,
        ),
        sa.Column(
            "partial_reasons",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("verification_failure_reason", sa.Text(), nullable=True),
        sa.Column("finalization_reason", sa.Text(), nullable=True),
        sa.Column("match_score", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("readiness_policy", sa.Text(), nullable=False),
        sa.Column("readiness_locked", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("legacy_read_only", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("found_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("matched_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("dossier_completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("marked_in_use_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("verification_started_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("verification_finished_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("finalized_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("source_state ~ '^[A-Z]{2}$'", name="ck_companies_source_state"),
        sa.CheckConstraint(
            "match_score IS NULL OR (match_score >= 0 AND match_score <= 100)",
            name="ck_companies_match_score",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(partial_reasons) = 'array'",
            name="ck_companies_partial_reasons_array",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_companies_availability",
        "companies",
        ["source_state", "dossier_status", "usage_status", "verification_status"],
        unique=False,
    )
    op.create_index(
        "ix_companies_available_stock",
        "companies",
        ["niche", "created_at", "id"],
        unique=False,
        postgresql_where=sa.text(
            "source_state = 'CA'"
            " AND dossier_status = 'READY'"
            " AND usage_status = 'AVAILABLE'"
            " AND verification_status = 'NOT_STARTED'"
        ),
    )

    op.create_table(
        "inventory_targets",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("source_state", sa.VARCHAR(length=2), nullable=False),
        sa.Column(
            "scope",
            sa.Enum("TOTAL", "NICHE", name="inventory_scope"),
            nullable=False,
        ),
        sa.Column("niche", sa.Text(), nullable=True),
        sa.Column("target_stock", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("low_threshold", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("batch_size", sa.Integer(), server_default=sa.text("10"), nullable=False),
        sa.Column("max_active_jobs", sa.Integer(), server_default=sa.text("5"), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "(scope = 'TOTAL' AND niche IS NULL) OR (scope = 'NICHE' AND niche IS NOT NULL)",
            name="ck_inventory_targets_scope_niche",
        ),
        sa.CheckConstraint("target_stock >= 0", name="ck_inventory_targets_target_stock"),
        sa.CheckConstraint("low_threshold >= 0", name="ck_inventory_targets_low_threshold"),
        sa.CheckConstraint("low_threshold <= target_stock", name="ck_inventory_targets_threshold_lte_target"),
        sa.CheckConstraint("batch_size > 0", name="ck_inventory_targets_batch_size"),
        sa.CheckConstraint("max_active_jobs > 0", name="ck_inventory_targets_max_active_jobs"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_inventory_targets_state_total",
        "inventory_targets",
        ["source_state"],
        unique=True,
        postgresql_where=sa.text("scope = 'TOTAL'"),
    )
    op.create_index(
        "uq_inventory_targets_state_niche",
        "inventory_targets",
        ["source_state", "niche"],
        unique=True,
        postgresql_where=sa.text("scope = 'NICHE'"),
    )

    op.create_table(
        "user_profiles",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("role", sa.Text(), server_default="VIEWER", nullable=False),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("role IN ('ADMIN','OPERATOR','VIEWER')", name="ck_user_profiles_role"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "app_settings",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("value_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.ForeignKeyConstraint(["updated_by"], ["user_profiles.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("key"),
    )

    op.create_table(
        "company_addresses",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column(
            "address_type",
            sa.Enum("PRINCIPAL", "MAILING", "AGENT", "CALIFORNIA_OFFICE", name="address_type"),
            nullable=False,
        ),
        sa.Column("street_line1", sa.Text(), nullable=True),
        sa.Column("suite", sa.Text(), nullable=True),
        sa.Column("city", sa.Text(), nullable=True),
        sa.Column("state", sa.VARCHAR(length=2), nullable=True),
        sa.Column("zip_code", sa.Text(), nullable=True),
        sa.Column("country", sa.VARCHAR(length=2), server_default="US", nullable=False),
        sa.Column("normalized", sa.Text(), nullable=True),
        sa.Column("address_hash", sa.VARCHAR(length=64), nullable=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("collected_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("country ~ '^[A-Z]{2}$'", name="ck_company_addresses_country"),
        sa.CheckConstraint("state IS NULL OR state ~ '^[A-Z]{2}$'", name="ck_company_addresses_state"),
        sa.CheckConstraint(
            "address_hash IS NULL OR address_hash ~ '^[0-9a-f]{64}$'",
            name="ck_company_addresses_hash",
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_company_addresses_company_type_date",
        "company_addresses",
        ["company_id", "address_type", "collected_at"],
        unique=False,
    )
    op.create_index(
        "ix_company_addresses_hash",
        "company_addresses",
        ["address_hash"],
        unique=False,
        postgresql_where=sa.text("address_hash IS NOT NULL"),
    )

    op.create_table(
        "company_field_evidence",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("field_name", sa.Text(), nullable=False),
        sa.Column("field_value", sa.Text(), nullable=False),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("evidence_category", sa.Text(), nullable=False),
        sa.Column("evidence_direction", sa.Text(), server_default="SUPPORTS", nullable=False),
        sa.Column("confidence", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("collected_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 100)",
            name="ck_company_field_evidence_confidence",
        ),
        sa.CheckConstraint(
            "evidence_direction IN ('SUPPORTS','CONTRADICTS','NEUTRAL')",
            name="ck_company_field_evidence_direction",
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_company_field_evidence_company_field",
        "company_field_evidence",
        ["company_id", "field_name"],
        unique=False,
    )
    op.create_index(
        "ix_company_field_evidence_company_category",
        "company_field_evidence",
        ["company_id", "evidence_category"],
        unique=False,
    )

    op.create_table(
        "company_documents",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("document_type", sa.Text(), nullable=False),
        sa.Column("subtype", sa.Text(), nullable=True),
        sa.Column("file_number", sa.Text(), nullable=True),
        sa.Column("filed_date", sa.Date(), nullable=True),
        sa.Column("storage_key", sa.Text(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("original_filename", sa.Text(), nullable=True),
        sa.Column("mime_type", sa.Text(), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("sha256", sa.VARCHAR(length=64), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("entity_number_extracted", sa.Text(), nullable=True),
        sa.Column("legal_name_extracted", sa.Text(), nullable=True),
        sa.Column("validation_status", sa.Text(), server_default="PENDING", nullable=False),
        sa.Column(
            "validation_errors",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "validation_status IN ('PENDING','VALID','INVALID','MISMATCH')",
            name="ck_company_documents_validation_status",
        ),
        sa.CheckConstraint("size_bytes IS NULL OR size_bytes >= 0", name="ck_company_documents_size_bytes"),
        sa.CheckConstraint(
            "sha256 IS NULL OR sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_company_documents_sha256",
        ),
        sa.CheckConstraint("page_count IS NULL OR page_count > 0", name="ck_company_documents_page_count"),
        sa.CheckConstraint(
            "jsonb_typeof(validation_errors) = 'array'",
            name="ck_company_documents_validation_errors_array",
        ),
        sa.CheckConstraint(
            "storage_key IS NOT NULL OR source_url IS NOT NULL",
            name="ck_company_documents_storage_or_url",
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_company_documents_company_id", "company_documents", ["company_id"], unique=False)
    op.create_index(
        "uq_company_documents_sha256",
        "company_documents",
        ["company_id", "sha256"],
        unique=True,
        postgresql_where=sa.text("sha256 IS NOT NULL"),
    )
    op.create_index(
        "uq_company_documents_file_number",
        "company_documents",
        ["company_id", "provider", "file_number"],
        unique=True,
        postgresql_where=sa.text("file_number IS NOT NULL"),
    )

    op.create_table(
        "website_snapshots",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("storage_key_html", sa.Text(), nullable=False),
        sa.Column("storage_key_text", sa.Text(), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("content_type", sa.Text(), nullable=True),
        sa.Column("sha256", sa.VARCHAR(length=64), nullable=False),
        sa.Column("collected_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column(
            "extraction_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="ck_website_snapshots_sha256"),
        sa.CheckConstraint(
            "http_status IS NULL OR (http_status >= 100 AND http_status <= 599)",
            name="ck_website_snapshots_http_status",
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "url", "sha256", name="uq_website_snapshots_company_url_sha256"),
    )
    op.create_index(
        "ix_website_snapshots_company_collected",
        "website_snapshots",
        ["company_id", "collected_at"],
        unique=False,
    )

    op.create_table(
        "search_runs",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("query_params", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("status", sa.Text(), server_default="PENDING", nullable=False),
        # nullable=False: contador, default=0
        sa.Column("candidate_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("requested_by", sa.UUID(), nullable=True),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("finished_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "status IN ('PENDING','RUNNING','SUCCEEDED','FAILED','CANCELLED')",
            name="ck_search_runs_status",
        ),
        sa.CheckConstraint("candidate_count >= 0", name="ck_search_runs_candidate_count"),
        sa.ForeignKeyConstraint(["requested_by"], ["user_profiles.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_search_runs_status_created", "search_runs", ["status", "created_at"], unique=False)

    op.create_table(
        "jobs",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=True),
        sa.Column("job_type", sa.Text(), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("status", sa.Text(), server_default="PENDING", nullable=False),
        sa.Column("priority", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default=sa.text("3"), nullable=False),
        sa.Column("next_run_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("locked_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_heartbeat_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("finished_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("locked_by", sa.Text(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("company_id", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "status IN ('PENDING','RUNNING','RETRY','SUCCEEDED','DEAD_LETTER','CANCELLED')",
            name="ck_jobs_status",
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_jobs_attempts_gte_0"),
        sa.CheckConstraint("max_attempts > 0", name="ck_jobs_max_attempts_gt_0"),
        sa.CheckConstraint("attempts <= max_attempts", name="ck_jobs_attempts_lte_max"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_jobs_type_idempotency",
        "jobs",
        ["job_type", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    op.create_index(
        "ix_jobs_pending_queue",
        "jobs",
        ["priority", "next_run_at", "created_at"],
        unique=False,
        postgresql_where=sa.text("status IN ('PENDING','RETRY')"),
    )
    op.create_index(
        "ix_jobs_running_lease",
        "jobs",
        ["lease_expires_at"],
        unique=False,
        postgresql_where=sa.text("status = 'RUNNING'"),
    )
    op.create_index(
        "ix_jobs_company_id",
        "jobs",
        ["company_id"],
        unique=False,
        postgresql_where=sa.text("company_id IS NOT NULL"),
    )

    op.create_table(
        "company_events",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("old_value", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("new_value", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("actor_type", sa.Text(), nullable=False),
        sa.Column("actor_id", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("actor_type IN ('USER','WORKER','SYSTEM')", name="ck_company_events_actor_type"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_company_events_company_created",
        "company_events",
        ["company_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "company_merges",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("canonical_id", sa.UUID(), nullable=False),
        sa.Column("merged_id", sa.UUID(), nullable=False),
        sa.Column("merge_reason", sa.Text(), nullable=False),
        sa.Column("merge_rule", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=True),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("canonical_id <> merged_id", name="ck_company_merges_no_self_merge"),
        sa.ForeignKeyConstraint(["canonical_id"], ["companies.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["merged_id"], ["companies.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("merged_id", name="uq_company_merges_merged_id"),
    )
    op.create_index("ix_company_merges_canonical_id", "company_merges", ["canonical_id"], unique=False)

    op.create_table(
        "provider_attempts",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=True),
        sa.Column("job_id", sa.UUID(), nullable=True),
        sa.Column("search_run_id", sa.UUID(), nullable=True),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("request_params", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("response_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("cost_units", sa.Numeric(), nullable=True),
        sa.Column("cost_amount_usd", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "status IN ('SUCCESS','FAILURE','SKIPPED','RATE_LIMITED')",
            name="ck_provider_attempts_status",
        ),
        sa.CheckConstraint(
            "company_id IS NOT NULL OR search_run_id IS NOT NULL OR job_id IS NOT NULL",
            name="ck_provider_attempts_at_least_one_ref",
        ),
        sa.CheckConstraint("cost_units IS NULL OR cost_units >= 0", name="ck_provider_attempts_cost_units"),
        sa.CheckConstraint("duration_ms IS NULL OR duration_ms >= 0", name="ck_provider_attempts_duration_ms"),
        sa.CheckConstraint(
            "cost_amount_usd IS NULL OR cost_amount_usd >= 0",
            name="ck_provider_attempts_cost_usd",
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["search_run_id"], ["search_runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_provider_attempts_company_id", "provider_attempts", ["company_id"], unique=False)
    op.create_index(
        "ix_provider_attempts_job_id",
        "provider_attempts",
        ["job_id"],
        unique=False,
        postgresql_where=sa.text("job_id IS NOT NULL"),
    )
    op.create_index(
        "ix_provider_attempts_search_run_id",
        "provider_attempts",
        ["search_run_id"],
        unique=False,
        postgresql_where=sa.text("search_run_id IS NOT NULL"),
    )
    op.create_index(
        "ix_provider_attempts_provider_created",
        "provider_attempts",
        ["provider", "created_at"],
        unique=False,
    )

    op.create_table(
        "replenishment_jobs",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("inventory_target_id", sa.UUID(), nullable=False),
        sa.Column("job_id", sa.UUID(), nullable=True),
        sa.Column("status", sa.Text(), server_default="PENDING", nullable=False),
        sa.Column("target_count", sa.Integer(), nullable=False),
        # nullable=False: contadores com default 0
        sa.Column("found_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("ready_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("finished_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "status IN ('PENDING','RUNNING','SUCCEEDED','FAILED','CANCELLED')",
            name="ck_replenishment_jobs_status",
        ),
        sa.CheckConstraint("target_count >= 0", name="ck_replenishment_jobs_target_count"),
        sa.CheckConstraint("found_count >= 0", name="ck_replenishment_jobs_found_count"),
        sa.CheckConstraint("ready_count >= 0", name="ck_replenishment_jobs_ready_count"),
        sa.CheckConstraint("ready_count <= found_count", name="ck_replenishment_jobs_ready_lte_found"),
        sa.ForeignKeyConstraint(["inventory_target_id"], ["inventory_targets.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id"),
    )
    op.create_index(
        "ix_replenishment_jobs_target_status_created",
        "replenishment_jobs",
        ["inventory_target_id", "status", "created_at"],
        unique=False,
    )

    op.create_table(
        "search_run_candidates",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("search_run_id", sa.UUID(), nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=True),
        sa.Column("raw_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("status", sa.Text(), server_default="PENDING", nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("processed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "status IN ('PENDING','MATCHED','REJECTED','DUPLICATE','FAILED')",
            name="ck_search_run_candidates_status",
        ),
        sa.ForeignKeyConstraint(["search_run_id"], ["search_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_search_run_candidates_run", "search_run_candidates", ["search_run_id"], unique=False)
    op.create_index(
        "ix_search_run_candidates_company",
        "search_run_candidates",
        ["company_id"],
        unique=False,
        postgresql_where=sa.text("company_id IS NOT NULL"),
    )
    op.create_index(
        "uq_search_run_candidates_run_company",
        "search_run_candidates",
        ["search_run_id", "company_id"],
        unique=True,
        postgresql_where=sa.text("company_id IS NOT NULL"),
    )

    # ── 2. FK cross-schema user_profiles → auth.users ─────────────────────────
    op.execute(
        """
        ALTER TABLE user_profiles
            ADD CONSTRAINT fk_user_profiles_auth_users
            FOREIGN KEY (id) REFERENCES auth.users(id) ON DELETE CASCADE
        """
    )

    # ── 3. Índice funcional: dedup por entity_number normalizado ───────────────
    op.execute(
        """
        CREATE UNIQUE INDEX uq_companies_state_entity_number
            ON companies (source_state, upper(btrim(entity_number)))
            WHERE entity_number IS NOT NULL AND btrim(entity_number) <> ''
        """
    )

    # ── 4. Função set_updated_at() + triggers (8 tabelas mutáveis) ─────────────
    op.execute(_SET_UPDATED_AT_FN)
    for table in _UPDATED_AT_TABLES:
        op.execute(
            f"""
            CREATE TRIGGER trg_{table}_updated_at
                BEFORE UPDATE ON {table}
                FOR EACH ROW EXECUTE FUNCTION set_updated_at()
            """
        )

    # ── 5. Trigger: impede merge entre source_states diferentes ───────────────
    op.execute(_CHECK_MERGE_STATE_FN)
    op.execute(
        """
        CREATE TRIGGER trg_company_merges_check_state
            BEFORE INSERT ON company_merges
            FOR EACH ROW EXECUTE FUNCTION check_merge_same_state()
        """
    )

    # ── 6. RLS em todas as 15 tabelas (bloqueia anon/authenticated) ────────────
    for table in _ALL_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    # ── 6. Desabilitar RLS (ordem inversa) ───────────────────────────────────
    for table in reversed(_ALL_TABLES):
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    # ── 5. Remover trigger e função de cross-state merge ─────────────────────
    op.execute("DROP TRIGGER IF EXISTS trg_company_merges_check_state ON company_merges")
    op.execute("DROP FUNCTION IF EXISTS check_merge_same_state()")

    # ── 4. Remover triggers e função set_updated_at ───────────────────────────
    for table in reversed(_UPDATED_AT_TABLES):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_updated_at ON {table}")
    op.execute("DROP FUNCTION IF EXISTS set_updated_at()")

    # ── 3. Remover índice funcional ──────────────────────────────────────────
    op.execute("DROP INDEX IF EXISTS uq_companies_state_entity_number")

    # ── 2. Remover FK cross-schema (a tabela ainda existe aqui) ──────────────
    op.execute(
        "ALTER TABLE user_profiles DROP CONSTRAINT IF EXISTS fk_user_profiles_auth_users"
    )

    # ── 1. Remover tabelas e índices Alembic (ordem reversa de FK) ────────────
    op.drop_index(
        "uq_search_run_candidates_run_company",
        table_name="search_run_candidates",
        postgresql_where=sa.text("company_id IS NOT NULL"),
    )
    op.drop_index(
        "ix_search_run_candidates_company",
        table_name="search_run_candidates",
        postgresql_where=sa.text("company_id IS NOT NULL"),
    )
    op.drop_index("ix_search_run_candidates_run", table_name="search_run_candidates")
    op.drop_table("search_run_candidates")

    op.drop_index("ix_replenishment_jobs_target_status_created", table_name="replenishment_jobs")
    op.drop_table("replenishment_jobs")

    op.drop_index(
        "ix_provider_attempts_search_run_id",
        table_name="provider_attempts",
        postgresql_where=sa.text("search_run_id IS NOT NULL"),
    )
    op.drop_index(
        "ix_provider_attempts_job_id",
        table_name="provider_attempts",
        postgresql_where=sa.text("job_id IS NOT NULL"),
    )
    op.drop_index("ix_provider_attempts_provider_created", table_name="provider_attempts")
    op.drop_index("ix_provider_attempts_company_id", table_name="provider_attempts")
    op.drop_table("provider_attempts")

    op.drop_index("ix_company_merges_canonical_id", table_name="company_merges")
    op.drop_table("company_merges")

    op.drop_index("ix_company_events_company_created", table_name="company_events")
    op.drop_table("company_events")

    op.drop_index(
        "uq_company_documents_file_number",
        table_name="company_documents",
        postgresql_where=sa.text("file_number IS NOT NULL"),
    )
    op.drop_index(
        "uq_company_documents_sha256",
        table_name="company_documents",
        postgresql_where=sa.text("sha256 IS NOT NULL"),
    )
    op.drop_index("ix_company_documents_company_id", table_name="company_documents")
    op.drop_table("company_documents")

    op.drop_index("ix_company_field_evidence_company_category", table_name="company_field_evidence")
    op.drop_index("ix_company_field_evidence_company_field", table_name="company_field_evidence")
    op.drop_table("company_field_evidence")

    op.drop_index(
        "ix_company_addresses_hash",
        table_name="company_addresses",
        postgresql_where=sa.text("address_hash IS NOT NULL"),
    )
    op.drop_index("ix_company_addresses_company_type_date", table_name="company_addresses")
    op.drop_table("company_addresses")

    op.drop_index("ix_website_snapshots_company_collected", table_name="website_snapshots")
    op.drop_table("website_snapshots")

    op.drop_index("ix_search_runs_status_created", table_name="search_runs")
    op.drop_table("search_runs")

    op.drop_index(
        "uq_jobs_type_idempotency",
        table_name="jobs",
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    op.drop_index(
        "ix_jobs_running_lease",
        table_name="jobs",
        postgresql_where=sa.text("status = 'RUNNING'"),
    )
    op.drop_index(
        "ix_jobs_pending_queue",
        table_name="jobs",
        postgresql_where=sa.text("status IN ('PENDING','RETRY')"),
    )
    op.drop_index(
        "ix_jobs_company_id",
        table_name="jobs",
        postgresql_where=sa.text("company_id IS NOT NULL"),
    )
    op.drop_table("jobs")

    op.drop_table("app_settings")
    op.drop_table("user_profiles")

    op.drop_index(
        "uq_inventory_targets_state_niche",
        table_name="inventory_targets",
        postgresql_where=sa.text("scope = 'NICHE'"),
    )
    op.drop_index(
        "uq_inventory_targets_state_total",
        table_name="inventory_targets",
        postgresql_where=sa.text("scope = 'TOTAL'"),
    )
    op.drop_table("inventory_targets")

    op.drop_index(
        "ix_companies_available_stock",
        table_name="companies",
        postgresql_where=sa.text(
            "source_state = 'CA'"
            " AND dossier_status = 'READY'"
            " AND usage_status = 'AVAILABLE'"
            " AND verification_status = 'NOT_STARTED'"
        ),
    )
    op.drop_index("ix_companies_availability", table_name="companies")
    op.drop_table("companies")

    # ── 0. Remover ENUMs (após tabelas) e extension ────────────────────────────
    op.execute("DROP TYPE IF EXISTS address_type")
    op.execute("DROP TYPE IF EXISTS inventory_scope")
    op.execute("DROP TYPE IF EXISTS verification_status")
    op.execute("DROP TYPE IF EXISTS usage_status")
    op.execute("DROP TYPE IF EXISTS dossier_status")
    op.execute("DROP EXTENSION IF EXISTS pgcrypto")
