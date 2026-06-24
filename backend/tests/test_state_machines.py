"""Testes — máquinas de estado (sem DB: mocks)."""
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.state_machines import (
    DOSSIER_TRANSITIONS,
    USAGE_TRANSITIONS,
    VERIFICATION_ADMIN_ONLY,
    VERIFICATION_TRANSITIONS,
    AdminRequiredError,
    InvalidTransitionError,
    transition_dossier,
    transition_usage,
    transition_verification,
)


def _make_company(**kwargs):
    defaults = {
        "id": uuid.uuid4(),
        "source_state": "CA",
        "readiness_locked": False,
        "readiness_policy": "CA_STANDARD",
        "legacy_read_only": False,
        "dossier_status": "DISCOVERED",
        "usage_status": "AVAILABLE",
        "verification_status": "NOT_STARTED",
    }
    defaults.update(kwargs)
    return MagicMock(**defaults)


def _make_session(company):
    session = AsyncMock()
    session.get = AsyncMock(return_value=company)
    session.execute = AsyncMock(return_value=None)
    return session


# ─── Tabelas de transição ─────────────────────────────────────────────────────


class TestTransitionTables:
    def test_dossier_transitions_complete(self):
        assert "MATCHED" in DOSSIER_TRANSITIONS["DISCOVERED"]
        assert "DOSSIER_BUILDING" in DOSSIER_TRANSITIONS["MATCHED"]
        assert "READY" in DOSSIER_TRANSITIONS["DOSSIER_BUILDING"]
        assert "PARTIAL" in DOSSIER_TRANSITIONS["DOSSIER_BUILDING"]
        assert "DOSSIER_BUILDING" in DOSSIER_TRANSITIONS["PARTIAL"]
        assert "PARTIAL" in DOSSIER_TRANSITIONS["READY"]

    def test_dossier_illegal_transitions(self):
        assert "READY" not in DOSSIER_TRANSITIONS.get("MATCHED", frozenset())
        assert "READY" not in DOSSIER_TRANSITIONS.get("PARTIAL", frozenset())
        assert "DISCOVERED" not in DOSSIER_TRANSITIONS.get("READY", frozenset())
        assert "MATCHED" not in DOSSIER_TRANSITIONS.get("PARTIAL", frozenset())

    def test_usage_finalized_is_terminal(self):
        assert USAGE_TRANSITIONS["FINALIZED"] == frozenset()

    def test_verification_admin_only_pairs(self):
        assert ("PASSED", "IN_PROGRESS") in VERIFICATION_ADMIN_ONLY
        assert ("FAILED", "IN_PROGRESS") in VERIFICATION_ADMIN_ONLY


# ─── Dossier ──────────────────────────────────────────────────────────────────


class TestDossierTransition:
    @pytest.mark.asyncio
    async def test_valid_discovered_to_matched(self):
        company = _make_company(dossier_status="DISCOVERED")
        session = _make_session(company)
        await transition_dossier(company.id, "MATCHED", session)

    @pytest.mark.asyncio
    async def test_invalid_discovered_to_ready(self):
        company = _make_company(dossier_status="DISCOVERED")
        session = _make_session(company)
        with pytest.raises(InvalidTransitionError, match="DISCOVERED→READY"):
            await transition_dossier(company.id, "READY", session)

    @pytest.mark.asyncio
    async def test_invalid_matched_to_partial(self):
        company = _make_company(dossier_status="MATCHED")
        session = _make_session(company)
        with pytest.raises(InvalidTransitionError, match="MATCHED→PARTIAL"):
            await transition_dossier(company.id, "PARTIAL", session)

    @pytest.mark.asyncio
    async def test_valid_partial_to_building(self):
        company = _make_company(dossier_status="PARTIAL")
        session = _make_session(company)
        await transition_dossier(company.id, "DOSSIER_BUILDING", session)

    @pytest.mark.asyncio
    async def test_ready_to_partial_requires_reason(self):
        company = _make_company(dossier_status="READY")
        session = _make_session(company)
        with pytest.raises(InvalidTransitionError, match="reason"):
            await transition_dossier(company.id, "PARTIAL", session, reason=None)

    @pytest.mark.asyncio
    async def test_ready_to_partial_with_reason_passes(self):
        company = _make_company(dossier_status="READY")
        session = _make_session(company)
        await transition_dossier(
            company.id, "PARTIAL", session, reason="revalidação anual"
        )

    @pytest.mark.asyncio
    async def test_fl_company_blocked_by_legacy_trigger(self):
        """Simula o trigger de banco rejeitando UPDATE em company FL legacy."""
        company = _make_company(
            source_state="FL",
            readiness_locked=True,
            legacy_read_only=True,
            dossier_status="DISCOVERED",
        )
        session = _make_session(company)
        # O trigger protect_legacy dispara no banco e lança exceção
        session.execute = AsyncMock(
            side_effect=Exception("legacy_read_only imutável em company legacy_read_only")
        )
        with pytest.raises(Exception, match="legacy_read_only"):
            await transition_dossier(company.id, "MATCHED", session)


