"""Testes — importador FL (preflight sem DB; import com mock de sessão)."""
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.fl_import.importer import (
    ImportReport,
    ParsedRecord,
    PreflightReport,
    load_fl_records,
    preflight,
)

DATA_JSON = Path(__file__).parents[2] / "data.json"

# ─── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def raw_records():
    """Carrega os 106 registros do data.json uma vez por módulo."""
    return load_fl_records(DATA_JSON)


@pytest.fixture(scope="module")
def fl_preflight(raw_records):
    """Roda o preflight completo (sem DB) uma vez por módulo."""
    return preflight(raw_records)


# ─── load_fl_records ──────────────────────────────────────────────────────────


class TestLoadFlRecords:
    def test_loads_106_records(self, raw_records):
        assert len(raw_records) == 106

    def test_required_fields_present(self, raw_records):
        required = {"nome", "ein", "email", "documento", "endereco"}
        for rec in raw_records:
            missing = required - set(rec.keys())
            assert not missing, f"Campos ausentes em {rec.get('nome')}: {missing}"


# ─── preflight ────────────────────────────────────────────────────────────────


class TestPreflight:
    def test_all_106_parse_ok(self, fl_preflight: PreflightReport):
        """Gate principal: todos os 106 registros devem parsear sem erro."""
        if fl_preflight.errors:
            msgs = [f"[{e.index}] {e.nome}: {e.field} — {e.message}"
                    for e in fl_preflight.errors[:5]]
            pytest.fail(
                f"Preflight falhou em {len(fl_preflight.errors)} registros:\n"
                + "\n".join(msgs)
            )
        assert fl_preflight.passed
        assert len(fl_preflight.ok) == 106

    def test_flal_count(self, fl_preflight: PreflightReport):
        assert fl_preflight.flal_count == 103

    def test_forl_count(self, fl_preflight: PreflightReport):
        assert fl_preflight.forl_count == 3

    def test_flal_plus_forl_equals_106(self, fl_preflight: PreflightReport):
        assert fl_preflight.flal_count + fl_preflight.forl_count == 106

    def test_suite_cases_count(self, fl_preflight: PreflightReport):
        """7 endereços com indicadores de suíte no data.json:
        STE inline (1), STE separado (1), SUITE separado (1), PH- separado (1),
        APT inline (1), # inline (2 — #1013 e #202).
        """
        assert len(fl_preflight.suite_cases) == 7, (
            f"Esperado 7 suítes, encontrado {len(fl_preflight.suite_cases)}: "
            f"{fl_preflight.suite_cases}"
        )

    def test_trailing_fl_cases_count(self, fl_preflight: PreflightReport):
        """30 endereços com trailing ', FL' no data.json."""
        assert len(fl_preflight.trailing_fl_cases) == 30, (
            f"Esperado 30 trailing-FL, encontrado {len(fl_preflight.trailing_fl_cases)}"
        )

    def test_all_entity_numbers_uppercase(self, fl_preflight: PreflightReport):
        for r in fl_preflight.ok:
            assert r.entity.entity_number == r.entity.entity_number.upper()

    def test_all_entity_numbers_unique(self, fl_preflight: PreflightReport):
        nums = [r.entity.entity_number for r in fl_preflight.ok]
        assert len(nums) == len(set(nums)), "entity_numbers duplicados encontrados"

    def test_all_eins_unique(self, fl_preflight: PreflightReport):
        eins = [r.ein for r in fl_preflight.ok]
        assert len(eins) == len(set(eins)), "EINs duplicados encontrados"

    def test_all_document_urls_unique(self, fl_preflight: PreflightReport):
        urls = [r.documento_url for r in fl_preflight.ok]
        assert len(urls) == len(set(urls)), "URLs de documento duplicadas"

    def test_all_address_hashes_non_empty(self, fl_preflight: PreflightReport):
        for r in fl_preflight.ok:
            assert r.address.address_hash, f"Hash vazio para {r.nome}"
            assert len(r.address.address_hash) == 64

    def test_phones_none_or_e164(self, fl_preflight: PreflightReport):
        """76 phones preenchidos devem ser E.164 (+1XXXXXXXXXX), 30 None."""
        filled = [r for r in fl_preflight.ok if r.phone_e164 is not None]
        empty = [r for r in fl_preflight.ok if r.phone_e164 is None]
        assert len(filled) == 76, f"Esperado 76 phones, encontrado {len(filled)}"
        assert len(empty) == 30, f"Esperado 30 sem phone, encontrado {len(empty)}"
        for r in filled:
            assert r.phone_e164.startswith("+1"), f"Phone não é +1: {r.phone_e164}"
            assert not r.phone_e164.startswith("+11"), f"Double country code: {r.phone_e164}"
            assert len(r.phone_e164) == 12, f"E.164 inválido: {r.phone_e164}"

    def test_csv_content_has_header_and_106_rows(self, fl_preflight: PreflightReport):
        lines = fl_preflight.csv_content.strip().splitlines()
        assert len(lines) == 107, f"CSV deve ter 1 header + 106 linhas, tem {len(lines)}"
        assert "entity_number" in lines[0]
        assert "nome" in lines[0]

    def test_fl_legacy_flags_set(self, fl_preflight: PreflightReport):
        """Valida que todos os parsed records têm prefixo FL e endereço US.

        state pode ser qualquer estado US: empresas registradas na FL
        podem ter endereço principal em outro estado (ex: TCG ACCOUNTING → MN).
        """
        for r in fl_preflight.ok:
            assert r.entity.prefix in ("flal", "forl")
            assert r.address.country == "US"
            assert len(r.address.state) == 2
            assert r.address.state == r.address.state.upper()


# ─── preflight — caso de erro (registros sintéticos) ──────────────────────────


class TestPreflightErrors:
    def test_bad_aggregate_id_caught(self):
        bad_records = [{
            "nome": "TESTE RUIM",
            "ein": "00-0000000",
            "email": "test@test.com",
            "telefone": "",
            "documento": "https://search.sunbiz.org/?aggregateId=BAD&transactionId=BAD",
            "endereco": "100 MAIN ST, MIAMI, FL 33101",
        }]
        report = preflight(bad_records)
        assert not report.passed
        assert len(report.errors) >= 1
        assert any("aggregateId" in e.message or "entity_number" in e.field
                   for e in report.errors)

    def test_bad_address_caught(self):
        bad_records = [{
            "nome": "ENDEREÇO RUIM",
            "ein": "00-0000001",
            "email": "test@test.com",
            "telefone": "",
            "documento": (
                "https://search.sunbiz.org/?aggregateId=flal-l99999999999-"
                "aabbccdd-0011-2233-4455-6677889900aa"
                "&transactionId=l99999999999"
            ),
            "endereco": "SÓ UMA PARTE",
        }]
        report = preflight(bad_records)
        assert not report.passed
        assert any(e.field == "endereco" for e in report.errors)

    def test_duplicate_ein_caught(self):
        doc_template = (
            "https://search.sunbiz.org/?aggregateId=flal-l{num}-"
            "aabbccdd-0011-2233-4455-6677889900aa"
            "&transactionId=l{num}"
        )
        records = [
            {
                "nome": f"EMPRESA {i}",
                "ein": "00-DUPLICATE",          # mesmo EIN nos dois
                "email": f"emp{i}@test.com",
                "telefone": "",
                "documento": doc_template.format(num=f"1{i:010d}"),
                "endereco": "100 MAIN ST, MIAMI, FL 33101",
            }
            for i in range(2)
        ]
        report = preflight(records)
        assert not report.passed
        assert any(e.field == "ein" for e in report.errors)
