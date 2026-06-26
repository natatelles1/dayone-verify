"""add_ready_no_pdf_status — Bloco 5 (READY_NO_PDF)

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-25

Adiciona:
  (a) Valor 'READY_NO_PDF' ao enum dossier_status.
      ALTER TYPE ... ADD VALUE deve rodar FORA de transação no PostgreSQL.
      Usamos op.get_context().autocommit_block() (Alembic ≥ 1.10) para sair
      do bloco transacional antes de executar o comando e re-entrar em seguida.
  (b) Atualiza validate_company_transitions() com as novas transições:
        DOSSIER_BUILDING → READY_NO_PDF  (entrada — análogo a DOSSIER_BUILDING→READY)
        READY_NO_PDF     → PARTIAL       (downgrade — análogo a READY→PARTIAL)

  Transições NÃO adicionadas (e por quê):
    READY_NO_PDF → READY: upgrade quando PDF for posteriormente validado.
      Não incluída agora — fluxo de upgrade não está em escopo neste bloco.
      Adicionar em migração futura se necessário.
    READY_NO_PDF → DOSSIER_BUILDING: não necessária — o caminho de re-avaliação
      é READY_NO_PDF → PARTIAL → DOSSIER_BUILDING (já existe PARTIAL→DOSSIER_BUILDING).

  Downgrade:
    - O enum value 'READY_NO_PDF' NÃO pode ser removido (PostgreSQL não suporta
      ALTER TYPE ... DROP VALUE). O valor permanece mesmo após downgrade.
    - Apenas o trigger é revertido para a versão 0005.

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ── V2: inclui READY_NO_PDF ────────────────────────────────────────────────────
_VALIDATE_TRANSITIONS_V2 = """
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
            (OLD.dossier_status = 'DOSSIER_BUILDING' AND NEW.dossier_status = 'READY_NO_PDF')      OR
            (OLD.dossier_status = 'PARTIAL'          AND NEW.dossier_status = 'DOSSIER_BUILDING')  OR
            (OLD.dossier_status = 'READY'            AND NEW.dossier_status = 'PARTIAL')           OR
            (OLD.dossier_status = 'READY_NO_PDF'     AND NEW.dossier_status = 'PARTIAL')
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

# ── V1: versão 0005 — para downgrade do trigger (enum não reverte) ─────────────
_VALIDATE_TRANSITIONS_V1 = """
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


def upgrade() -> None:
    # (a) ADD VALUE fora de transação — obrigatório no PostgreSQL
    with op.get_context().autocommit_block():
        op.execute(
            sa.text("ALTER TYPE dossier_status ADD VALUE IF NOT EXISTS 'READY_NO_PDF'")
        )
    # (b) Atualizar função de validação de transições (pode rodar em transação)
    op.execute(sa.text(_VALIDATE_TRANSITIONS_V2))


def downgrade() -> None:
    # AVISO: 'READY_NO_PDF' permanece no enum após downgrade (PostgreSQL não suporta DROP VALUE).
    # Apenas revertemos o trigger para a versão 0005.
    op.execute(sa.text(_VALIDATE_TRANSITIONS_V1))
