"""rls_rpcs_add_ready_no_pdf — Bloco 5 (READY_NO_PDF visível no dashboard)

Revision ID: 0007
Revises: 0006
Create Date: 2026-06-25

Estende 5 políticas RLS e 2 RPCs para incluir READY_NO_PDF:
  - dashboard_anon_ca_companies
  - dashboard_anon_ca_addresses
  - dashboard_anon_ca_evidence
  - dashboard_anon_ca_documents
  - dashboard_anon_ca_usage_events
  - mark_company_in_use()
  - unmark_company_in_use()

Downgrade: restaura as versões 0006 (só READY + PARTIAL).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# ── Helpers ───────────────────────────────────────────────────────────────────

_READY_ARRAY_V2 = "ARRAY['READY'::dossier_status,'PARTIAL'::dossier_status,'READY_NO_PDF'::dossier_status]"
_READY_ARRAY_V1 = "ARRAY['READY'::dossier_status,'PARTIAL'::dossier_status]"


def _rls_upgrade() -> None:
    """Recria todas as políticas RLS com READY_NO_PDF."""
    stmts = [
        # companies
        "DROP POLICY IF EXISTS dashboard_anon_ca_companies ON companies",
        f"""
        CREATE POLICY dashboard_anon_ca_companies ON companies
          FOR SELECT
          USING (source_state = 'CA'
                 AND dossier_status = ANY ({_READY_ARRAY_V2}))
        """,
        # company_addresses
        "DROP POLICY IF EXISTS dashboard_anon_ca_addresses ON company_addresses",
        f"""
        CREATE POLICY dashboard_anon_ca_addresses ON company_addresses
          FOR SELECT
          USING (EXISTS (
            SELECT 1 FROM companies c
            WHERE c.id = company_addresses.company_id
              AND c.source_state = 'CA'
              AND c.dossier_status = ANY ({_READY_ARRAY_V2})
          ))
        """,
        # company_field_evidence
        "DROP POLICY IF EXISTS dashboard_anon_ca_evidence ON company_field_evidence",
        f"""
        CREATE POLICY dashboard_anon_ca_evidence ON company_field_evidence
          FOR SELECT
          USING (EXISTS (
            SELECT 1 FROM companies c
            WHERE c.id = company_field_evidence.company_id
              AND c.source_state = 'CA'
              AND c.dossier_status = ANY ({_READY_ARRAY_V2})
          ))
        """,
        # company_documents
        "DROP POLICY IF EXISTS dashboard_anon_ca_documents ON company_documents",
        f"""
        CREATE POLICY dashboard_anon_ca_documents ON company_documents
          FOR SELECT
          USING (EXISTS (
            SELECT 1 FROM companies c
            WHERE c.id = company_documents.company_id
              AND c.source_state = 'CA'
              AND c.dossier_status = ANY ({_READY_ARRAY_V2})
          ))
        """,
        # company_events — inclui READY_NO_PDF para USAGE_MARKED
        "DROP POLICY IF EXISTS dashboard_anon_ca_usage_events ON company_events",
        f"""
        CREATE POLICY dashboard_anon_ca_usage_events ON company_events
          FOR SELECT
          USING (event_type = 'USAGE_MARKED'
                 AND EXISTS (
                   SELECT 1 FROM companies c
                   WHERE c.id = company_events.company_id
                     AND c.source_state = 'CA'
                     AND c.dossier_status = ANY (ARRAY['READY'::dossier_status,'READY_NO_PDF'::dossier_status])
                 ))
        """,
    ]
    for stmt in stmts:
        op.execute(sa.text(stmt))


def _rls_downgrade() -> None:
    """Restaura políticas RLS para versão 0006 (READY + PARTIAL somente)."""
    stmts = [
        "DROP POLICY IF EXISTS dashboard_anon_ca_companies ON companies",
        f"""
        CREATE POLICY dashboard_anon_ca_companies ON companies
          FOR SELECT
          USING (source_state = 'CA'
                 AND dossier_status = ANY ({_READY_ARRAY_V1}))
        """,
        "DROP POLICY IF EXISTS dashboard_anon_ca_addresses ON company_addresses",
        f"""
        CREATE POLICY dashboard_anon_ca_addresses ON company_addresses
          FOR SELECT
          USING (EXISTS (
            SELECT 1 FROM companies c
            WHERE c.id = company_addresses.company_id
              AND c.source_state = 'CA'
              AND c.dossier_status = ANY ({_READY_ARRAY_V1})
          ))
        """,
        "DROP POLICY IF EXISTS dashboard_anon_ca_evidence ON company_field_evidence",
        f"""
        CREATE POLICY dashboard_anon_ca_evidence ON company_field_evidence
          FOR SELECT
          USING (EXISTS (
            SELECT 1 FROM companies c
            WHERE c.id = company_field_evidence.company_id
              AND c.source_state = 'CA'
              AND c.dossier_status = ANY ({_READY_ARRAY_V1})
          ))
        """,
        "DROP POLICY IF EXISTS dashboard_anon_ca_documents ON company_documents",
        f"""
        CREATE POLICY dashboard_anon_ca_documents ON company_documents
          FOR SELECT
          USING (EXISTS (
            SELECT 1 FROM companies c
            WHERE c.id = company_documents.company_id
              AND c.source_state = 'CA'
              AND c.dossier_status = ANY ({_READY_ARRAY_V1})
          ))
        """,
        "DROP POLICY IF EXISTS dashboard_anon_ca_usage_events ON company_events",
        f"""
        CREATE POLICY dashboard_anon_ca_usage_events ON company_events
          FOR SELECT
          USING (event_type = 'USAGE_MARKED'
                 AND EXISTS (
                   SELECT 1 FROM companies c
                   WHERE c.id = company_events.company_id
                     AND c.source_state = 'CA'
                     AND c.dossier_status = 'READY'::dossier_status
                 ))
        """,
    ]
    for stmt in stmts:
        op.execute(sa.text(stmt))


_MARK_V2 = """
CREATE OR REPLACE FUNCTION mark_company_in_use(p_company_id uuid)
RETURNS json
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
  v_status usage_status;
