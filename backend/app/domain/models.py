"""SQLAlchemy ORM models — DayOne Verify.

Trigger function set_updated_at(), triggers, RLS, CREATE EXTENSION,
a FK cross-schema para auth.users e o índice funcional upper(btrim(entity_number))
são adicionados como raw SQL na migration (não representáveis via autogenerate).
"""
import enum

import sqlalchemy as sa
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    VARCHAR,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.sql import func, text


class Base(DeclarativeBase):
    pass


# ─── Python enums (camada de aplicação) ───────────────────────────────────────


class DossierStatus(str, enum.Enum):
    DISCOVERED = "DISCOVERED"
    MATCHED = "MATCHED"
    DOSSIER_BUILDING = "DOSSIER_BUILDING"
    READY = "READY"
    PARTIAL = "PARTIAL"
    READY_NO_PDF = "READY_NO_PDF"


class UsageStatus(str, enum.Enum):
    AVAILABLE = "AVAILABLE"
    IN_USE = "IN_USE"
    FINALIZED = "FINALIZED"


class VerificationStatus(str, enum.Enum):
    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    PASSED = "PASSED"
    FAILED = "FAILED"


class AddressType(str, enum.Enum):
    PRINCIPAL = "PRINCIPAL"
    MAILING = "MAILING"
    AGENT = "AGENT"
    CALIFORNIA_OFFICE = "CALIFORNIA_OFFICE"


class InventoryScope(str, enum.Enum):
    TOTAL = "TOTAL"
    NICHE = "NICHE"


# ─── Tables ───────────────────────────────────────────────────────────────────


