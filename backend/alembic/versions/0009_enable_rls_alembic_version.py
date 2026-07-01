"""enable_rls_alembic_version — fecha alerta do Supabase advisor (RLS Disabled in Public)

Revision ID: 0009
Revises: 0008
Create Date: 2026-06-30

`alembic_version` é criada automaticamente pelo próprio Alembic (fora de
_ALL_TABLES da migration 0001), então nunca recebeu RLS junto das outras
15 tabelas. Anon tem grant total (SELECT/INSERT/UPDATE/DELETE/TRUNCATE)
nela por padrão do Supabase — sem RLS, isso é acesso público real.

Liga RLS sem nenhuma policy (deny-all pra anon/authenticated), mesmo
padrão já usado em app_settings/jobs/user_profiles/etc. Não quebra nada:
só quem acessa essa tabela é o Alembic via DIRECT_URL (role `postgres`,
rolbypassrls=true) e o Supabase Studio (role `postgres`/service_role,
também bypassa RLS).
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE alembic_version ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.execute("ALTER TABLE alembic_version DISABLE ROW LEVEL SECURITY")
