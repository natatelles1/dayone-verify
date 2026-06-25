"""Testes — ca_readiness (barreiras 1+2 preservadas; Bloco 4 com mocks)."""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.ca_readiness import (
    CompanyNotEligibleError,
    ReadinessResult,
    evaluate_ca_dossier_readiness,
)


def _make_company(**kwargs):
    defaults = {
        "id": uuid.uuid4(),
        "source_state": "CA",
        "readiness_locked": False,
        "readiness_policy": "CA_STANDARD",
        "legacy_read_only": False,
        "legal_name": "ACME ACCOUNTING LLC",
        "entity_number": "202300012345",
        "legacy_ein": None,
        "website_url": "https://acme.com",
        "domain": "acme.com",
        "email": "contact@acme.com",
        "phone_e164": "+14155551234",
        "match_score": 85,
        "dossier_status": "DOSSIER_BUILDING",  # estado esperado antes de READY/PARTIAL
    }
    defaults.update(kwargs)
    return MagicMock(**defaults)


def _make_session(company, execute_side_effects=None):
    session = AsyncMock()
    session.get = AsyncMock(return_value=company)
    if execute_side_effects is not None:
        session.execute = AsyncMock(side_effect=execute_side_effects)
    else:
        session.execute = AsyncMock(return_value=MagicMock(first=MagicMock(return_value=object())))
    return session


def _row_exists():
    """Retorna mock de resultado SQL que indica existência (first() não-None)."""
    r = MagicMock()
    r.first = MagicMock(return_value=object())
    r.scalar_one = MagicMock(return_value=3)
    return r


def _row_missing():
    """Retorna mock de resultado SQL que indica ausência (first()=None)."""
    r = MagicMock()
    r.first = MagicMock(return_value=None)
    r.scalar_one = MagicMock(return_value=0)
    return r


def _all_pass_effects():
    """Side effects para session.execute que fazem todos os 10 critérios com query passarem."""
    return [
        _row_exists(),   # 1. legal_name evidence
        _row_exists(),   # 2. principal_address
        _row_exists(),   # 3. email evidence
        _row_exists(),   # 4. phone evidence
        _row_exists(),   # 6. SI document
        _row_exists(),   # 8. website snapshot (html_text)
        _row_exists(),   # 10. evidence categories (scalar_one=3 >= 2)
        _row_missing(),  # 11. contradictions (missing = no contradictions = OK)
        # transition_dossier → session.execute (UPDATE + INSERT event)
        MagicMock(),
        MagicMock(),
    ]


# ─── Barreiras 1 e 2 (preservadas do stub) ────────────────────────────────────


class TestEvaluateCaDossierReadiness:
    @pytest.mark.asyncio
    async def test_fl_legacy_rejected_by_readiness_locked(self):
        """As 106 FL têm readiness_locked=True → sempre rejeitadas na barreira 1."""
        company = _make_company(
            source_state="FL",
            readiness_locked=True,
            readiness_policy="FL_LEGACY_V1",
        )
        session = _make_session(company)
        with pytest.raises(CompanyNotEligibleError, match="readiness_locked=true"):
            await evaluate_ca_dossier_readiness(company.id, session)

    @pytest.mark.asyncio
    async def test_locked_ca_company_also_rejected(self):
        """Mesmo source_state='CA', se readiness_locked=True → barreira 1."""
        company = _make_company(
            source_state="CA",
            readiness_locked=True,
            readiness_policy="CA_LOCKED",
        )
        session = _make_session(company)
        with pytest.raises(CompanyNotEligibleError, match="readiness_locked=true"):
            await evaluate_ca_dossier_readiness(company.id, session)

    @pytest.mark.asyncio
    async def test_non_ca_state_rejected(self):
        """source_state != 'CA' e readiness_locked=False → barreira 2."""
        company = _make_company(
            source_state="TX",
            readiness_locked=False,
            readiness_policy="TX_STANDARD",
        )
        session = _make_session(company)
        with pytest.raises(CompanyNotEligibleError, match="source_state="):
            await evaluate_ca_dossier_readiness(company.id, session)

    @pytest.mark.asyncio
    async def test_company_not_found_raises(self):
        session = AsyncMock()
        session.get = AsyncMock(return_value=None)
        with pytest.raises(CompanyNotEligibleError, match="não encontrada"):
            await evaluate_ca_dossier_readiness(uuid.uuid4(), session)

    @pytest.mark.asyncio
    async def test_fl_legacy_error_message_contains_policy(self):
        """A mensagem de erro deve mencionar a policy para facilitar debugging."""
        company = _make_company(
            source_state="FL",
            readiness_locked=True,
            readiness_policy="FL_LEGACY_V1",
        )
        session = _make_session(company)
        with pytest.raises(CompanyNotEligibleError) as exc_info:
            await evaluate_ca_dossier_readiness(company.id, session)
        assert "FL_LEGACY_V1" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_company_not_eligible_is_value_error_subclass(self):
        """CompanyNotEligibleError deve ser capturável como ValueError."""
        company = _make_company(
            source_state="FL",
            readiness_locked=True,
        )
        session = _make_session(company)
        with pytest.raises(ValueError):
            await evaluate_ca_dossier_readiness(company.id, session)