# ─── Usage ────────────────────────────────────────────────────────────────────


class TestUsageTransition:
    @pytest.mark.asyncio
    async def test_valid_available_to_in_use(self):
        company = _make_company(usage_status="AVAILABLE")
        session = _make_session(company)
        await transition_usage(company.id, "IN_USE", session)

    @pytest.mark.asyncio
    async def test_invalid_available_to_finalized(self):
        company = _make_company(usage_status="AVAILABLE")
        session = _make_session(company)
        with pytest.raises(InvalidTransitionError, match="AVAILABLE→FINALIZED"):
            await transition_usage(company.id, "FINALIZED", session)

    @pytest.mark.asyncio
    async def test_finalized_is_terminal(self):
        company = _make_company(usage_status="FINALIZED")
        session = _make_session(company)
        with pytest.raises(InvalidTransitionError, match="FINALIZED→"):
            await transition_usage(company.id, "AVAILABLE", session)

    @pytest.mark.asyncio
    async def test_valid_in_use_to_finalized(self):
        company = _make_company(usage_status="IN_USE")
        session = _make_session(company)
        await transition_usage(company.id, "FINALIZED", session)


# ─── Verification ─────────────────────────────────────────────────────────────


class TestVerificationTransition:
    @pytest.mark.asyncio
    async def test_valid_not_started_to_in_progress(self):
        company = _make_company(verification_status="NOT_STARTED")
        session = _make_session(company)
        await transition_verification(company.id, "IN_PROGRESS", session)

    @pytest.mark.asyncio
    async def test_valid_in_progress_to_passed(self):
        company = _make_company(verification_status="IN_PROGRESS")
        session = _make_session(company)
        await transition_verification(company.id, "PASSED", session)

    @pytest.mark.asyncio
    async def test_valid_in_progress_to_failed(self):
        company = _make_company(verification_status="IN_PROGRESS")
        session = _make_session(company)
        await transition_verification(company.id, "FAILED", session)

    @pytest.mark.asyncio
    async def test_passed_to_in_progress_requires_admin(self):
        company = _make_company(verification_status="PASSED")
        session = _make_session(company)
        with pytest.raises(AdminRequiredError):
            await transition_verification(
                company.id, "IN_PROGRESS", session, is_admin=False
            )

    @pytest.mark.asyncio
    async def test_passed_to_in_progress_as_admin_passes(self):
        company = _make_company(verification_status="PASSED")
        session = _make_session(company)
        await transition_verification(
            company.id, "IN_PROGRESS", session, is_admin=True
        )

    @pytest.mark.asyncio
    async def test_failed_to_in_progress_requires_admin(self):
        company = _make_company(verification_status="FAILED")
        session = _make_session(company)
        with pytest.raises(AdminRequiredError):
            await transition_verification(
                company.id, "IN_PROGRESS", session, is_admin=False
            )

    @pytest.mark.asyncio
    async def test_invalid_not_started_to_passed(self):
        company = _make_company(verification_status="NOT_STARTED")
        session = _make_session(company)
        with pytest.raises(InvalidTransitionError, match="NOT_STARTED→PASSED"):
            await transition_verification(company.id, "PASSED", session)

    def test_fl_never_in_stock(self):
        """FL não aparece no estoque: ix_companies_available_stock filtra source_state='CA'.

        Este teste documenta a invariante. O índice partial no banco garante a regra;
        aqui verificamos que a definição de estoque usa CA explicitamente.
        """
        from app.domain.models import Company
        idx = next(
            (i for i in Company.__table_args__ if getattr(i, "name", None) == "ix_companies_available_stock"),
            None,
        )
        assert idx is not None, "Índice ix_companies_available_stock não encontrado"
        where_clause = str(idx.dialect_kwargs.get("postgresql_where", ""))
        assert "CA" in where_clause, "Índice não filtra source_state='CA'"
