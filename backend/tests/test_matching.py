"""Testes matching — score_candidate (8 casos)."""
import pytest

from app.services.matching import MatchCandidate, MatchResult, score_candidate


def _score(candidate: MatchCandidate, **db_kwargs) -> MatchResult:
    defaults = {
        "db_entity_number": None,
        "db_legal_name": None,
        "db_ein": None,
        "db_address": None,
    }
    defaults.update(db_kwargs)
    return score_candidate(candidate, **defaults)


class TestScoreCandidate:
    def test_entity_number_exact_match_is_anchor(self):
        """entity_number exato → anchor='entity_number', matched=True."""
        result = _score(
            MatchCandidate(entity_number="12345678"),
            db_entity_number="12345678",
        )
        assert result.anchor == "entity_number"
        assert result.matched is True
        assert result.breakdown["entity_number"] == 60.0

    def test_entity_number_mismatch_no_anchor(self):
        result = _score(
            MatchCandidate(entity_number="12345678"),
            db_entity_number="99999999",
        )
        assert result.anchor is None
        assert result.breakdown["entity_number"] == 0.0

    def test_name_and_ein_anchor(self):
        """Nome ≥ 80 + EIN exato → anchor='name_ein', matched=True."""
        result = _score(
            MatchCandidate(
                legal_name="Acme Accounting LLC",
                legacy_ein="12-3456789",
            ),
            db_legal_name="Acme Accounting LLC",
            db_ein="12-3456789",
        )
        assert result.anchor == "name_ein"
        assert result.matched is True

    def test_name_only_never_matches(self):
        """Nome excelente SEM entity_number nem EIN → matched=False."""
        result = _score(
            MatchCandidate(legal_name="Perfect Name LLC"),
            db_legal_name="Perfect Name LLC",
        )
        # Sem âncora, e name_only=True → matched deve ser False
        assert result.matched is False

    def test_address_only_never_matches(self):
        """Endereço SEM entity_number, nome ou EIN → matched=False."""
        result = _score(
            MatchCandidate(address_line="123 Main St"),
            db_address="123 Main St Orlando FL",
        )
        assert result.matched is False

    def test_score_below_threshold_not_matched(self):
        """Score calculado abaixo de 60 sem âncora → matched=False."""
        result = _score(
            MatchCandidate(legal_name="Totally Different Corp"),
            db_legal_name="Acme Accounting LLC",
        )
        assert result.score < 60
        assert result.matched is False

    def test_entity_number_normalized_case_insensitive(self):
        """entity_number é comparado após _norm → maiúsc./minúsc. não importam."""
        result = _score(
            MatchCandidate(entity_number="ABC-123"),
            db_entity_number="abc 123",
        )
        # Após _norm: "abc 123" == "abc 123"
        assert result.anchor == "entity_number"
        assert result.matched is True

    def test_breakdown_keys_present(self):
        """breakdown sempre contém todas as dimensões avaliadas."""
        result = _score(
            MatchCandidate(entity_number="X1", legal_name="Foo Bar LLC"),
            db_entity_number="X1",
            db_legal_name="Foo Bar LLC",
        )
        assert "entity_number" in result.breakdown
        assert "legal_name" in result.breakdown
        assert "legacy_ein" in result.breakdown
        assert "address" in result.breakdown
