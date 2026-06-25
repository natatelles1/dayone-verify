"""State machine transitions — Dossier, Usage, Verification."""
from __future__ import annotations

import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import text

from app.domain.models import Company

DOSSIER_TRANSITIONS: dict[str, frozenset[str]] = {
    "DISCOVERED": frozenset({"MATCHED"}),
    "MATCHED": frozenset({"DOSSIER_BUILDING"}),
    "DOSSIER_BUILDING": frozenset({"READY", "PARTIAL"}),
    "PARTIAL": frozenset({"DOSSIER_BUILDING"}),
    "READY": frozenset({"PARTIAL"}),
}

USAGE_TRANSITIONS: dict[str, frozenset[str]] = {
    "AVAILABLE": frozenset({"IN_USE"}),
    "IN_USE": frozenset({"FINALIZED"}),
    "FINALIZED": frozenset(),
}

VERIFICATION_TRANSITIONS: dict[str, frozenset[str]] = {
    "NOT_STARTED": frozenset({"IN_PROGRESS"}),
    "IN_PROGRESS": frozenset({"PASSED", "FAILED"}),
    "PASSED": frozenset({"IN_PROGRESS"}),
    "FAILED": frozenset({"IN_PROGRESS"}),
}

VERIFICATION_ADMIN_ONLY: frozenset[tuple[str, str]] = frozenset({
    ("PASSED", "IN_PROGRESS"),
    ("FAILED", "IN_PROGRESS"),
})


class InvalidTransitionError(ValueError):
    pass


class AdminRequiredError(ValueError):
    pass


async def _insert_event(
    session: AsyncSession,
    company_id: uuid.UUID,
    event_type: str,
    old_value: str,
    new_value: str,
    actor_type: str,
    actor_id: str | None,
    reason: str | None,
) -> None:
    await session.execute(
        text(
            "INSERT INTO company_events "
            "(company_id, event_type, old_value, new_value, actor_type, actor_id, reason) "
            "VALUES (:cid, :et, :ov::jsonb, :nv::jsonb, :at, :ai, :r)"
        ),
        {
            "cid": company_id,
            "et": event_type,
            "ov": f'"{old_value}"',
            "nv": f'"{new_value}"',
            "at": actor_type,
            "ai": actor_id,
            "r": reason,
        },
    )


async def transition_dossier(
    company_id: uuid.UUID,
    target: str,
    session: AsyncSession,
    *,
    actor_type: str = "SYSTEM",
    actor_id: str | None = None,
    reason: str | None = None,
) -> None:
    company = await session.get(Company, company_id)
    if company is None:
        raise InvalidTransitionError(f"Company {company_id} não encontrada")

    current = company.dossier_status
    allowed = DOSSIER_TRANSITIONS.get(current, frozenset())
    if target not in allowed:
        raise InvalidTransitionError(
            f"Transição dossier_status inválida: {current}→{target} (company {company_id})"
        )

    if current == "READY" and target == "PARTIAL" and not reason:
        raise InvalidTransitionError(
            "READY→PARTIAL requer reason não-vazio (revalidação auditável)"
        )

    await session.execute(
        text("UPDATE companies SET dossier_status=:s WHERE id=:id"),
        {"s": target, "id": company_id},
    )
    await _insert_event(
        session, company_id, "DOSSIER_STATUS_CHANGED",
        current, target, actor_type, actor_id, reason,
    )


async def transition_usage(
    company_id: uuid.UUID,
    target: str,
    session: AsyncSession,
    *,
    actor_type: str = "SYSTEM",
    actor_id: str | None = None,
    reason: str | None = None,
) -> None:
    company = await session.get(Company, company_id)
    if company is None:
        raise InvalidTransitionError(f"Company {company_id} não encontrada")

    current = company.usage_status
    allowed = USAGE_TRANSITIONS.get(current, frozenset())
    if target not in allowed:
        raise InvalidTransitionError(
            f"Transição usage_status inválida: {current}→{target} (company {company_id})"
        )

    await session.execute(
        text("UPDATE companies SET usage_status=:s WHERE id=:id"),
        {"s": target, "id": company_id},
    )
    await _insert_event(
        session, company_id, "USAGE_STATUS_CHANGED",
        current, target, actor_type, actor_id, reason,
    )


async def transition_verification(
    company_id: uuid.UUID,
    target: str,
    session: AsyncSession,
    *,
    actor_type: str = "SYSTEM",
    actor_id: str | None = None,
    reason: str | None = None,
    is_admin: bool = False,
) -> None:
    company = await session.get(Company, company_id)
    if company is None:
        raise InvalidTransitionError(f"Company {company_id} não encontrada")

    current = company.verification_status
    allowed = VERIFICATION_TRANSITIONS.get(current, frozenset())
    if target not in allowed:
        raise InvalidTransitionError(
            f"Transição verification_status inválida: {current}→{target} (company {company_id})"
        )

    if (current, target) in VERIFICATION_ADMIN_ONLY:
        if not is_admin:
            raise AdminRequiredError(
                f"Reabertura {current}→{target} requer is_admin=True (company {company_id})"
            )
        await session.execute(text("SET LOCAL app.actor_role = 'ADMIN'"))

    await session.execute(
        text("UPDATE companies SET verification_status=:s WHERE id=:id"),
        {"s": target, "id": company_id},
    )
    await _insert_event(
        session, company_id, "VERIFICATION_STATUS_CHANGED",
        current, target, actor_type, actor_id, reason,
    )
