"""Testes unitários — parsers FL legacy (sem DB, sem I/O)."""
import pytest

from app.services.fl_import.parsers import (
    ParsedAddress,
    ParsedEntityNumber,
    normalize_phone,
    parse_entity_number,
    parse_fl_address,
)

# ─── parse_entity_number ───────────────────────────────────────────────────────

_SUNBIZ_FLAL = (
    "https://search.sunbiz.org/Inquiry/CorporationSearch/SearchResultDetail"
    "?inquiryType=EntityName&inquiryDirective=DetailInquiry"
    "&aggregateId=flal-l23000502530-1a2b3c4d-5e6f-7a8b-9c0d-1e2f3a4b5c6d"
    "&transactionId=l23000502530"
    "&action=DirectionLinks"
)

_SUNBIZ_FORL = (
    "https://search.sunbiz.org/Inquiry/CorporationSearch/SearchResultDetail"
    "?inquiryType=EntityName&inquiryDirective=DetailInquiry"
    "&aggregateId=forl-m20000001234-aabbccdd-0011-2233-4455-6677889900aa"
    "&transactionId=m20000001234"
    "&action=DirectionLinks"
)


class TestParseEntityNumber:
    def test_flal_prefix(self):
        result = parse_entity_number(_SUNBIZ_FLAL)
        assert isinstance(result, ParsedEntityNumber)
        assert result.prefix == "flal"
        assert result.entity_number == "L23000502530"
        assert result.entity_number == result.entity_number.upper()

    def test_forl_prefix(self):
        result = parse_entity_number(_SUNBIZ_FORL)
        assert result.prefix == "forl"
        assert result.entity_number == "M20000001234"

    def test_entity_number_uppercase(self):
        result = parse_entity_number(_SUNBIZ_FLAL)
        assert result.entity_number == result.entity_number.upper()

    def test_aggregate_id_preserved(self):
        result = parse_entity_number(_SUNBIZ_FLAL)
        assert "flal-l23000502530" in result.aggregate_id

    def test_missing_aggregate_id_raises(self):
        url = (
            "https://search.sunbiz.org/Inquiry/CorporationSearch/SearchResultDetail"
            "?transactionId=l23000502530"
        )
        with pytest.raises(ValueError, match="aggregateId ausente"):
            parse_entity_number(url)

    def test_transaction_id_mismatch_raises(self):
        url = (
            "https://search.sunbiz.org/Inquiry/CorporationSearch/SearchResultDetail"
            "?aggregateId=flal-l23000502530-1a2b3c4d-5e6f-7a8b-9c0d-1e2f3a4b5c6d"
            "&transactionId=l99999999999"  # ← número errado
        )
        with pytest.raises(ValueError, match="transactionId"):
            parse_entity_number(url)

    def test_empty_url_raises(self):
        with pytest.raises(ValueError):
            parse_entity_number("")

    def test_invalid_aggregate_id_pattern_raises(self):
        url = (
            "https://search.sunbiz.org/?aggregateId=invalid-pattern"
            "&transactionId=invalid"
        )
        with pytest.raises(ValueError, match="aggregateId não corresponde"):
            parse_entity_number(url)


# ─── parse_fl_address ──────────────────────────────────────────────────────────


