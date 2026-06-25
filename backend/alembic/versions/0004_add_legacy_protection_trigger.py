"""add_legacy_protection_trigger — proteção DB para legacy_read_only=true

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-24

Trigger BEFORE UPDATE OR DELETE on companies que rejeita:
  - DELETE de qualquer company com legacy_read_only=true
  - Alteração dos campos protegidos: source_state, entity_number,
    readiness_policy, readiness_locked, legacy_read_only,
    dossier_status, usage_status, verification_status.

Defesa adicional à camada de aplicação; não substitui o filtro
source_state='CA' do estoque.

Operações de manutenção excepcional devem usar fluxo administrativo
explícito (company_event com motivo).
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PROTECT_FN = """
CREATE OR REPLACE FUNCTION protect_legacy_read_only()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    -- Bloqueia DELETE de companies legacy
    IF TG_OP = 'DELETE' THEN
        IF OLD.legacy_read_only THEN
            RAISE EXCEPTION
                'DELETE proibido: company % é legacy_read_only=true '
                '(policy=%). Use fluxo administrativo com company_event.',
                OLD.id, OLD.readiness_policy;
        END IF;
        RETURN OLD;
    END IF;

    -- Bloqueia alteração de campos protegidos em companies legacy
    IF OLD.legacy_read_only THEN
        IF OLD.source_state IS DISTINCT FROM NEW.source_state THEN
            RAISE EXCEPTION
                'source_state imutável em company legacy_read_only: id=%  %→%',
                OLD.id, OLD.source_state, NEW.source_state;
        END IF;
        IF OLD.entity_number IS DISTINCT FROM NEW.entity_number THEN
            RAISE EXCEPTION
                'entity_number imutável em company legacy_read_only: id=%  %→%',
                OLD.id, OLD.entity_number, NEW.entity_number;
        END IF;
        IF OLD.readiness_policy IS DISTINCT FROM NEW.readiness_policy THEN
            RAISE EXCEPTION
                'readiness_policy imutável em company legacy_read_only: id=%  %→%',
                OLD.id, OLD.readiness_policy, NEW.readiness_policy;
        END IF;
        IF OLD.readiness_locked IS DISTINCT FROM NEW.readiness_locked THEN
            RAISE EXCEPTION
                'readiness_locked imutável em company legacy_read_only: id=%  %→%',
                OLD.id, OLD.readiness_locked, NEW.readiness_locked;
        END IF;
        IF OLD.legacy_read_only IS DISTINCT FROM NEW.legacy_read_only THEN
            RAISE EXCEPTION
                'legacy_read_only imutável em company legacy_read_only: id=%  %→%',
                OLD.id, OLD.legacy_read_only, NEW.legacy_read_only;
        END IF;
        IF OLD.dossier_status IS DISTINCT FROM NEW.dossier_status THEN
            RAISE EXCEPTION
                'dossier_status imutável em company legacy_read_only: id=%  %→%',
                OLD.id, OLD.dossier_status, NEW.dossier_status;
        END IF;
        IF OLD.usage_status IS DISTINCT FROM NEW.usage_status THEN
            RAISE EXCEPTION
                'usage_status imutável em company legacy_read_only: id=%  %→%',
                OLD.id, OLD.usage_status, NEW.usage_status;
        END IF;
        IF OLD.verification_status IS DISTINCT FROM NEW.verification_status THEN
            RAISE EXCEPTION
                'verification_status imutável em company legacy_read_only: id=%  %→%',
                OLD.id, OLD.verification_status, NEW.verification_status;
        END IF;
    END IF;

    RETURN NEW;
END;
$$;
"""

_TRIGGER = """
CREATE TRIGGER trg_companies_protect_legacy
    BEFORE UPDATE OR DELETE ON companies
    FOR EACH ROW EXECUTE FUNCTION protect_legacy_read_only()
"""


def upgrade() -> None:
    op.execute(_PROTECT_FN)
    op.execute(_TRIGGER)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_companies_protect_legacy ON companies")
    op.execute("DROP FUNCTION IF EXISTS protect_legacy_read_only()")
