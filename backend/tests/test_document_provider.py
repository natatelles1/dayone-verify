"""Testes do ManualUploadDocumentProvider e stubs.

Cobertura:
  - arquivo vazio → INVALID, sem DB record
  - MIME real inválido (não-PDF disfarçado de .pdf) → INVALID
  - SI-NO CHANGE → INVALID, armazenado mas não vinculado como válido
  - Entity No. divergente → MISMATCH, documento NÃO vincula a empresa
  - legal name divergente → VALID mas sinalizado (validation_errors + evidência CONTRADICTS)
  - upload R2 + INSERT corretos em company_documents
  - sha256 calculado antes do upload
  - stubs desligados lançam NotImplementedError
  - nenhuma FL tocada
"""
from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.providers.documents.base import AcceptResult
from app.providers.documents.manual_upload import ManualUploadDocumentProvider, _is_pdf_mime
from app.providers.documents.stubs import (
    BrowserDocumentProvider,
    BulkDocumentProvider,
    CalicoDocumentProvider,
    KyckrDocumentProvider,
)

_FIXTURE = Path(__file__).parent / "fixtures" / "11cc_statement_of_information.pdf"

# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_session(company_row: dict | None = None, extra_effects: list | None = None):
    """Cria AsyncMock de session com company_row como primeiro resultado de execute."""
    session = AsyncMock()
    if company_row is not None:
        row_mock = MagicMock()
        row_mock.fetchone.return_value = MagicMock(**company_row)
        effects = [row_mock] + (extra_effects or [MagicMock(), MagicMock(), MagicMock()])
    else:
        effects = extra_effects or [MagicMock(), MagicMock(), MagicMock()]
    session.execute = AsyncMock(side_effect=effects)
    return session


def _patch_r2():
    """Context manager que mocka get_r2_client para evitar chamadas reais ao R2."""
    mock_client = MagicMock()
    mock_client.put_object = MagicMock()
    return patch(
        "app.providers.documents.manual_upload.get_r2_client",
        return_value=mock_client,
    )


# ── Validação de MIME ─────────────────────────────────────────────────────────

class TestMimeValidation:
    def test_empty_bytes_not_pdf(self):
        assert not _is_pdf_mime(b"")

    def test_pdf_magic_bytes_pass(self):
        assert _is_pdf_mime(b"%PDF-1.4 minimal content")

    def test_png_disguised_as_pdf_rejected(self):
        png_header = b"\x89PNG\r\n\x1a\n" + b"x" * 100
        assert not _is_pdf_mime(png_header)

    def test_zip_disguised_as_pdf_rejected(self):
        zip_header = b"PK\x03\x04" + b"x" * 100
        assert not _is_pdf_mime(zip_header)


# ── Validação de arquivo ──────────────────────────────────────────────────────

class TestFileValidation:
    @pytest.mark.asyncio
    async def test_empty_file_returns_invalid(self):
        provider = ManualUploadDocumentProvider()
        session = AsyncMock()
        result = await provider.accept(
            company_id=uuid.uuid4(),
            data=b"",
            filename="empty.pdf",
            session=session,
        )
        assert result.validation_status == "INVALID"
        assert any("vazio" in e for e in result.validation_errors)
        assert result.document_id is None
        session.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_pdf_mime_returns_invalid(self):
        provider = ManualUploadDocumentProvider()
        session = AsyncMock()
        fake_pdf = b"\x89PNG\r\n\x1a\n" + b"x" * 200
        result = await provider.accept(
            company_id=uuid.uuid4(),
            data=fake_pdf,
            filename="evil.pdf",
            session=session,
        )
        assert result.validation_status == "INVALID"
        assert any("MIME" in e for e in result.validation_errors)
        session.execute.assert_not_called()


# ── SI-NO CHANGE ─────────────────────────────────────────────────────────────

