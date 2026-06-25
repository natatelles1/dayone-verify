"""Entity matching — pontua candidatos e avança state machine DISCOVERED→MATCHED.

Âncoras obrigatórias:
  entity_number EXACT  →  match garantido (peso 60)
  OU name ≥ 80 + EIN match  →  match garantido

NUNCA fazer match só por nome ou só por endereço.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field

from rapidfuzz import fuzz
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.state_machines import transition_dossier

_WEIGHTS = {
    "entity_number": 60.0,
    "legal_name": 25.0,
    "legacy_ein": 10.0,
    "address": 5.0,
}

MATCH_THRESHOLD = 60.0

_NON_ALNUM = re.compile(r"[^a-z0-9\s]+")
_SPACES = re.compile(r"\s+")


def _norm(s: str | None) -> str:
    if not s:
        return ""
    s = s.lower()
    s = _NON_ALNUM.sub(" ", s)
    return _SPACES.sub(" ", s).strip()


@dataclass
class MatchCandidate:
    """Dados do candidato externo a comparar com a company no banco."""
    entity_number: str | None = None
    legal_name: str | None = None
    legacy_ein: str | None = None
    address_line: str | None = None


@dataclass
class MatchResult:
    score: float
    matched: bool
    anchor: str | None = None        # "entity_number" | "name_ein" | None
    breakdown: dict[str, float] = field(default_factory=dict)


def score_candidate(
    candidate: MatchCandidate,
    *,
    db_entity_number: str | None,
    db_legal_name: str | None,
    db_ein: str | None,
    db_address: str | None,
) -> MatchResult:
    """Pontua um candidato externo contra dados canônicos do banco.

    Retorna MatchResult com score 0–100 e indicação de âncora.
    """
    breakdown: dict[str, float] = {}
    score = 0.0
    anchor: str | None = None

    # entity_number: exact match (normalizado) → peso total
    cand_en = _norm(candidate.entity_number)
    db_en = _norm(db_entity_number)
    if cand_en and db_en:
        if cand_en == db_en:
            breakdown["entity_number"] = _WEIGHTS["entity_number"]
            score += _WEIGHTS["entity_number"]
            anchor = "entity_number"
        else:
            breakdown["entity_number"] = 0.0  # mismatch — sem partial em chave exata
    else:
        breakdown["entity_number"] = 0.0

    # legal_name: fuzzy via token_sort_ratio
    cand_ln = _norm(candidate.legal_name)
    db_ln = _norm(db_legal_name)
    if cand_ln and db_ln:
        name_sim = fuzz.token_sort_ratio(cand_ln, db_ln)
        name_contribution = (name_sim / 100.0) * _WEIGHTS["legal_name"]
        breakdown["legal_name"] = name_contribution
        score += name_contribution

        # Verificar âncora name+EIN
        if anchor is None and name_sim >= 80:
            cand_ein = _norm(candidate.legacy_ein)
            db_ein_n = _norm(db_ein)
            if cand_ein and db_ein_n and cand_ein == db_ein_n:
                anchor = "name_ein"
    else:
        breakdown["legal_name"] = 0.0

    # legacy_ein: exact match
    cand_ein = _norm(candidate.legacy_ein)
    db_ein_n = _norm(db_ein)
    if cand_ein and db_ein_n:
        if cand_ein == db_ein_n:
            breakdown["legacy_ein"] = _WEIGHTS["legacy_ein"]
            score += _WEIGHTS["legacy_ein"]
        else:
            breakdown["legacy_ein"] = 0.0
    else:
        breakdown["legacy_ein"] = 0.0

    # address: fuzzy, peso reduzido — nunca pode ser âncora sozinho
    cand_addr = _norm(candidate.address_line)
    db_addr = _norm(db_address)
    if cand_addr and db_addr:
        addr_sim = fuzz.partial_ratio(cand_addr, db_addr)
        addr_contribution = (addr_sim / 100.0) * _WEIGHTS["address"]
        breakdown["address"] = addr_contribution
        score += addr_contribution
    else:
        breakdown["address"] = 0.0

    # Regra de segurança: sem âncora e sem score suficiente = não match
    # Apenas nome ou apenas endereço nunca resultam em match.
    has_anchor = anchor is not None
    name_only = (
        breakdown.get("entity_number", 0.0) == 0.0
        and breakdown.get("legacy_ein", 0.0) == 0.0
        and breakdown.get("address", 0.0) == 0.0
    )
    address_only = (
        breakdown.get("entity_number", 0.0) == 0.0
        and breakdown.get("legacy_ein", 0.0) == 0.0
        and breakdown.get("legal_name", 0.0) == 0.0
    )

    matched = (
        has_anchor
        or (score >= MATCH_THRESHOLD and not name_only and not address_only)
    )

    return MatchResult(
        score=round(score, 2),
        matched=matched,
        anchor=anchor,
        breakdown=breakdown,
    )


async def match_and_advance(
    company_id: uuid.UUID,
    candidate: MatchCandidate,
    session: AsyncSession,
    *,
    db_entity_number: str | None,
    db_legal_name: str | None,
    db_ein: str | None,
    db_address: str | None,
    actor_id: str | None = None,
) -> MatchResult:
    """Pontua e, se matched, avança dossier_status de DISCOVERED→MATCHED.

    Persiste match_score na company.
    Raises InvalidTransitionError se a company não estiver em DISCOVERED.
    """
    from sqlalchemy.sql import text

    result = score_candidate(
        candidate,
        db_entity_number=db_entity_number,
        db_legal_name=db_legal_name,
        db_ein=db_ein,
        db_address=db_address,
    )

    # Persistir score independente de matched
    await session.execute(
        text("UPDATE companies SET match_score=:s WHERE id=:id"),
        {"s": result.score, "id": company_id},
    )

    if result.matched:
        await transition_dossier(
            company_id,
            "MATCHED",
            session,
            actor_type="SYSTEM",
            actor_id=actor_id,
            reason=f"match_and_advance: score={result.score}, anchor={result.anchor}",
        )

    return result
