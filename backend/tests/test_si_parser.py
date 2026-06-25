"""Testes do SI parser — fixture 11CC real + casos de borda.

Fixture canônica (auditoria seção 17):
  Entity No. 202112310799  — NÃO confundir com File No.
  File No.   BA20250271262 — só registrar, nunca usar como identidade
  Date Filed 2025-02-06
  2 páginas
  Agent email SYL94563@GMAIL.COM → NÃO é e-mail comercial
  company_email = None (nenhum e-mail de domínio próprio no PDF)
"""
from __future__ import annotations

import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.services.si_parser import SIParseError, SIParseResult, parse_si

_FIXTURE = Path(__file__).parent / "fixtures" / "11cc_statement_of_information.pdf"


@pytest.fixture(scope="module")
def pdf_bytes() -> bytes:
    return _FIXTURE.read_bytes()


@pytest.fixture(scope="module")
def parsed(pdf_bytes: bytes) -> SIParseResult:
    return parse_si(pdf_bytes)


# ── Fixture 11CC — 6 pontos canônicos da auditoria ──────────────────────────

class TestFixture11CC:
    def test_entity_number_is_202112310799(self, parsed: SIParseResult):
        """Entity No. correto — NÃO confundir com File No."""
        assert parsed.entity_number == "202112310799"

    def test_file_number_is_BA20250271262(self, parsed: SIParseResult):
        """File No. capturado separadamente — só para registro."""
        assert parsed.file_number == "BA20250271262"

    def test_entity_number_distinct_from_file_number(self, parsed: SIParseResult):
        """Entity No. e File No. são campos separados e com valores distintos."""
        assert parsed.entity_number != parsed.file_number

    def test_filed_date_is_2025_02_06(self, parsed: SIParseResult):
        assert parsed.filed_date == datetime.date(2025, 2, 6)

    def test_principal_address_separate_from_agent_address(self, parsed: SIParseResult):
        """Endereços extraídos como campos separados."""
        assert parsed.principal_address is not None
        assert parsed.agent_address is not None
        # Ambos são campos independentes (mesmo que coincidam no endereço físico)
        assert isinstance(parsed.principal_address, str)
        assert isinstance(parsed.agent_address, str)

    def test_principal_address_contains_orinda(self, parsed: SIParseResult):
        assert parsed.principal_address is not None
        assert "ORINDA" in parsed.principal_address.upper()

    def test_agent_email_is_not_company_email(self, parsed: SIParseResult):
        """SYL94563@GMAIL.COM é do agente — não deve virar e-mail comercial."""
        # agent_email capturado (para descarte)
        assert parsed.agent_email is not None
        assert "gmail.com" in parsed.agent_email.lower()
        # company_email deve ser None: sem e-mail de domínio próprio neste PDF
        assert parsed.company_email is None

    def test_page_count_is_2(self, parsed: SIParseResult):
        assert parsed.page_count == 2

    def test_is_not_no_change(self, parsed: SIParseResult):
        assert parsed.is_no_change is False

    def test_legal_name_extracted(self, parsed: SIParseResult):
        assert parsed.legal_name is not None
        assert "11CC" in parsed.legal_name.upper()


# ── File No. nunca deve ser usado como identidade ───────────────────────────

class TestFileNumberNeverIdentity:
    def test_file_number_is_ba_prefixed(self, parsed: SIParseResult):
        """BA-prefix é o padrão de File No. da CA SOS — distinção visual do Entity No."""
        assert parsed.file_number is not None
        assert parsed.file_number.startswith("BA")

    def test_entity_number_is_numeric(self, parsed: SIParseResult):
        """Entity No. da CA SOS é puramente numérico (sem prefixo BA)."""
        assert parsed.entity_number is not None
        assert parsed.entity_number.isdigit(), (
            f"Entity No. deveria ser numérico, mas é {parsed.entity_number!r}"
        )


# ── SI-NO CHANGE detectado ───────────────────────────────────────────────────

class TestSINoChangeDetection:
    def _make_parsed_with_text(self, text: str) -> SIParseResult:
        mock_page = MagicMock()
        mock_page.extract_text.return_value = text

        with patch("pdfplumber.open") as mock_open:
            mock_pdf = MagicMock()
            mock_pdf.pages = [mock_page]
            mock_open.return_value.__enter__.return_value = mock_pdf
            return parse_si(b"%PDF-1.4 stub")

    def test_statement_of_no_change_detected(self):
        result = self._make_parsed_with_text(
            "STATEMENT OF NO CHANGE\nEntity No. 202112310799\n"
            "File No.: BA20250271262\nDate Filed: 2/6/2025"
        )
        assert result.is_no_change is True

    def test_no_changes_to_report_detected(self):
        result = self._make_parsed_with_text(
            "No changes to report\nEntity No. 202112310799\n"
            "File No.: BA20250271262\nDate Filed: 1/1/2025"
        )
        assert result.is_no_change is True

    def test_regular_si_not_flagged_as_no_change(self):
        """'No Manager or Member...' NÃO deve ativar is_no_change."""
        result = self._make_parsed_with_text(
            "Entity Details\n"
            "Limited Liability Company Name ACME LLC\n"
            "Entity No. 202300012345\n"
            "No Manager or Member has an outstanding judgment.\n"
            "File No.: BA20240000001\nDate Filed: 3/15/2024"
        )
        assert result.is_no_change is False


# ── Arquivo vazio / texto não extraível ─────────────────────────────────────

class TestParseEdgeCases:
    def test_empty_text_raises_parse_error(self):
        """PDF sem texto extraível deve lançar SIParseError."""
        mock_page = MagicMock()
        mock_page.extract_text.return_value = ""

        with patch("pdfplumber.open") as mock_open:
            mock_pdf = MagicMock()
            mock_pdf.pages = [mock_page]
            mock_open.return_value.__enter__.return_value = mock_pdf

            with pytest.raises(SIParseError, match="texto extraível"):
                parse_si(b"%PDF-1.4 stub")

    def test_missing_entity_number_returns_none(self):
        mock_page = MagicMock()
        mock_page.extract_text.return_value = (
            "File No.: BA20240000001\nDate Filed: 1/1/2024\nSome content"
        )
        with patch("pdfplumber.open") as mock_open:
            mock_pdf = MagicMock()
            mock_pdf.pages = [mock_page]
            mock_open.return_value.__enter__.return_value = mock_pdf
            result = parse_si(b"%PDF-1.4 stub")

        assert result.entity_number is None

    def test_agent_email_in_section_not_company_email(self):
        """E-mail dentro da seção Agent nunca aparece em company_email."""
        text = (
            "Entity No. 202300012345\n"
            "File No.: BA20240000001\nDate Filed: 1/1/2024\n"
            "Agent for Service of Process\n"
            "Agent Name JOHN DOE agent@legalzoom.com\n"
            "Agent Address 123 Main St\nSACRAMENTO, CA 95814\n"
            "Type of Business\nType of Business ACCOUNTING"
        )
        mock_page = MagicMock()
        mock_page.extract_text.return_value = text
        with patch("pdfplumber.open") as mock_open:
            mock_pdf = MagicMock()
            mock_pdf.pages = [mock_page]
            mock_open.return_value.__enter__.return_value = mock_pdf
            result = parse_si(b"%PDF-1.4 stub")

        assert result.agent_email == "agent@legalzoom.com"
        assert result.company_email is None