class TestSINoChange:
    @pytest.mark.asyncio
    async def test_no_change_returns_invalid(self):
        """SI-NO CHANGE: armazenado no R2 mas validation_status=INVALID."""
        provider = ManualUploadDocumentProvider()
        session = _make_session()

        with patch("app.providers.documents.manual_upload.parse_si") as mock_parse, \
             _patch_r2():
            mock_parse.return_value = MagicMock(
                is_no_change=True,
                entity_number="202112310799",
                file_number="BA20250271262",
                legal_name="11CC, LLC",
                filed_date=None,
                page_count=1,
            )
            result = await provider.accept(
                company_id=uuid.uuid4(),
                data=b"%PDF-1.4 no change content",
                filename="no_change.pdf",
                session=session,
            )

        assert result.validation_status == "INVALID"
        assert any("SI-NO CHANGE" in e for e in result.validation_errors)

    @pytest.mark.asyncio
    async def test_no_change_does_not_replace_complete(self):
        """Confirma que SI-NO CHANGE nunca avança readiness (validation_status != VALID)."""
        provider = ManualUploadDocumentProvider()
        session = _make_session()

        with patch("app.providers.documents.manual_upload.parse_si") as mock_parse, \
             _patch_r2():
            mock_parse.return_value = MagicMock(
                is_no_change=True,
                entity_number="202112310799",
                file_number=None,
                legal_name=None,
                filed_date=None,
                page_count=1,
            )
            result = await provider.accept(
                company_id=uuid.uuid4(),
                data=b"%PDF-1.4 no change",
                filename="no_change.pdf",
                session=session,
            )

        assert result.validation_status != "VALID"


# ── Validação rígida de Entity No. ──────────────────────────────────────────

class TestEntityNumberValidation:
    @pytest.mark.asyncio
    async def test_mismatch_entity_number_returns_mismatch(self):
        """Entity No. do PDF ≠ empresa no banco → MISMATCH, não vinculado como VALID."""
        provider = ManualUploadDocumentProvider()
        session = _make_session(
            company_row={"entity_number": "999999999999", "legal_name": "11CC, LLC"}
        )

        with patch("app.providers.documents.manual_upload.parse_si") as mock_parse, \
             _patch_r2():
            mock_parse.return_value = MagicMock(
                is_no_change=False,
                entity_number="202112310799",
                file_number="BA20250271262",
                legal_name="11CC, LLC",
                filed_date=None,
                page_count=2,
            )
            result = await provider.accept(
                company_id=uuid.uuid4(),
                data=b"%PDF-1.4 mismatch",
                filename="11cc.pdf",
                session=session,
            )

        assert result.validation_status == "MISMATCH"
        assert any("Entity No." in e for e in result.validation_errors)

    @pytest.mark.asyncio
    async def test_mismatch_document_is_stored_for_audit(self):
        """Documento MISMATCH deve ser armazenado no R2 para rastreabilidade."""
        provider = ManualUploadDocumentProvider()
        session = _make_session(
            company_row={"entity_number": "999999999999", "legal_name": "11CC, LLC"}
        )

        with patch("app.providers.documents.manual_upload.parse_si") as mock_parse, \
             _patch_r2() as mock_r2_patch:
            mock_r2 = mock_r2_patch.return_value
            mock_parse.return_value = MagicMock(
                is_no_change=False,
                entity_number="202112310799",
                file_number=None,
                legal_name="11CC, LLC",
                filed_date=None,
                page_count=1,
            )
            await provider.accept(
                company_id=uuid.uuid4(),
                data=b"%PDF-1.4 mismatch content",
                filename="mismatch.pdf",
                session=session,
            )

        # Deve ter feito upload para R2 mesmo sendo MISMATCH (audit trail)
        mock_r2.put_object.assert_called_once()

    @pytest.mark.asyncio
    async def test_file_number_not_used_as_entity_identity(self):
        """File No. é registrado em file_number, nunca confundido com entity_number."""
        provider = ManualUploadDocumentProvider()
        # company tem entity_number correto
        session = _make_session(
            company_row={"entity_number": "202112310799", "legal_name": "11CC, LLC"}
        )

        with patch("app.providers.documents.manual_upload.parse_si") as mock_parse, \
             _patch_r2():
            mock_parse.return_value = MagicMock(
                is_no_change=False,
                entity_number="202112310799",   # match
                file_number="BA20250271262",    # file_number ≠ entity_number
                legal_name="11CC, LLC",
                filed_date=None,
                page_count=2,
            )
            result = await provider.accept(
                company_id=uuid.uuid4(),
                data=b"%PDF-1.4 ok",
                filename="11cc.pdf",
                session=session,
            )

        # Deve ser VALID — file_number NÃO é comparado com entity_number
        assert result.validation_status == "VALID"


# ── Legal name divergente ────────────────────────────────────────────────────

class TestLegalNameDivergence:
    @pytest.mark.asyncio
    async def test_name_mismatch_signals_but_not_rejects(self):
        """legal_name divergente gera validation_errors mas não muda para MISMATCH."""
        provider = ManualUploadDocumentProvider()
        session = _make_session(
            company_row={"entity_number": "202112310799", "legal_name": "WRONG NAME LLC"}
        )

        with patch("app.providers.documents.manual_upload.parse_si") as mock_parse, \
             _patch_r2():
            mock_parse.return_value = MagicMock(
                is_no_change=False,
                entity_number="202112310799",
                file_number="BA20250271262",
                legal_name="11CC, LLC",
                filed_date=None,
                page_count=2,
            )
            result = await provider.accept(
                company_id=uuid.uuid4(),
                data=b"%PDF-1.4 name mismatch",
                filename="11cc.pdf",
                session=session,
            )

        assert result.validation_status == "VALID"   # não rejeita
        assert any("legal_name" in e for e in result.validation_errors)  # mas sinaliza


