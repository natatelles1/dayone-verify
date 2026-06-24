"""seed_app_settings — 18 feature flags iniciais

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-24

Seed idempotente: INSERT ... ON CONFLICT (key) DO NOTHING.
Downgrade remove apenas as 18 flags deste seed (não toca registros
inseridos manualmente após o deploy).
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 18 feature flags — ordem fixa para auditabilidade
_FLAGS = [
    ("ENABLE_CA_FLOW",                     "true",  "Habilita o fluxo CA (busca, dossier, verificacao)"),
    ("ENABLE_FL_REEVALUATION",             "false", "Permite re-avaliacao de empresas FL legacy (somente leitura)"),
    ("ENABLE_FL_WRITES",                   "false", "Habilita escrita em registros FL (desabilitado: FL e read-only)"),
    ("ENABLE_MANUAL_DOCUMENT_UPLOAD",      "true",  "Permite upload manual de documentos pela API"),
    ("ENABLE_CSV_DISCOVERY",               "true",  "Habilita descoberta de empresas via importacao CSV"),
    ("ENABLE_CBA_MANUAL_IMPORT",           "true",  "Habilita importacao manual do diretorio CBA"),
    ("ENABLE_CALICO_SEARCH",               "false", "Habilita busca via provider Calico"),
    ("ENABLE_CALICO_DOCUMENTS",            "false", "Habilita download de documentos via Calico"),
    ("ENABLE_BE_MASTER_IMPORT",            "false", "Habilita importacao do master file BE"),
    ("ENABLE_BE_BULK_IMAGES",              "false", "Habilita download em lote de imagens BE"),
    ("ENABLE_GOOGLE_PLACES_DISCOVERY",     "false", "Habilita descoberta via Google Places API"),
    ("ENABLE_APIFY_MAPS_DISCOVERY",        "false", "Habilita descoberta via Apify Maps Actor"),
    ("ENABLE_APIFY_CA_ACTOR",              "false", "Habilita busca CA via Apify actor especializado"),
    ("ENABLE_THIRD_PARTY_DOCUMENTS",       "false", "Habilita busca de documentos em providers terceiros"),
    ("ENABLE_BROWSER_DOCUMENT_PROVIDER",   "false", "Habilita provider de documentos com browser headless"),
    ("ENABLE_OCR",                         "false", "Habilita OCR em documentos enviados"),
    ("ENABLE_AUTOMATIC_REPLENISHMENT",     "false", "Habilita disparo automatico de jobs de reposicao"),
    ("ENABLE_GOOGLE_SUBMISSION_AUTOMATION","false", "Habilita automacao de submissao via Google"),
]

assert len(_FLAGS) == 18, f"Esperado 18 flags, encontrado {len(_FLAGS)}"


def upgrade() -> None:
    values_parts = []
    for key, value, desc in _FLAGS:
        values_parts.append(f"('{key}', '{value}'::jsonb, '{desc}')")
    values_sql = ",\n        ".join(values_parts)
    op.execute(
        f"""
        INSERT INTO app_settings (key, value_json, description) VALUES
        {values_sql}
        ON CONFLICT (key) DO NOTHING
        """
    )


def downgrade() -> None:
    keys = ", ".join(f"'{key}'" for key, _, _ in _FLAGS)
    op.execute(f"DELETE FROM app_settings WHERE key IN ({keys})")
