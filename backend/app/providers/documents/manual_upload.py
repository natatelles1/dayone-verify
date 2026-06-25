"""ManualUploadDocumentProvider — único provider ATIVO no Bloco 6.

Fluxo:
  1. Validação de formato (vazio / MIME real / tamanho).
  2. Parse do SI (si_parser).
  3. Detecção de SI-NO CHANGE.
  4. Query da company para entity_number e legal_name esperados.
  5. Validação rígida: entity_number divergente → MISMATCH (documento não vinculado).
  6. Upload para R2; INSERT em company_documents.
  7. Evidências (SUPPORTS ou CONTRADICTS) em company_field_evidence.

Invariantes:
  - document_type='SI', provider='MANUAL_UPLOAD'.
  - Documento MISMATCH é armazenado mas NUNCA avança a readiness.
  - E-mail do agente nunca vira company_email (regra 11CC).
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import timezone

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import text

from app.core.config import settings
from app.core.storage import get_r2_client
from app.providers.documents.base import AcceptResult, DocumentProvider
from app.services.si_parser import SIParseError, SIParseResult, parse_si

_PDF_MAGIC = b"%PDF"
MAX_PDF_BYTES = 20 * 1024 * 1024  # 20 MB

_EVIDENCE_CATEGORY_DOCUMENT = "DOCUMENT"
_SOURCE_TYPE = "STATEMENT_OF_INFORMATION"


def _is_pdf_mime(data: bytes) -> bool:
    return data[:4] == _PDF_MAGIC


class ManualUploadDocumentProvider(DocumentProvider):
    """Recebe PDF enviado manualmente e registra o documento para a company."""

    async def accept(
        self,
        company_id: uuid.UUID,
        data: bytes,
        filename: str,
        session: AsyncSession,
    ) -> AcceptResult:
        # ── 1. Validação de formato ───────────────────────────────────────────
        if not data:
            return AcceptResult(
                validation_status="INVALID",
                validation_errors=["arquivo vazio"],
            )
        if len(data) > MAX_PDF_BYTES:
            return AcceptResult(
                validation_status="INVALID",
                validation_errors=[f"arquivo excede {MAX_PDF_BYTES // (1024*1024)} MB"],
            )
        if not _is_pdf_mime(data):
            return AcceptResult(
                validation_status="INVALID",
                validation_errors=["MIME real não é application/pdf (magic bytes inválidos)"],
            )

        # ── 2. Parse do PDF ───────────────────────────────────────────────────
        try:
            parsed: SIParseResult = parse_si(data)
        except (SIParseError, Exception) as exc:
            return AcceptResult(
                validation_status="INVALID",
                validation_errors=[f"parse do PDF falhou: {exc}"],
            )

        # ── 3. SI-NO CHANGE não substitui SI-COMPLETE ─────────────────────────
        if parsed.is_no_change:
            sha256 = hashlib.sha256(data).hexdigest()
            storage_key = f"documents/{company_id}/{sha256}.pdf"
            _upload_r2(data, storage_key)
            doc_id = await _insert_document(
                session,
                company_id=company_id,
                parsed=parsed,
                sha256=sha256,
                storage_key=storage_key,
                filename=filename,
                validation_status="INVALID",
                validation_errors=["SI-NO CHANGE não substitui SI-COMPLETE"],
                subtype="NO_CHANGE",
            )
            return AcceptResult(
                validation_status="INVALID",
                validation_errors=["SI-NO CHANGE não substitui SI-COMPLETE"],
                document_id=doc_id,
            )

        # ── 4. Query da company ───────────────────────────────────────────────
        row = await session.execute(
            text("SELECT entity_number, legal_name FROM companies WHERE id = :id"),
            {"id": company_id},
        )
        company = row.fetchone()
        if company is None:
            return AcceptResult(
                validation_status="INVALID",
                validation_errors=[f"company {company_id} não encontrada"],
            )
        db_entity_number: str | None = company.entity_number
        db_legal_name: str | None = company.legal_name

        # ── 5. Validação rígida de entity_number ─────────────────────────────
        validation_errors: list[str] = []
        validation_status = "VALID"

        if db_entity_number and parsed.entity_number:
            if parsed.entity_number.upper().strip() != db_entity_number.upper().strip():
                validation_status = "MISMATCH"
                validation_errors.append(
                    f"Entity No. extraído '{parsed.entity_number}' ≠ "
                    f"'{db_entity_number}' (empresa no banco)"
                )

        # ── 6. Divergência de legal name — sinalizar, não rejeitar ───────────
        name_contradicts = False
        if (
            validation_status == "VALID"
            and db_legal_name
            and parsed.legal_name
            and parsed.legal_name.upper().strip() != db_legal_name.upper().strip()
        ):
            name_contradicts = True
            validation_errors.append(
                f"legal_name extraído '{parsed.legal_name}' ≠ '{db_legal_name}' (banco)"
            )

        # ── 7. Upload R2 + INSERT company_documents ───────────────────────────
        sha256 = hashlib.sha256(data).hexdigest()
        storage_key = f"documents/{company_id}/{sha256}.pdf"
        _upload_r2(data, storage_key)

        doc_id = await _insert_document(
            session,
            company_id=company_id,
            parsed=parsed,
            sha256=sha256,
            storage_key=storage_key,
            filename=filename,
            validation_status=validation_status,
            validation_errors=validation_errors,
            subtype="COMPLETE",
        )

        # ── 8. Evidências ─────────────────────────────────────────────────────
        if validation_status == "MISMATCH":
            await _insert_evidence(
                session,
                company_id=company_id,
                field_name="entity_number",
                field_value=parsed.entity_number or "",
                direction="CONTRADICTS",
            )
        elif validation_status == "VALID":
            await _insert_evidence(
                session,
                company_id=company_id,
                field_name="entity_number",
                field_value=parsed.entity_number or "",
                direction="SUPPORTS",
            )
            if parsed.legal_name:
                await _insert_evidence(
                    session,
                    company_id=company_id,
                    field_name="legal_name",
                    field_value=parsed.legal_name,
                    direction="CONTRADICTS" if name_contradicts else "SUPPORTS",
                )

        return AcceptResult(
            validation_status=validation_status,
            validation_errors=validation_errors,
            document_id=doc_id,
        )


def _upload_r2(data: bytes, storage_key: str) -> None:
    client = get_r2_client()
    client.put_object(
        Bucket=settings.r2_bucket,
        Key=storage_key,
        Body=data,
        ContentType="application/pdf",
    )


async def _insert_document(
    session: AsyncSession,
    *,
    company_id: uuid.UUID,
    parsed: SIParseResult,
    sha256: str,
    storage_key: str,
    filename: str,
    validation_status: str,
    validation_errors: list[str],
    subtype: str,
) -> uuid.UUID:
    import json

    doc_id = uuid.uuid4()
    await session.execute(
        text("""
            INSERT INTO company_documents (
                id, company_id, provider, document_type, subtype,
                file_number, filed_date, storage_key, original_filename,
                mime_type, size_bytes, sha256, page_count,
                entity_number_extracted, legal_name_extracted,
                validation_status, validation_errors
            ) VALUES (
                :id, :company_id, 'MANUAL_UPLOAD', 'SI', :subtype,
                :file_number, :filed_date, :storage_key, :filename,
                'application/pdf', :size_bytes, :sha256, :page_count,
                :entity_number_extracted, :legal_name_extracted,
                :validation_status, :validation_errors::jsonb
            )
            ON CONFLICT DO NOTHING
        """),
        {
            "id": doc_id,
            "company_id": company_id,
            "subtype": subtype,
            "file_number": parsed.file_number,
            "filed_date": parsed.filed_date,
            "storage_key": storage_key,
            "filename": filename,
            "size_bytes": None,   # set by caller if needed
            "sha256": sha256,
            "page_count": parsed.page_count,
            "entity_number_extracted": parsed.entity_number,
            "legal_name_extracted": parsed.legal_name,
            "validation_status": validation_status,
            "validation_errors": json.dumps(validation_errors),
        },
    )
    return doc_id


async def _insert_evidence(
    session: AsyncSession,
    *,
    company_id: uuid.UUID,
    field_name: str,
    field_value: str,
    direction: str,
) -> None:
    await session.execute(
        text("""
            INSERT INTO company_field_evidence (
                id, company_id, field_name, field_value,
                source_type, evidence_category, evidence_direction, confidence
            ) VALUES (
                gen_random_uuid(), :company_id, :field_name, :field_value,
                'STATEMENT_OF_INFORMATION', 'DOCUMENT', :direction, 0.95
            )
            ON CONFLICT DO NOTHING
        """),
        {
            "company_id": company_id,
            "field_name": field_name,
            "field_value": field_value,
            "direction": direction,
        },
    )
