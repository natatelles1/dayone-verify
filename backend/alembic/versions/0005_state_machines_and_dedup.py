"""state_machines_and_dedup — Bloco 4

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-24

Adiciona:
  - validate_company_transitions(): trigger BEFORE UPDATE que bloqueia transições
    de status inválidas para dossier_status, usage_status e verification_status.
    Reabertura de PASSED/FAILED requer app.actor_role = 'ADMIN' via SET LOCAL.
  - trg_companies_validate_transitions: dispara validate_company_transitions().
  - merge_companies_fn(): função transacional que funde duas companies da mesma
    source_state, reatribuindo evidências, endereços, documentos, snapshots,
    provider_attempts e search_run_candidates, e registrando em company_merges e
    company_events.

Ordem de disparo (alfabética): protect_legacy (p) < validate_transitions (v),
portanto FL é sempre rejeitada pela barreira legacy antes de chegar aqui.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_VALIDATE_TRANSITIONS_FN = """
CREATE OR REPLACE FUNCTION validate_company_transitions()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    -- dossier_status
    IF OLD.dossier_status IS DISTINCT FROM NEW.dossier_status THEN
        IF NOT (
            (OLD.dossier_status = 'DISCOVERED'      AND NEW.dossier_status = 'MATCHED')           OR
            (OLD.dossier_status = 'MATCHED'          AND NEW.dossier_status = 'DOSSIER_BUILDING')  OR
            (OLD.dossier_status = 'DOSSIER_BUILDING' AND NEW.dossier_status = 'READY')             OR
            (OLD.dossier_status = 'DOSSIER_BUILDING' AND NEW.dossier_status = 'PARTIAL')           OR
            (OLD.dossier_status = 'PARTIAL'          AND NEW.dossier_status = 'DOSSIER_BUILDING')  OR
            (OLD.dossier_status = 'READY'            AND NEW.dossier_status = 'PARTIAL')
        ) THEN
            RAISE EXCEPTION
                'Transição dossier_status inválida: %→% (company %)',
                OLD.dossier_status, NEW.dossier_status, OLD.id;
        END IF;
    END IF;

    -- usage_status
    IF OLD.usage_status IS DISTINCT FROM NEW.usage_status THEN
        IF NOT (
            (OLD.usage_status = 'AVAILABLE' AND NEW.usage_status = 'IN_USE')      OR
            (OLD.usage_status = 'IN_USE'    AND NEW.usage_status = 'FINALIZED')
        ) THEN
            RAISE EXCEPTION
                'Transição usage_status inválida: %→% (company %)',
                OLD.usage_status, NEW.usage_status, OLD.id;
        END IF;
    END IF;

    -- verification_status
    IF OLD.verification_status IS DISTINCT FROM NEW.verification_status THEN
        IF (OLD.verification_status = 'PASSED' AND NEW.verification_status = 'IN_PROGRESS') OR
           (OLD.verification_status = 'FAILED' AND NEW.verification_status = 'IN_PROGRESS') THEN
            IF current_setting('app.actor_role', true) IS DISTINCT FROM 'ADMIN' THEN
                RAISE EXCEPTION
                    'Reabertura de verification_status=%→IN_PROGRESS requer app.actor_role=ADMIN (company %)',
                    OLD.verification_status, OLD.id;
            END IF;
        ELSIF NOT (
            (OLD.verification_status = 'NOT_STARTED'  AND NEW.verification_status = 'IN_PROGRESS') OR
            (OLD.verification_status = 'IN_PROGRESS'  AND NEW.verification_status = 'PASSED')      OR
            (OLD.verification_status = 'IN_PROGRESS'  AND NEW.verification_status = 'FAILED')
        ) THEN
            RAISE EXCEPTION
                'Transição verification_status inválida: %→% (company %)',
                OLD.verification_status, NEW.verification_status, OLD.id;
        END IF;
    END IF;

    RETURN NEW;
END;
$$;
"""

_VALIDATE_TRANSITIONS_TRIGGER = """
CREATE TRIGGER trg_companies_validate_transitions
    BEFORE UPDATE ON companies
    FOR EACH ROW EXECUTE FUNCTION validate_company_transitions()
"""

_MERGE_FN = """
CREATE OR REPLACE FUNCTION merge_companies_fn(
    p_canonical_id UUID,
    p_merged_id    UUID,
    p_merge_reason TEXT,
    p_merge_rule   TEXT,
    p_actor        TEXT
) RETURNS void LANGUAGE plpgsql AS $$
DECLARE
    v_canonical_state VARCHAR(2);
    v_merged_state    VARCHAR(2);
