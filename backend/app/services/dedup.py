"""Dedup service — auto-merge de companies com entity_number duplicado.

Regras permitidas de auto-merge (source_state+entity_number idênticos):
  RULE_SAME_NAME        — legal_name normalizado igual
  RULE_SAME_EIN         — legacy_ein igual
  RULE_SAME_ENTITY_NUMBER — fallback; o índice único deveria ter impedido

Proibições: merge por nome só, endereço só, agent address ou suíte diferente.
Cross-state é bloqueado pelo trigger trg_company_merges_check_state.
FL é rejeitada na camada Python antes de qualquer SQL.
"""
from __future__ import annotations

import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import text

RULE_SAME_NAME = "RULE_SAME_NAME"
RULE_SAME_EIN = "RULE_SAME_EIN"
RULE_SAME_ENTITY_NUMBER = "RULE_SAME_ENTITY_NUMBER"


class MergeNotAllowedError(ValueError):
    pass


def _normalize_name(name: str | None) -> str:
    if name is None:
        return ""
    return name.upper().strip()


async def find_dedup_candidates(
    session: AsyncSession,
) -> list[tuple[uuid.UUID, uuid.UUID, str]]:
    """Retorna (canonical_id, merged_id, rule) para companies com entity_number duplicado.

    Ignora FL. canonical é o de menor id (UUID lexicográfico) para determinismo.
    """
    rows = await session.execute(
        text("""
            SELECT
                a.id AS canonical_id,
                b.id AS merged_id,
                a.legal_name AS a_name,
                b.legal_name AS b_name,
                a.legacy_ein AS a_ein,
                b.legacy_ein AS b_ein
            FROM companies a
            JOIN companies b
              ON a.source_state = b.source_state
             AND upper(btrim(a.entity_number)) = upper(btrim(b.entity_number))
             AND a.id < b.id
            WHERE a.entity_number IS NOT NULL
              AND btrim(a.entity_number) <> ''
              AND a.source_state <> 'FL'
        """)
    )
    results: list[tuple[uuid.UUID, uuid.UUID, str]] = []
    for row in rows.fetchall():
        canonical_id = row.canonical_id
        merged_id = row.merged_id
        if _normalize_name(row.a_name) == _normalize_name(row.b_name) and row.a_name:
            rule = RULE_SAME_NAME
        elif row.a_ein and row.a_ein == row.b_ein:
            rule = RULE_SAME_EIN
        else:
            rule = RULE_SAME_ENTITY_NUMBER
        results.append((canonical_id, merged_id, rule))
    return results


async def merge_companies(
    canonical_id: uuid.UUID,
    merged_id: uuid.UUID,
    merge_reason: str,
    merge_rule: str,
    session: AsyncSession,
    *,
    actor: str | None = None,
) -> None:
    """Funde merged_id em canonical_id via merge_companies_fn.

    Verifica source_state no Python antes de chamar o SQL para falhar rápido
    com mensagem legível, e também para impedir que FL chegue ao banco.
    """
    if canonical_id == merged_id:
        raise MergeNotAllowedError(f"Self-merge proibido: {canonical_id}")

    row = await session.execute(
        text(
            "SELECT id, source_state FROM companies WHERE id = ANY(:ids)"
        ),
        {"ids": [canonical_id, merged_id]},
    )
    companies = {r.id: r.source_state for r in row.fetchall()}

    for cid, state in companies.items():
        if state == "FL":
            raise MergeNotAllowedError(
                f"Merge proibido: company {cid} é FL (source_state='FL'). "
                "Empresas FL são read-only."
            )

    await session.execute(
        text(
            "SELECT merge_companies_fn(:c, :m, :r, :rl, :a)"
        ),
        {
            "c": canonical_id,
            "m": merged_id,
            "r": merge_reason,
            "rl": merge_rule,
            "a": actor,
        },
    )


async def auto_dedup(session: AsyncSession, *, actor: str = "AUTO_DEDUP") -> int:
    """Executa auto-merge para todos os candidatos encontrados.

    Retorna o número de merges realizados.
    """
    candidates = await find_dedup_candidates(session)
    merged = 0
    for canonical_id, merged_id, rule in candidates:
        await merge_companies(
            canonical_id,
            merged_id,
            merge_reason=f"Auto-dedup: {rule}",
            merge_rule=rule,
            session=session,
            actor=actor,
        )
        merged += 1
    return merged