class UserProfile(Base):
    """Espelha auth.users(id). FK cross-schema adicionada manualmente na migration."""

    __tablename__ = "user_profiles"

    id = Column(UUID(as_uuid=True), primary_key=True)
    role = Column(Text, nullable=False, server_default="VIEWER")
    display_name = Column(Text)
    created_at = Column(sa.TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(sa.TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("role IN ('ADMIN','OPERATOR','VIEWER')", name="ck_user_profiles_role"),
    )


class Company(Base):
    __tablename__ = "companies"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    source_state = Column(VARCHAR(2), nullable=False)
    commercial_name = Column(Text)
    legal_name = Column(Text)
    entity_number = Column(Text)
    legacy_ein = Column(Text)
    entity_type = Column(Text)
    formation_jurisdiction = Column(Text)
    entity_status = Column(Text)
    niche = Column(Text)
    domain = Column(Text)
    website_url = Column(Text)
    email = Column(Text)
    phone_e164 = Column(Text)
    place_id = Column(Text)
    owner_first_name = Column(Text)
    owner_last_name = Column(Text)
    owner_source = Column(Text)
    dossier_status = Column(
        sa.Enum("DISCOVERED", "MATCHED", "DOSSIER_BUILDING", "READY", "PARTIAL", "READY_NO_PDF", name="dossier_status"),
        nullable=False,
        server_default="DISCOVERED",
    )
    usage_status = Column(
        sa.Enum("AVAILABLE", "IN_USE", "FINALIZED", name="usage_status"),
        nullable=False,
        server_default="AVAILABLE",
    )
    verification_status = Column(
        sa.Enum("NOT_STARTED", "IN_PROGRESS", "PASSED", "FAILED", name="verification_status"),
        nullable=False,
        server_default="NOT_STARTED",
    )
    partial_reasons = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    verification_failure_reason = Column(Text)
    finalization_reason = Column(Text)
    match_score = Column(Numeric(5, 2))
    readiness_policy = Column(Text, nullable=False)
    readiness_locked = Column(Boolean, nullable=False, server_default=text("false"))
    legacy_read_only = Column(Boolean, nullable=False, server_default=text("false"))
    found_at = Column(sa.TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    matched_at = Column(sa.TIMESTAMP(timezone=True))
    dossier_completed_at = Column(sa.TIMESTAMP(timezone=True))
    marked_in_use_at = Column(sa.TIMESTAMP(timezone=True))
    verification_started_at = Column(sa.TIMESTAMP(timezone=True))
    verification_finished_at = Column(sa.TIMESTAMP(timezone=True))
    finalized_at = Column(sa.TIMESTAMP(timezone=True))
    created_at = Column(sa.TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(sa.TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("source_state ~ '^[A-Z]{2}$'", name="ck_companies_source_state"),
        CheckConstraint(
            "match_score IS NULL OR (match_score >= 0 AND match_score <= 100)",
            name="ck_companies_match_score",
        ),
        CheckConstraint(
            "jsonb_typeof(partial_reasons) = 'array'",
            name="ck_companies_partial_reasons_array",
        ),
        # Functional unique index added as raw SQL in migration (autogenerate não suporta)
        # uq_companies_state_entity_number: UNIQUE(source_state, upper(btrim(entity_number)))
        # WHERE entity_number IS NOT NULL AND btrim(entity_number) <> ''
        Index(
            "ix_companies_available_stock",
            "niche", "created_at", "id",
            postgresql_where=text(
                "source_state = 'CA'"
                " AND dossier_status = 'READY'"
                " AND usage_status = 'AVAILABLE'"
                " AND verification_status = 'NOT_STARTED'"
            ),
        ),
        Index(
            "ix_companies_availability",
            "source_state", "dossier_status", "usage_status", "verification_status",
        ),
    )


class CompanyAddress(Base):
    __tablename__ = "company_addresses"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    company_id = Column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    address_type = Column(
        sa.Enum("PRINCIPAL", "MAILING", "AGENT", "CALIFORNIA_OFFICE", name="address_type"),
        nullable=False,
    )
    street_line1 = Column(Text)
    suite = Column(Text)
    city = Column(Text)
    state = Column(VARCHAR(2))
    zip_code = Column(Text)
    country = Column(VARCHAR(2), nullable=False, server_default="US")
    normalized = Column(Text)
    address_hash = Column(VARCHAR(64))
    source = Column(Text, nullable=False)
    collected_at = Column(sa.TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    created_at = Column(sa.TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("country ~ '^[A-Z]{2}$'", name="ck_company_addresses_country"),
        CheckConstraint(
            "state IS NULL OR state ~ '^[A-Z]{2}$'",
            name="ck_company_addresses_state",
        ),
        CheckConstraint(
            "address_hash IS NULL OR address_hash ~ '^[0-9a-f]{64}$'",
            name="ck_company_addresses_hash",
        ),
        Index("ix_company_addresses_company_type_date", "company_id", "address_type", "collected_at"),
        Index(
            "ix_company_addresses_hash",
            "address_hash",
            postgresql_where=text("address_hash IS NOT NULL"),
        ),
    )


class CompanyFieldEvidence(Base):
    __tablename__ = "company_field_evidence"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    company_id = Column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    field_name = Column(Text, nullable=False)
    field_value = Column(Text, nullable=False)
    source_type = Column(Text, nullable=False)
    source_url = Column(Text)
    evidence_category = Column(Text, nullable=False)
    evidence_direction = Column(Text, nullable=False, server_default="SUPPORTS")
    confidence = Column(Numeric(5, 2))
    collected_at = Column(sa.TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    metadata_json = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at = Column(sa.TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 100)",
            name="ck_company_field_evidence_confidence",
        ),
        CheckConstraint(
            "evidence_direction IN ('SUPPORTS','CONTRADICTS','NEUTRAL')",
            name="ck_company_field_evidence_direction",
        ),
        Index("ix_company_field_evidence_company_field", "company_id", "field_name"),
        Index("ix_company_field_evidence_company_category", "company_id", "evidence_category"),
    )


class CompanyDocument(Base):
    __tablename__ = "company_documents"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    company_id = Column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    provider = Column(Text, nullable=False)
    document_type = Column(Text, nullable=False)
    subtype = Column(Text)
    file_number = Column(Text)
    filed_date = Column(Date)
    storage_key = Column(Text)
    source_url = Column(Text)
    original_filename = Column(Text)
    mime_type = Column(Text)
    size_bytes = Column(BigInteger)
    sha256 = Column(VARCHAR(64))
    page_count = Column(Integer)
    entity_number_extracted = Column(Text)
    legal_name_extracted = Column(Text)
    validation_status = Column(Text, nullable=False, server_default="PENDING")
    validation_errors = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    created_at = Column(sa.TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(sa.TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "validation_status IN ('PENDING','VALID','INVALID','MISMATCH')",
            name="ck_company_documents_validation_status",
        ),
        CheckConstraint(
            "size_bytes IS NULL OR size_bytes >= 0",
            name="ck_company_documents_size_bytes",
        ),
        CheckConstraint(
            "sha256 IS NULL OR sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_company_documents_sha256",
        ),
        CheckConstraint(
            "page_count IS NULL OR page_count > 0",
            name="ck_company_documents_page_count",
        ),
        CheckConstraint(
            "jsonb_typeof(validation_errors) = 'array'",
            name="ck_company_documents_validation_errors_array",
        ),
        CheckConstraint(
            "storage_key IS NOT NULL OR source_url IS NOT NULL",
            name="ck_company_documents_storage_or_url",
        ),
        Index(
            "uq_company_documents_sha256",
            "company_id", "sha256",
            unique=True,
            postgresql_where=text("sha256 IS NOT NULL"),
        ),
        Index(
            "uq_company_documents_file_number",
            "company_id", "provider", "file_number",
            unique=True,
            postgresql_where=text("file_number IS NOT NULL"),
        ),
        Index("ix_company_documents_company_id", "company_id"),
    )


class WebsiteSnapshot(Base):
    __tablename__ = "website_snapshots"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    company_id = Column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    url = Column(Text, nullable=False)
    storage_key_html = Column(Text, nullable=False)
    storage_key_text = Column(Text)
    http_status = Column(Integer)
    content_type = Column(Text)
    sha256 = Column(VARCHAR(64), nullable=False)
    collected_at = Column(sa.TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    extraction_metadata = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at = Column(sa.TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_website_snapshots_sha256",
        ),
        CheckConstraint(
            "http_status IS NULL OR (http_status >= 100 AND http_status <= 599)",
            name="ck_website_snapshots_http_status",
        ),
        UniqueConstraint("company_id", "url", "sha256", name="uq_website_snapshots_company_url_sha256"),
        Index("ix_website_snapshots_company_collected", "company_id", "collected_at"),
    )


class SearchRun(Base):
    __tablename__ = "search_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    provider = Column(Text, nullable=False)
    query_params = Column(JSONB)
    status = Column(Text, nullable=False, server_default="PENDING")
    candidate_count = Column(Integer, server_default=text("0"))
    error = Column(Text)
    requested_by = Column(
        UUID(as_uuid=True),
        ForeignKey("user_profiles.id", ondelete="SET NULL"),
    )
    started_at = Column(sa.TIMESTAMP(timezone=True))
    finished_at = Column(sa.TIMESTAMP(timezone=True))
    created_at = Column(sa.TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(sa.TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING','RUNNING','SUCCEEDED','FAILED','CANCELLED')",
            name="ck_search_runs_status",
        ),
        CheckConstraint("candidate_count >= 0", name="ck_search_runs_candidate_count"),
        Index("ix_search_runs_status_created", "status", "created_at"),
    )


class SearchRunCandidate(Base):
    __tablename__ = "search_run_candidates"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    search_run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("search_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    company_id = Column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="SET NULL"),
    )
    raw_data = Column(JSONB)
    status = Column(Text, nullable=False, server_default="PENDING")
    reason = Column(Text)
    processed_at = Column(sa.TIMESTAMP(timezone=True))
    created_at = Column(sa.TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING','MATCHED','REJECTED','DUPLICATE','FAILED')",
            name="ck_search_run_candidates_status",
        ),
        Index("ix_search_run_candidates_run", "search_run_id"),
        Index(
            "ix_search_run_candidates_company",
            "company_id",
            postgresql_where=text("company_id IS NOT NULL"),
        ),
        Index(
            "uq_search_run_candidates_run_company",
            "search_run_id", "company_id",
            unique=True,
            postgresql_where=text("company_id IS NOT NULL"),
        ),
    )


class Job(Base):
    __tablename__ = "jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    idempotency_key = Column(Text)
    job_type = Column(Text, nullable=False)
    payload = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    status = Column(Text, nullable=False, server_default="PENDING")
    priority = Column(Integer, nullable=False, server_default=text("0"))
    attempts = Column(Integer, nullable=False, server_default=text("0"))
    max_attempts = Column(Integer, nullable=False, server_default=text("3"))
    next_run_at = Column(sa.TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    locked_at = Column(sa.TIMESTAMP(timezone=True))
    lease_expires_at = Column(sa.TIMESTAMP(timezone=True))
    last_heartbeat_at = Column(sa.TIMESTAMP(timezone=True))
    started_at = Column(sa.TIMESTAMP(timezone=True))
    finished_at = Column(sa.TIMESTAMP(timezone=True))
    locked_by = Column(Text)
    last_error = Column(Text)
    result = Column(JSONB)
    company_id = Column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="SET NULL"),
    )
    created_at = Column(sa.TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(sa.TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING','RUNNING','RETRY','SUCCEEDED','DEAD_LETTER','CANCELLED')",
            name="ck_jobs_status",
        ),
        CheckConstraint("attempts >= 0", name="ck_jobs_attempts_gte_0"),
        CheckConstraint("max_attempts > 0", name="ck_jobs_max_attempts_gt_0"),
        CheckConstraint("attempts <= max_attempts", name="ck_jobs_attempts_lte_max"),
        # Composite idempotency key (job_type + idempotency_key when not null)
        Index(
            "uq_jobs_type_idempotency",
            "job_type", "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        # Queue polling index
        Index(
            "ix_jobs_pending_queue",
            "priority", "next_run_at", "created_at",
            postgresql_where=text("status IN ('PENDING','RETRY')"),
        ),
        # Expired lease index
        Index(
            "ix_jobs_running_lease",
            "lease_expires_at",
            postgresql_where=text("status = 'RUNNING'"),
        ),
        # company_id lookup
        Index(
            "ix_jobs_company_id",
            "company_id",
            postgresql_where=text("company_id IS NOT NULL"),
        ),
    )


class ProviderAttempt(Base):
    __tablename__ = "provider_attempts"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    company_id = Column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="SET NULL"),
    )
    job_id = Column(
        UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="SET NULL"),
    )
    search_run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("search_runs.id", ondelete="SET NULL"),
    )
    provider = Column(Text, nullable=False)
    operation = Column(Text, nullable=False)
    status = Column(Text, nullable=False)
    request_params = Column(JSONB)
    response_summary = Column(JSONB)
    error = Column(Text)
    duration_ms = Column(Integer)
    cost_units = Column(Numeric)
    cost_amount_usd = Column(Numeric(18, 6))
    created_at = Column(sa.TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "status IN ('SUCCESS','FAILURE','SKIPPED','RATE_LIMITED')",
            name="ck_provider_attempts_status",
        ),
        CheckConstraint(
            "company_id IS NOT NULL OR search_run_id IS NOT NULL OR job_id IS NOT NULL",
            name="ck_provider_attempts_at_least_one_ref",
        ),
        CheckConstraint(
            "cost_units IS NULL OR cost_units >= 0",
            name="ck_provider_attempts_cost_units",
        ),
        CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0",
            name="ck_provider_attempts_duration_ms",
        ),
        CheckConstraint(
            "cost_amount_usd IS NULL OR cost_amount_usd >= 0",
            name="ck_provider_attempts_cost_usd",
        ),
        Index("ix_provider_attempts_company_id", "company_id"),
        Index(
            "ix_provider_attempts_job_id",
            "job_id",
            postgresql_where=text("job_id IS NOT NULL"),
        ),
        Index(
            "ix_provider_attempts_search_run_id",
            "search_run_id",
            postgresql_where=text("search_run_id IS NOT NULL"),
        ),
        Index("ix_provider_attempts_provider_created", "provider", "created_at"),
    )


