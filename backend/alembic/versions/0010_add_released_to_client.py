"""add_released_to_client — trava de liberação pro Gabriel (API externa)

Revision ID: 0010
Revises: 0009
Create Date: 2026-06-30

Adiciona `released_to_client` (Boolean, NOT NULL, DEFAULT FALSE) à tabela
companies. A API externa (/api/external/companies) só retorna empresas
prontas (READY/READY_NO_PDF) E released_to_client=TRUE — o cliente
(Gabriel) só vê o que for explicitamente liberado, mesmo já estando pronto.

Default FALSE cobre as 30 redondas atuais automaticamente (nenhuma fica
liberada por engano na aplicação da migration).

Aditivo e reversível — downgrade apenas remove a coluna.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "companies",
        sa.Column("released_to_client", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column("companies", "released_to_client")
