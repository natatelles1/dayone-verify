"""CA Dossier Readiness — avaliação completa (Bloco 4).

Barreiras em ordem:
1. readiness_locked=True → rejeição imediata (inclui toda FL legacy).
2. source_state != 'CA' → rejeição por estado.
3. Avaliação dos 11 critérios CA → READY ou PARTIAL.

As 106 FL sempre falham na barreira 1 (readiness_locked=True).
Esta é a ÚNICA função que pode marcar dossier_status=READY.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import text

from app.domain.models import (
    Company,
    CompanyAddress,
    CompanyDocument,
    CompanyFieldEvidence,
    WebsiteSnapshot,
)
from app.services.state_machines import transition_dossier


class CompanyNotEligibleError(ValueError):
    """Lançada quando uma company não pode ser avaliada pelo fluxo CA."""


@dataclass
class ReadinessResult:
    passed: bool
    partial_reasons: list[str] = field(default_factory=list)


async def evaluate_ca_dossier_readiness(
    company_id: uuid.UUID,
    session: AsyncSession,
) -> ReadinessResult:
    """Avalia se uma company CA está pronta para o fluxo de uso.

    Retorna ReadinessResult(passed=True) e marca READY, ou
    ReadinessResult(passed=False, partial_reasons=[...]) e marca PARTIAL.
    """
    company = await session.get(Company, company_id)
    if company is None:
        raise CompanyNotEligibleError(f"Company {company_id} não encontrada")

    # Barreira 1: legado / locked
    if company.readiness_locked:
        raise CompanyNotEligibleError(
            f"Company {company_id} rejeitada: readiness_locked=true "
            f"(policy={company.readiness_policy}, source_state={company.source_state}). "
            "Empresas FL_LEGACY_V1 nunca passam pela readiness CA."
        )

    # Barreira 2: somente CA
    if company.source_state != "CA":
        raise CompanyNotEligibleError(
            f"Company {company_id} rejeitada: source_state={company.source_state!r}. "
            "evaluate_ca_dossier_readiness aceita apenas source_state='CA'."
        )

    partial_reasons: list[str] = []

    # 1. legal_name + evidência SUPPORTS
    has_ln_evidence = False
    if company.legal_name:
        row = await session.execute(
            sa.select(CompanyFieldEvidence.id).where(
                CompanyFieldEvidence.company_id == company_id,
                CompanyFieldEvidence.field_name == "legal_name",
                CompanyFieldEvidence.evidence_direction == "SUPPORTS",
            ).limit(1)
        )
        has_ln_evidence = row.first() is not None
    if not company.legal_name or not has_ln_evidence:
        partial_reasons.append("legal_name ausente ou sem evidência")

    # 2. principal_address
    row = await session.execute(
        sa.select(CompanyAddress.id).where(
            CompanyAddress.company_id == company_id,
            CompanyAddress.address_type == "PRINCIPAL",
        ).limit(1)
    )
    if row.first() is None:
        partial_reasons.append("principal_address ausente")

    # 3. email — evidência SUPPORTS
    row = await session.execute(
        sa.select(CompanyFieldEvidence.id).where(
            CompanyFieldEvidence.company_id == company_id,
            CompanyFieldEvidence.field_name == "email",
            CompanyFieldEvidence.evidence_direction == "SUPPORTS",
        ).limit(1)
    )
    if row.first() is None:
        partial_reasons.append("email público sem evidência")

    # 4. phone_e164 — evidência SUPPORTS
    row = await session.execute(
        sa.select(CompanyFieldEvidence.id).where(
            CompanyFieldEvidence.company_id == company_id,
            CompanyFieldEvidence.field_name == "phone_e164",
            CompanyFieldEvidence.evidence_direction == "SUPPORTS",
        ).limit(1)
    )
    if row.first() is None:
        partial_reasons.append("telefone público sem evidência")

    # 5. entity_number
    if not company.entity_number or not company.entity_number.strip():
        partial_reasons.append("entity_number ausente")

    # 6. documento SI válido
    row = await session.execute(
        sa.select(CompanyDocument.id).where(
            CompanyDocument.company_id == company_id,
            CompanyDocument.document_type == "SI",
            CompanyDocument.validation_status == "VALID",
        ).limit(1)
    )
    if row.first() is None:
        partial_reasons.append("documento SI-COMPLETE ausente ou inválido")

    # 7. website vinculado
    if not company.website_url and not company.domain:
        partial_reasons.append("website não vinculado")

    # 8. HTML bruto (texto extraído)
    row = await session.execute(
        sa.select(WebsiteSnapshot.id).where(
            WebsiteSnapshot.company_id == company_id,
            WebsiteSnapshot.storage_key_text.isnot(None),
        ).limit(1)
    )
    if row.first() is None:
        partial_reasons.append("HTML bruto (texto) ausente")

    # 9. match_score >= 60
    if company.match_score is None or company.match_score < 60:
        partial_reasons.append("match_score insuficiente (<60)")

    # 10. ≥2 categorias de evidência SUPPORTS
    row = await session.execute(
        sa.select(
            sa.func.count(sa.func.distinct(CompanyFieldEvidence.evidence_category))
        ).where(
            CompanyFieldEvidence.company_id == company_id,
            CompanyFieldEvidence.evidence_direction == "SUPPORTS",
        )
    )
    cat_count = row.scalar_one()
    if cat_count < 2:
        partial_reasons.append("menos de 2 categorias de evidência")

    # 11. nenhuma contradição ativa
    row = await session.execute(
        sa.select(CompanyFieldEvidence.id).where(
            CompanyFieldEvidence.company_id == company_id,
            CompanyFieldEvidence.evidence_direction == "CONTRADICTS",
        ).limit(1)
    )
    if row.first() is not None:
        partial_reasons.append("contradição ativa detectada")

    if not partial_reasons:
        await transition_dossier(
            company_id,
            "READY",
            session,
            actor_type="SYSTEM",
            reason="evaluate_ca_dossier_readiness: todos os critérios atendidos",
        )
        return ReadinessResult(passed=True, partial_reasons=[])

    await session.execute(
        text(
            "UPDATE companies SET dossier_status='PARTIAL', partial_reasons=:pr::jsonb "
            "WHERE id=:id"
        ),
        {"pr": json.dumps(partial_reasons), "id": company_id},
    )
    await session.execute(
        text(
            "INSERT INTO company_events "
            "(company_id, event_type, old_value, new_value, actor_type, reason) "
            "VALUES (:cid, 'DOSSIER_STATUS_CHANGED', '\"DOSSIER_BUILDING\"'::jsonb, "
            "'\"PARTIAL\"'::jsonb, 'SYSTEM', :r)"
        ),
        {"cid": company_id, "r": f"partial: {'; '.join(partial_reasons)}"},
    )
    return ReadinessResult(passed=False, partial_reasons=partial_reasons)
