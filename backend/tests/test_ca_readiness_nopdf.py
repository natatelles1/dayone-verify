"""Testes — evaluate_ca_dossier_readiness_nopdf (READY_NO_PDF, Bloco 5).

Pure calculation: nenhuma escrita no banco — todos os testes usam mocks.
Execute calls esperadas (happy path):
  0: legal_name evidence    → .first() não-None
  1: principal_address      → .first() não-None
  2: email field_values     → .all() retorna [(email,)]  (validado com classify_email)
  3: phone evidence         → .first() não-None
  4: website snapshot       → .first() não-None
  5: evidence categories    → .scalar_one() >= 2
  6: contradictions         → .first() None  (OK)
"""
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.ca_readiness import (
    CompanyNotEligibleError,
    NoPdfReadinessResult,
    evaluate_ca_dossier_readiness_nopdf,
)


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _make_company(**kwargs):
    defaults = {
        "id": uuid.uuid4(),
        "source_state": "CA",
        "readiness_locked": False,
        "readiness_policy": "CA_STANDARD",
        "legacy_read_only": False,
        "legal_name": "ACME ACCOUNTING LLC",
        "entity_number": "202300012345",
        "website_url": "https://acme.com",
        "domain": "acme.com",
        "match_score": 85,
        "dossier_status": "DOSSIER_BUILDING",
    }
    defaults.update(kwargs)
    return MagicMock(**defaults)


def _row_exists():
    r = MagicMock()
    r.first = MagicMock(return_value=object())
    r.scalar_one = MagicMock(return_value=3)
    return r


def _row_missing():
    r = MagicMock()
    r.first = MagicMock(return_value=None)
    r.scalar_one = MagicMock(return_value=0)
    return r


def _email_rows(*emails):
    """Mock para a query de email — .all() retorna lista de (field_value,) tuplas."""
    r = MagicMock()
    r.all = MagicMock(return_value=[(e,) for e in emails])
    return r


def _all_pass_effects():
    """7 execute side-effects para o happy path nopdf (sem SI, sem escrita)."""
    cat_result = _row_exists()
    cat_result.scalar_one = MagicMock(return_value=3)
    return [
        _row_exists(),                           # 0: legal_name evidence
        _row_exists(),                           # 1: principal_address
        _email_rows("billing@bestcpa.com"),      # 2: email → COMPANY_DOMAIN ✅
        _row_exists(),                           # 3: phone evidence
        _row_exists(),                           # 4: website snapshot
        cat_result,                              # 5: evidence categories (>=2)
        _row_missing(),                          # 6: contradictions (nenhuma = OK)
    ]


def _make_session(company, execute_side_effects=None):
    session = AsyncMock()
    session.get = AsyncMock(return_value=company)
    if execute_side_effects is not None:
        session.execute = AsyncMock(side_effect=execute_side_effects)
    else:
        session.execute = AsyncMock(
            return_value=MagicMock(first=MagicMock(return_value=object()))
        )
    return session


# ─── Testes ───────────────────────────────────────────────────────────────────


class TestNoPdfReadiness:

    @pytest.mark.asyncio
    async def test_all_criteria_ok_returns_ready_no_pdf(self):
        """10 critérios OK (sem PDF) → decision='READY_NO_PDF', sem partial_reasons."""
        company = _make_company()
        session = _make_session(company, _all_pass_effects())

        result = await evaluate_ca_dossier_readiness_nopdf(company.id, session)

        assert isinstance(result, NoPdfReadinessResult)
        assert result.decision == "READY_NO_PDF"
        assert result.partial_reasons == []

    @pytest.mark.asyncio
    async def test_freemail_only_returns_partial_with_email_reason(self):
        """Apenas email freemail (gmail) → PARTIAL; reason menciona email."""
        company = _make_company()
        effects = [
            _row_exists(),                           # legal_name evidence
            _row_exists(),                           # principal_address
            _email_rows("owner@gmail.com"),          # email → GENERIC_FREEMAIL ❌
            _row_exists(),                           # phone
            _row_exists(),                           # snapshot
            _row_exists(),                           # categories (scalar_one=3)
            _row_missing(),                          # contradictions OK
        ]
        effects[5].scalar_one = MagicMock(return_value=3)
        session = _make_session(company, effects)

        result = await evaluate_ca_dossier_readiness_nopdf(company.id, session)

        assert result.decision == "PARTIAL"
        assert any("email" in r for r in result.partial_reasons)

    @pytest.mark.asyncio
    async def test_builder_email_hostingersite_returns_partial(self):
        """Email de subdomínio Hostinger → PLACEHOLDER → não aceito → PARTIAL."""
        company = _make_company()
        effects = [
            _row_exists(),
            _row_exists(),
            _email_rows("hello@nexusworksllc-com-976359.hostingersite.com"),  # PLACEHOLDER ❌
            _row_exists(),
            _row_exists(),
            _row_exists(),
            _row_missing(),
        ]
        effects[5].scalar_one = MagicMock(return_value=3)
        session = _make_session(company, effects)

        result = await evaluate_ca_dossier_readiness_nopdf(company.id, session)

        assert result.decision == "PARTIAL"
        assert any("email" in r for r in result.partial_reasons)

    @pytest.mark.asyncio
    async def test_missing_entity_number_returns_partial(self):
        """entity_number vazio → PARTIAL com reason 'entity_number ausente'."""
        company = _make_company(entity_number=None)
        effects = [
            _row_exists(),                           # legal_name evidence
            _row_exists(),                           # principal_address
            _email_rows("contact@acme.com"),         # email OK
            _row_exists(),                           # phone
            _row_exists(),                           # snapshot
            _row_exists(),                           # categories
            _row_missing(),                          # contradictions OK
        ]
        effects[5].scalar_one = MagicMock(return_value=3)
        session = _make_session(company, effects)

        result = await evaluate_ca_dossier_readiness_nopdf(company.id, session)

        assert result.decision == "PARTIAL"
        assert "entity_number ausente" in result.partial_reasons

    @pytest.mark.asyncio
    async def test_pdf_present_does_not_break_and_returns_ready_no_pdf(self):
        """PDF/SI presente não quebra nada — critério #6 é ignorado → READY_NO_PDF."""
        company = _make_company()
        # Mesmo happy path: sem query de SI document nos side_effects
        session = _make_session(company, _all_pass_effects())

        result = await evaluate_ca_dossier_readiness_nopdf(company.id, session)

        # PDF existindo ou não é irrelevante — a função não consulta SI
        assert result.decision == "READY_NO_PDF"
        assert result.partial_reasons == []

    @pytest.mark.asyncio
    async def test_readiness_locked_raises_not_eligible(self):
        """readiness_locked=True → CompanyNotEligibleError (barreira 1 preservada)."""
        company = _make_company(readiness_locked=True)
        session = _make_session(company)

        with pytest.raises(CompanyNotEligibleError, match="readiness_locked=true"):
            await evaluate_ca_dossier_readiness_nopdf(company.id, session)

    @pytest.mark.asyncio
    async def test_non_ca_raises_not_eligible(self):
        """source_state != 'CA' → CompanyNotEligibleError (barreira 2 preservada)."""
        company = _make_company(source_state="FL", readiness_locked=False)
        session = _make_session(company)

        with pytest.raises(CompanyNotEligibleError, match="source_state="):
            await evaluate_ca_dossier_readiness_nopdf(company.id, session)
