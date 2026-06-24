"""add_idempotency_indexes — índices de idempotência para importação FL

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-24

Dois índices únicos parciais:
1. company_addresses(company_id, address_type, address_hash, source)
   WHERE address_hash IS NOT NULL
   → impede duplicação de mesmo endereço por empresa/tipo/fonte em re-runs.

2. company_documents(company_id, source_url)
   WHERE source_url IS NOT NULL
   → impede duplicação de mesmo documento (URL) por empresa em re-runs.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "uq_company_addresses_company_type_hash_source",
        "company_addresses",
        ["company_id", "address_type", "address_hash", "source"],
        unique=True,
        postgresql_where=sa.text("address_hash IS NOT NULL"),
    )
    op.create_index(
        "uq_company_documents_company_source_url",
        "company_documents",
        ["company_id", "source_url"],
        unique=True,
        postgresql_where=sa.text("source_url IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_company_documents_company_source_url",
        table_name="company_documents",
        postgresql_where=sa.text("source_url IS NOT NULL"),
    )
    op.drop_index(
        "uq_company_addresses_company_type_hash_source",
        table_name="company_addresses",
        postgresql_where=sa.text("address_hash IS NOT NULL"),
    )