class TestParseFlAddress:
    def test_standard_3part(self):
        addr = parse_fl_address("1000 MAIN ST, MIAMI, FL 33101")
        assert addr.street_line1 == "1000 MAIN ST"
        assert addr.city == "MIAMI"
        assert addr.state == "FL"
        assert addr.zip_code == "33101"
        assert addr.suite is None
        assert addr.country == "US"

    def test_trailing_fl_stripped(self):
        """30 endereços no data.json têm trailing ', FL' — deve produzir o mesmo resultado."""
        with_trailing = parse_fl_address("1000 MAIN ST, MIAMI, FL 33101, FL")
        without_trailing = parse_fl_address("1000 MAIN ST, MIAMI, FL 33101")
        assert with_trailing.normalized == without_trailing.normalized
        assert with_trailing.address_hash == without_trailing.address_hash
        assert with_trailing.city == "MIAMI"
        assert with_trailing.zip_code == "33101"

    def test_double_comma_cleaned(self):
        """Endereço com double-comma: '6801 LAKE WORTH RD.,, GREENACRES, FL 33467'."""
        addr = parse_fl_address("6801 LAKE WORTH RD.,, GREENACRES, FL 33467")
        assert addr.street_line1 == "6801 LAKE WORTH RD."
        assert addr.city == "GREENACRES"
        assert addr.zip_code == "33467"
        assert addr.suite is None

    def test_suite_separate_segment_ste(self):
        """'700 S. ROSEMARY AVE, STE 204, WEST PALM BEACH, FL 33401'."""
        addr = parse_fl_address("700 S. ROSEMARY AVE, STE 204, WEST PALM BEACH, FL 33401")
        assert addr.street_line1 == "700 S. ROSEMARY AVE"
        assert addr.suite == "204"
        assert addr.city == "WEST PALM BEACH"

    def test_suite_separate_segment_suite(self):
        """'618 E SOUTH ST, SUITE 500, ORLANDO, FL 32801'."""
        addr = parse_fl_address("618 E SOUTH ST, SUITE 500, ORLANDO, FL 32801")
        assert addr.street_line1 == "618 E SOUTH ST"
        assert addr.suite == "500"
        assert addr.city == "ORLANDO"

    def test_ph_separate_segment(self):
        """'3475 S OCEAN BLVD., PH-7, PALM BEACH, FL 33480' — PH-7 preservado."""
        addr = parse_fl_address("3475 S OCEAN BLVD., PH-7, PALM BEACH, FL 33480")
        assert addr.street_line1 == "3475 S OCEAN BLVD."
        assert addr.suite == "PH-7"
        assert addr.city == "PALM BEACH"

    def test_suite_inline_ste(self):
        """'6751 NORTH FEDERAL HIGHWAY STE 400, BOCA RATON, FL 33487' — STE no street."""
        addr = parse_fl_address("6751 NORTH FEDERAL HIGHWAY STE 400, BOCA RATON, FL 33487")
        assert addr.street_line1 == "6751 NORTH FEDERAL HIGHWAY"
        assert addr.suite == "400"
        assert addr.city == "BOCA RATON"

    def test_apt_inline_plus_trailing_fl(self):
        """'8110 Cleary Blvd Apt 1116, Plantation, FL 33324, FL' — APT inline + trailing FL."""
        addr = parse_fl_address("8110 Cleary Blvd Apt 1116, Plantation, FL 33324, FL")
        assert addr.suite == "1116"
        assert addr.city == "Plantation"
        assert addr.zip_code == "33324"
        # Street não deve conter "Apt 1116"
        assert "Apt" not in addr.street_line1
        assert "1116" not in addr.street_line1

    def test_address_hash_deterministic(self):
        """Mesmo endereço → mesmo hash (deterministico para idempotência)."""
        h1 = parse_fl_address("1000 MAIN ST, MIAMI, FL 33101").address_hash
        h2 = parse_fl_address("1000 MAIN ST, MIAMI, FL 33101").address_hash
        assert h1 == h2
        assert len(h1) == 64  # SHA-256 hex

    def test_trailing_fl_same_hash_as_without(self):
        """trailing ', FL' deve produzir o mesmo hash (mesma empresa, mesmo endereço)."""
        h1 = parse_fl_address("100 OAK AVE, TAMPA, FL 33602").address_hash
        h2 = parse_fl_address("100 OAK AVE, TAMPA, FL 33602, FL").address_hash
        assert h1 == h2

    def test_invalid_too_few_parts(self):
        with pytest.raises(ValueError, match="partes insuficientes"):
            parse_fl_address("ONLY ONE PART")

    def test_non_fl_state_accepted(self):
        """Parser aceita qualquer estado US: entidades FL podem ter endereço em outro estado."""
        addr = parse_fl_address("100 MAIN ST, MINNEAPOLIS, MN 55401")
        assert addr.state == "MN"
        assert addr.country == "US"

    def test_invalid_zip_format_within_state_segment(self):
        """Segmento final sem ZIP válido deve levantar ValueError."""
        with pytest.raises(ValueError):
            parse_fl_address("100 MAIN ST, MIAMI, FL ABCDE")


# ─── normalize_phone ───────────────────────────────────────────────────────────


class TestNormalizePhone:
    def test_none_returns_none(self):
        assert normalize_phone(None) is None

    def test_empty_returns_none(self):
        assert normalize_phone("") is None

    def test_e164_passthrough_11digits(self):
        """Data FL: todos os 76 phones já têm +1 (11 dígitos com '1')."""
        result = normalize_phone("+13055551234")
        assert result == "+13055551234"

    def test_11_digits_with_leading_1(self):
        result = normalize_phone("13055551234")
        assert result == "+13055551234"

    def test_10_digits_gets_plus1(self):
        result = normalize_phone("3055551234")
        assert result == "+13055551234"

    def test_formatted_with_dashes(self):
        result = normalize_phone("1-305-555-1234")
        assert result == "+13055551234"

    def test_formatted_with_parens(self):
        result = normalize_phone("(305) 555-1234")
        assert result == "+13055551234"

    def test_invalid_9_digits_raises(self):
        with pytest.raises(ValueError, match="dígitos"):
            normalize_phone("305555123")  # 9 dígitos

    def test_invalid_12_digits_raises(self):
        with pytest.raises(ValueError, match="dígitos"):
            normalize_phone("123056789012")  # 12 dígitos

    def test_never_produces_double_country_code(self):
        """Garante que '+11...' nunca é produzido (double country code)."""
        result = normalize_phone("+13055551234")
        assert not result.startswith("+11")