BEGIN
  SELECT usage_status INTO v_status
  FROM companies
  WHERE id = p_company_id
    AND source_state = 'CA'
    AND dossier_status IN ('READY', 'READY_NO_PDF')
  FOR UPDATE;
  IF NOT FOUND THEN
    RETURN json_build_object('error', 'Company not found or not eligible');
  END IF;
  IF v_status != 'AVAILABLE' THEN
    RETURN json_build_object('error', 'Company is not AVAILABLE');
  END IF;
  UPDATE companies SET usage_status = 'IN_USE' WHERE id = p_company_id;
  INSERT INTO company_events (company_id, event_type, old_value, new_value, actor_type)
  VALUES (p_company_id, 'USAGE_MARKED', to_jsonb('AVAILABLE'::text), to_jsonb('IN_USE'::text), 'USER');
  RETURN json_build_object('ok', true);
END;
$$
"""

_UNMARK_V2 = """
CREATE OR REPLACE FUNCTION unmark_company_in_use(p_company_id uuid)
RETURNS json
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
  v_status usage_status;
BEGIN
  SELECT usage_status INTO v_status
  FROM companies
  WHERE id = p_company_id
    AND source_state = 'CA'
    AND dossier_status IN ('READY', 'READY_NO_PDF')
  FOR UPDATE;
  IF NOT FOUND THEN
    RETURN json_build_object('error', 'Company not found or not eligible');
  END IF;
  IF v_status != 'IN_USE' THEN
    RETURN json_build_object('error', 'Company is not IN_USE');
  END IF;
  UPDATE companies SET usage_status = 'AVAILABLE' WHERE id = p_company_id;
  INSERT INTO company_events (company_id, event_type, old_value, new_value, actor_type)
  VALUES (p_company_id, 'USAGE_REVERTED', to_jsonb('IN_USE'::text), to_jsonb('AVAILABLE'::text), 'USER');
  RETURN json_build_object('ok', true);
END;
$$
"""

_MARK_V1 = """
CREATE OR REPLACE FUNCTION mark_company_in_use(p_company_id uuid)
RETURNS json
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
  v_status usage_status;
BEGIN
  SELECT usage_status INTO v_status
  FROM companies
  WHERE id = p_company_id AND source_state = 'CA' AND dossier_status = 'READY'
  FOR UPDATE;
  IF NOT FOUND THEN
    RETURN json_build_object('error', 'Company not found or not eligible');
  END IF;
  IF v_status != 'AVAILABLE' THEN
    RETURN json_build_object('error', 'Company is not AVAILABLE');
  END IF;
  UPDATE companies SET usage_status = 'IN_USE' WHERE id = p_company_id;
  INSERT INTO company_events (company_id, event_type, old_value, new_value, actor_type)
  VALUES (p_company_id, 'USAGE_MARKED', to_jsonb('AVAILABLE'::text), to_jsonb('IN_USE'::text), 'USER');
  RETURN json_build_object('ok', true);
END;
$$
"""

_UNMARK_V1 = """
CREATE OR REPLACE FUNCTION unmark_company_in_use(p_company_id uuid)
RETURNS json
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
  v_status usage_status;
BEGIN
  SELECT usage_status INTO v_status
  FROM companies
  WHERE id = p_company_id AND source_state = 'CA' AND dossier_status = 'READY'
  FOR UPDATE;
  IF NOT FOUND THEN
    RETURN json_build_object('error', 'Company not found or not eligible');
  END IF;
  IF v_status != 'IN_USE' THEN
    RETURN json_build_object('error', 'Company is not IN_USE');
  END IF;
  UPDATE companies SET usage_status = 'AVAILABLE' WHERE id = p_company_id;
  INSERT INTO company_events (company_id, event_type, old_value, new_value, actor_type)
  VALUES (p_company_id, 'USAGE_REVERTED', to_jsonb('IN_USE'::text), to_jsonb('AVAILABLE'::text), 'USER');
  RETURN json_build_object('ok', true);
END;
$$
"""


def upgrade() -> None:
    _rls_upgrade()
    op.execute(sa.text(_MARK_V2))
    op.execute(sa.text(_UNMARK_V2))


def downgrade() -> None:
    _rls_downgrade()
    op.execute(sa.text(_MARK_V1))
    op.execute(sa.text(_UNMARK_V1))
