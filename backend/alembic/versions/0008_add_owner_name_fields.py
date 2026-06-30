"""add_owner_name_fields — Bloco 5 (régua premium READY_NO_PDF)

Revision ID: 0008
Revises: 0007
Create Date: 2026-06-30

Adiciona à tabela companies:
  - owner_first_name (Text, nullable) — nome do sócio/gerente (Manager/Member), do CA SOS Principals.csv
  - owner_last_name  (Text, nullable) — sobrenome do sócio/gerente
  - owner_source     (Text, nullable) — procedência, ex: "CA_SOS_PRINCIPALS:<entity_number>:<position>"

Aditivo e reversível — downgrade apenas remove as colunas (sem perda de dado
fora desta migration, já que nenhuma outra tabela depende delas).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("companies", sa.Column("owner_first_name", sa.Text(), nullable=True))
    op.add_column("companies", sa.Column("owner_last_name", sa.Text(), nullable=True))
    op.add_column("companies", sa.Column("owner_source", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("companies", "owner_source")
    op.drop_column("companies", "owner_last_name")
    op.drop_column("companies", "owner_first_name")