# ─── Bloco 4: critérios CA ─────────────────────────────────────────────────────


class TestEvaluateCaDossierReadinessBloco4:
    @pytest.mark.asyncio
    async def test_ca_with_all_criteria_returns_ready(self):
        """CA company com todos os critérios atendidos → ReadinessResult(passed=True)."""
        company = _make_company()
        effects = _all_pass_effects()
        # scalar_one para critério 10 (categories) deve retornar >=2
        effects[6].scalar_one = MagicMock(return_value=3)
        session = _make_session(company, execute_side_effects=effects)

        result = await evaluate_ca_dossier_readiness(company.id, session)

        assert isinstance(result, ReadinessResult)
        assert result.passed is True
        assert result.partial_reasons == []

    @pytest.mark.asyncio
    async def test_ca_missing_entity_number_returns_partial(self):
        """company.entity_number=None → critério 5 falha sem query, resultado PARTIAL."""
        company = _make_company(entity_number=None)
        # Mesmo que outras queries passem, entity_number falha no Python
        effects = [
            _row_exists(),   # 1. legal_name evidence
            _row_exists(),   # 2. principal_address
            _row_exists(),   # 3. email evidence
            _row_exists(),   # 4. phone evidence
            # 5. entity_number — sem query (falha no Python)
            _row_exists(),   # 6. SI document
            _row_exists(),   # 8. html_text snapshot
            _row_exists(),   # 10. categories
            _row_missing(),  # 11. contradictions (OK)
            MagicMock(),     # UPDATE PARTIAL
            MagicMock(),     # INSERT event
        ]
        effects[6].scalar_one = MagicMock(return_value=3)
        session = _make_session(company, execute_side_effects=effects)

        result = await evaluate_ca_dossier_readiness(company.id, session)

        assert result.passed is False
        assert "entity_number ausente" in result.partial_reasons

    @pytest.mark.asyncio
    async def test_ca_contradiction_returns_partial(self):
        """Company com evidência CONTRADICTS → partial com 'contradição ativa detectada'."""
        company = _make_company()
        effects = [
            _row_exists(),   # 1. legal_name evidence
            _row_exists(),   # 2. principal_address
            _row_exists(),   # 3. email evidence
            _row_exists(),   # 4. phone evidence
            _row_exists(),   # 6. SI document
            _row_exists(),   # 8. html_text snapshot
            _row_exists(),   # 10. categories (scalar_one=3)
            _row_exists(),   # 11. contradições → EXISTS = falha
            MagicMock(),     # UPDATE PARTIAL
            MagicMock(),     # INSERT event
        ]
        effects[6].scalar_one = MagicMock(return_value=3)
        session = _make_session(company, execute_side_effects=effects)

        result = await evaluate_ca_dossier_readiness(company.id, session)

        assert result.passed is False
        assert "contradição ativa detectada" in result.partial_reasons

    @pytest.mark.asyncio
    async def test_ca_low_match_score_returns_partial(self):
        """match_score < 60 → partial com 'match_score insuficiente'."""
        company = _make_company(match_score=50)
        effects = [
            _row_exists(),   # 1. legal_name evidence
            _row_exists(),   # 2. principal_address
            _row_exists(),   # 3. email evidence
            _row_exists(),   # 4. phone evidence
            _row_exists(),   # 6. SI document
            _row_exists(),   # 8. html_text snapshot
            _row_exists(),   # 10. categories
            _row_missing(),  # 11. contradições (OK)
            MagicMock(),     # UPDATE PARTIAL
            MagicMock(),     # INSERT event
        ]
        effects[6].scalar_one = MagicMock(return_value=3)
        session = _make_session(company, execute_side_effects=effects)

        result = await evaluate_ca_dossier_readiness(company.id, session)

        assert result.passed is False
        assert "match_score insuficiente (<60)" in result.partial_reasons

    @pytest.mark.asyncio
    async def test_ca_missing_si_document_returns_partial(self):
        """Sem documento SI válido → partial."""
        company = _make_company()
        effects = [
            _row_exists(),   # 1. legal_name evidence
            _row_exists(),   # 2. principal_address
            _row_exists(),   # 3. email evidence
            _row_exists(),   # 4. phone evidence
            _row_missing(),  # 6. SI document → FALTA
            _row_exists(),   # 8. html_text snapshot
            _row_exists(),   # 10. categories
            _row_missing(),  # 11. contradições (OK)
            MagicMock(),     # UPDATE PARTIAL
            MagicMock(),     # INSERT event
        ]
        effects[6].scalar_one = MagicMock(return_value=3)
        session = _make_session(company, execute_side_effects=effects)

        result = await evaluate_ca_dossier_readiness(company.id, session)

        assert result.passed is False
        assert "documento SI-COMPLETE ausente ou inválido" in result.partial_reasons

    @pytest.mark.asyncio
    async def test_readiness_result_is_dataclass(self):
        """ReadinessResult deve ser instanciável com passed e partial_reasons."""
        r = ReadinessResult(passed=True)
        assert r.passed is True
        assert r.partial_reasons == []

        r2 = ReadinessResult(passed=False, partial_reasons=["foo"])
        assert r2.partial_reasons == ["foo"]