# ── SHA256 e R2 ───────────────────────────────────────────────────────────────

class TestSha256AndR2:
    @pytest.mark.asyncio
    async def test_sha256_calculated_and_used_in_storage_key(self):
        """sha256 calculado; storage_key deve conter o hash."""
        provider = ManualUploadDocumentProvider()
        company_id = uuid.uuid4()
        pdf_data = b"%PDF-1.4 test content for sha"
        expected_sha = hashlib.sha256(pdf_data).hexdigest()

        session = _make_session(
            company_row={"entity_number": "202112310799", "legal_name": "11CC, LLC"}
        )

        with patch("app.providers.documents.manual_upload.parse_si") as mock_parse, \
             _patch_r2() as mock_r2_patch:
            mock_r2 = mock_r2_patch.return_value
            mock_parse.return_value = MagicMock(
                is_no_change=False,
                entity_number="202112310799",
                file_number=None,
                legal_name="11CC, LLC",
                filed_date=None,
                page_count=1,
            )
            await provider.accept(
                company_id=company_id,
                data=pdf_data,
                filename="test.pdf",
                session=session,
            )

        call_kwargs = mock_r2.put_object.call_args[1]
        assert expected_sha in call_kwargs["Key"]
        assert str(company_id) in call_kwargs["Key"]
        assert call_kwargs["ContentType"] == "application/pdf"


# ── Fixture 11CC completo via provider ───────────────────────────────────────

class TestProviderWith11CCFixture:
    @pytest.mark.asyncio
    async def test_11cc_pdf_accepted_as_valid(self):
        """PDF real da 11CC deve ser aceito como VALID quando entity_number confere."""
        provider = ManualUploadDocumentProvider()
        company_id = uuid.uuid4()
        pdf_data = _FIXTURE.read_bytes()

        session = _make_session(
            company_row={
                "entity_number": "202112310799",
                "legal_name": "11CC, LLC",
            }
        )

        with _patch_r2():
            result = await provider.accept(
                company_id=company_id,
                data=pdf_data,
                filename="11cc_statement_of_information.pdf",
                session=session,
            )

        assert result.validation_status == "VALID"
        assert result.validation_errors == []
        assert result.document_id is not None

    @pytest.mark.asyncio
    async def test_11cc_pdf_mismatch_when_wrong_company(self):
        """PDF da 11CC submetido para uma company com entity_number diferente → MISMATCH."""
        provider = ManualUploadDocumentProvider()
        pdf_data = _FIXTURE.read_bytes()

        session = _make_session(
            company_row={
                "entity_number": "999999999999",
                "legal_name": "DIFFERENT COMPANY LLC",
            }
        )

        with _patch_r2():
            result = await provider.accept(
                company_id=uuid.uuid4(),
                data=pdf_data,
                filename="11cc_statement_of_information.pdf",
                session=session,
            )

        assert result.validation_status == "MISMATCH"


# ── Stubs desligados ─────────────────────────────────────────────────────────

class TestStubProviders:
    @pytest.mark.asyncio
    async def test_calico_stub_raises(self):
        with pytest.raises(NotImplementedError, match="ENABLE_CALICO_DOCUMENTS"):
            await CalicoDocumentProvider().accept(
                uuid.uuid4(), b"", "x.pdf", AsyncMock()
            )

    @pytest.mark.asyncio
    async def test_bulk_stub_raises(self):
        with pytest.raises(NotImplementedError, match="ENABLE_BULK_PROVIDER"):
            await BulkDocumentProvider().accept(
                uuid.uuid4(), b"", "x.pdf", AsyncMock()
            )

    @pytest.mark.asyncio
    async def test_kyckr_stub_raises(self):
        with pytest.raises(NotImplementedError, match="ENABLE_KYCKR_PROVIDER"):
            await KyckrDocumentProvider().accept(
                uuid.uuid4(), b"", "x.pdf", AsyncMock()
            )

    @pytest.mark.asyncio
    async def test_browser_stub_raises(self):
        with pytest.raises(NotImplementedError, match="ENABLE_BROWSER_PROVIDER"):
            await BrowserDocumentProvider().accept(
                uuid.uuid4(), b"", "x.pdf", AsyncMock()
            )