class CompanyEvent(Base):
    __tablename__ = "company_events"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    company_id = Column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    event_type = Column(Text, nullable=False)
    old_value = Column(JSONB)
    new_value = Column(JSONB)
    actor_type = Column(Text, nullable=False)
    actor_id = Column(Text)
    reason = Column(Text)
    metadata_json = Column(JSONB)
    created_at = Column(sa.TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "actor_type IN ('USER','WORKER','SYSTEM')",
            name="ck_company_events_actor_type",
        ),
        Index("ix_company_events_company_created", "company_id", "created_at"),
    )


class CompanyMerge(Base):
    __tablename__ = "company_merges"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    canonical_id = Column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    merged_id = Column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    merge_reason = Column(Text, nullable=False)
    merge_rule = Column(Text, nullable=False)
    actor = Column(Text)
    evidence = Column(JSONB)
    created_at = Column(sa.TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("canonical_id <> merged_id", name="ck_company_merges_no_self_merge"),
        # cross-state merge bloqueado por trigger (adicionado na migration)
        UniqueConstraint("merged_id", name="uq_company_merges_merged_id"),
        Index("ix_company_merges_canonical_id", "canonical_id"),
    )


class InventoryTarget(Base):
    __tablename__ = "inventory_targets"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    source_state = Column(VARCHAR(2), nullable=False)
    scope = Column(
        sa.Enum("TOTAL", "NICHE", name="inventory_scope"),
        nullable=False,
    )
    niche = Column(Text)
    target_stock = Column(Integer, nullable=False, server_default=text("0"))
    low_threshold = Column(Integer, nullable=False, server_default=text("0"))
    batch_size = Column(Integer, nullable=False, server_default=text("10"))
    max_active_jobs = Column(Integer, nullable=False, server_default=text("5"))
    enabled = Column(Boolean, nullable=False, server_default=text("false"))
    created_at = Column(sa.TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(sa.TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "(scope = 'TOTAL' AND niche IS NULL) OR (scope = 'NICHE' AND niche IS NOT NULL)",
            name="ck_inventory_targets_scope_niche",
        ),
        CheckConstraint("target_stock >= 0", name="ck_inventory_targets_target_stock"),
        CheckConstraint("low_threshold >= 0", name="ck_inventory_targets_low_threshold"),
        CheckConstraint(
            "low_threshold <= target_stock",
            name="ck_inventory_targets_threshold_lte_target",
        ),
        CheckConstraint("batch_size > 0", name="ck_inventory_targets_batch_size"),
        CheckConstraint("max_active_jobs > 0", name="ck_inventory_targets_max_active_jobs"),
        Index(
            "uq_inventory_targets_state_total",
            "source_state",
            unique=True,
            postgresql_where=text("scope = 'TOTAL'"),
        ),
        Index(
            "uq_inventory_targets_state_niche",
            "source_state", "niche",
            unique=True,
            postgresql_where=text("scope = 'NICHE'"),
        ),
    )