BEGIN
    SELECT source_state INTO v_canonical_state FROM companies WHERE id = p_canonical_id;
    SELECT source_state INTO v_merged_state    FROM companies WHERE id = p_merged_id;

    IF v_canonical_state IS NULL OR v_merged_state IS NULL THEN
        RAISE EXCEPTION 'merge_companies_fn: uma ou ambas as companies não encontradas (% / %)',
            p_canonical_id, p_merged_id;
    END IF;

    IF v_canonical_state IS DISTINCT FROM v_merged_state THEN
        RAISE EXCEPTION 'merge_companies_fn: merge cross-state proibido: %→% (% / %)',
            v_canonical_state, v_merged_state, p_canonical_id, p_merged_id;
    END IF;

    -- Evidências: reatribui todas
    UPDATE company_field_evidence
    SET company_id = p_canonical_id
    WHERE company_id = p_merged_id;

    -- Endereços: reatribui todos
    UPDATE company_addresses
    SET company_id = p_canonical_id
    WHERE company_id = p_merged_id;

    -- Documentos: só os sem conflito de sha256 e file_number
    UPDATE company_documents
    SET company_id = p_canonical_id
    WHERE company_id = p_merged_id
      AND (sha256 IS NULL OR NOT EXISTS (
          SELECT 1 FROM company_documents d2
          WHERE d2.company_id = p_canonical_id AND d2.sha256 = company_documents.sha256))
      AND (file_number IS NULL OR NOT EXISTS (
          SELECT 1 FROM company_documents d2
          WHERE d2.company_id = p_canonical_id
            AND d2.provider = company_documents.provider
            AND d2.file_number = company_documents.file_number));
    DELETE FROM company_documents WHERE company_id = p_merged_id;

    -- Snapshots: só os sem conflito de (url, sha256)
    UPDATE website_snapshots
    SET company_id = p_canonical_id
    WHERE company_id = p_merged_id
      AND NOT EXISTS (
          SELECT 1 FROM website_snapshots w2
          WHERE w2.company_id = p_canonical_id
            AND w2.url = website_snapshots.url
            AND w2.sha256 = website_snapshots.sha256);
    DELETE FROM website_snapshots WHERE company_id = p_merged_id;

    -- Provider attempts: reatribui todos
    UPDATE provider_attempts
    SET company_id = p_canonical_id
    WHERE company_id = p_merged_id;

    -- Search run candidates: reatribui onde não há conflito de (search_run_id, company_id)
    UPDATE search_run_candidates
    SET company_id = p_canonical_id
    WHERE company_id = p_merged_id
      AND NOT EXISTS (
          SELECT 1 FROM search_run_candidates s2
          WHERE s2.search_run_id = search_run_candidates.search_run_id
            AND s2.company_id = p_canonical_id);
    -- Restantes (com conflito): NULL para não violar FK
    UPDATE search_run_candidates
    SET company_id = NULL
    WHERE company_id = p_merged_id;

    -- Registro do merge
    INSERT INTO company_merges (canonical_id, merged_id, merge_reason, merge_rule, actor)
    VALUES (p_canonical_id, p_merged_id, p_merge_reason, p_merge_rule, p_actor);

    -- Eventos de auditoria
    INSERT INTO company_events (company_id, event_type, old_value, new_value, actor_type, actor_id, reason)
    VALUES
        (p_canonical_id, 'COMPANY_MERGED_INTO',
         jsonb_build_object('merged_id', p_merged_id::text),
         jsonb_build_object('rule', p_merge_rule),
         'SYSTEM', p_actor, p_merge_reason),
        (p_merged_id, 'COMPANY_ABSORBED',
         jsonb_build_object('canonical_id', p_canonical_id::text),
         jsonb_build_object('rule', p_merge_rule),
         'SYSTEM', p_actor, p_merge_reason);
END;
$$;
"""


def upgrade() -> None:
    op.execute(_VALIDATE_TRANSITIONS_FN)
    op.execute(_VALIDATE_TRANSITIONS_TRIGGER)
    op.execute(_MERGE_FN)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_companies_validate_transitions ON companies")
    op.execute("DROP FUNCTION IF EXISTS validate_company_transitions()")
    op.execute("DROP FUNCTION IF EXISTS merge_companies_fn(UUID, UUID, TEXT, TEXT, TEXT)")
