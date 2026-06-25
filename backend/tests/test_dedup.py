"""Testes — dedup service (sem DB: mocks)."""
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.dedup import (
    MergeNotAllowedError,
    _normalize_name,
    merge_companies,
)


def _mock_session_with_states(*states):
    """Cria um session mock onde execute retorna rows com (id, source_state) para cada state."""
    rows = []
    ids = []
    for state in states:
        cid = uuid.uuid4()
        ids.append(cid)
        row = MagicMock()
        row.id = cid
        row.source_state = state
        rows.append(row)

    result = MagicMock()
    result.fetchall = MagicMock(return_value=rows)
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)
    return session, ids


class TestNormalizeName:
    def test_uppercases_and_strips(self):
        assert _normalize_name("  Acme LLC  ") == "ACME LLC"

    def test_none_returns_empty(self):
        assert _normalize_name(None) == ""

    def test_already_upper(self):
        assert _normalize_name("ACME LLC") == "ACME LLC"

    def test_empty_string(self):
        assert _normalize_name("") == ""


class TestMergeCompanies:
    @pytest.mark.asyncio
    async def test_merge_fl_canonical_raises(self):
        session, ids = _mock_session_with_states("FL", "CA")
        canonical_id, merged_id = ids
        with pytest.raises(MergeNotAllowedError, match="FL"):
            await merge_companies(
                canonical_id, merged_id, "test", "RULE_SAME_EIN", session
            )

    @pytest.mark.asyncio
    async def test_merge_fl_merged_raises(self):
        session, ids = _mock_session_with_states("CA", "FL")
        canonical_id, merged_id = ids
        with pytest.raises(MergeNotAllowedError, match="FL"):
            await merge_companies(
                canonical_id, merged_id, "test", "RULE_SAME_EIN", session
            )

    @pytest.mark.asyncio
    async def test_merge_ca_calls_sql_function(self):
        session, ids = _mock_session_with_states("CA", "CA")
        canonical_id, merged_id = ids

        # Segunda chamada ao execute (a da função SQL) não retorna nada especial
        call_args_list = []
        original_execute = session.execute

        async def capture_execute(stmt, params=None):
            call_args_list.append((str(stmt), params))
            return MagicMock(fetchall=MagicMock(return_value=[
                MagicMock(id=canonical_id, source_state="CA"),
                MagicMock(id=merged_id, source_state="CA"),
            ]))

        session.execute = capture_execute

        await merge_companies(
            canonical_id, merged_id, "dedup automático", "RULE_SAME_NAME", session
        )

        sql_calls = " ".join(sql for sql, _ in call_args_list)
        assert "merge_companies_fn" in sql_calls

    @pytest.mark.asyncio
    async def test_merge_requires_different_ids(self):
        cid = uuid.uuid4()
        session = AsyncMock()
        with pytest.raises(MergeNotAllowedError, match="Self-merge"):
            await merge_companies(cid, cid, "test", "RULE_SAME_EIN", session)