class ReplenishmentJob(Base):
    __tablename__ = "replenishment_jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    inventory_target_id = Column(
        UUID(as_uuid=True),
        ForeignKey("inventory_targets.id", ondelete="RESTRICT"),
        nullable=False,
    )
    job_id = Column(
        UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="SET NULL"),
        unique=True,
    )
    status = Column(Text, nullable=False, server_default="PENDING")
    target_count = Column(Integer, nullable=False)
    found_count = Column(Integer, server_default=text("0"))
    ready_count = Column(Integer, server_default=text("0"))
    error = Column(Text)
    started_at = Column(sa.TIMESTAMP(timezone=True))
    finished_at = Column(sa.TIMESTAMP(timezone=True))
    created_at = Column(sa.TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(sa.TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING','RUNNING','SUCCEEDED','FAILED','CANCELLED')",
            name="ck_replenishment_jobs_status",
        ),
        CheckConstraint("target_count >= 0", name="ck_replenishment_jobs_target_count"),
        CheckConstraint("found_count >= 0", name="ck_replenishment_jobs_found_count"),
        CheckConstraint("ready_count >= 0", name="ck_replenishment_jobs_ready_count"),
        CheckConstraint(
            "ready_count <= found_count",
            name="ck_replenishment_jobs_ready_lte_found",
        ),
        Index(
            "ix_replenishment_jobs_target_status_created",
            "inventory_target_id", "status", "created_at",
        ),
    )


class AppSetting(Base):
    __tablename__ = "app_settings"

    key = Column(Text, primary_key=True)
    value_json = Column(JSONB, nullable=False)
    description = Column(Text)
    updated_at = Column(sa.TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    updated_by = Column(
        UUID(as_uuid=True),
        ForeignKey("user_profiles.id", ondelete="SET NULL"),
    )
