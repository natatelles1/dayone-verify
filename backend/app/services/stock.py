"""Stock availability — CA companies prontas para uso."""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import text


async def count_available_stock(session: AsyncSession, *, niche: str | None = None) -> int:
    """Conta companies CA disponíveis (dossier=READY, usage=AVAILABLE, verification=NOT_STARTED).

    Usa o índice ix_companies_available_stock.
    """
    q = text("""
        SELECT COUNT(*) FROM companies
        WHERE source_state = 'CA'
          AND dossier_status = 'READY'
          AND usage_status = 'AVAILABLE'
          AND verification_status = 'NOT_STARTED'
          AND (:niche IS NULL OR niche = :niche)
    """)
    result = await session.execute(q, {"niche": niche})
    return result.scalar_one()


async def get_available_companies(
    session: AsyncSession,
    *,
    niche: str | None = None,
    limit: int = 10,
) -> list[dict]:
    """Retorna companies disponíveis ordenadas por niche, created_at (usa o índice)."""
    q = text("""
        SELECT id, legal_name, entity_number, niche, created_at
        FROM companies
        WHERE source_state = 'CA'
          AND dossier_status = 'READY'
          AND usage_status = 'AVAILABLE'
          AND verification_status = 'NOT_STARTED'
          AND (:niche IS NULL OR niche = :niche)
        ORDER BY niche NULLS LAST, created_at, id
        LIMIT :limit
    """)
    result = await session.execute(q, {"niche": niche, "limit": limit})
    return [dict(r._mapping) for r in result]
