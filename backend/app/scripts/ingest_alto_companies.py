"""PASSO 2 — ingestão dos 6 ALTO confirmados (DISCOVERED → READY_NO_PDF).

Fluxo por empresa (tudo em uma transação):
  1. DISCOVERED → MATCHED  (transition_dossier + event)
  2. UPDATE companies: match_score=90, legal_name, entity_number
  3. INSERT company_events: MATCH_SCORE_SET (provenance MANUAL_PARSEFORGE_CONFIRMED)
  4. MATCHED → DOSSIER_BUILDING  (transition_dossier + event)
  5. DELETE PRINCIPAL address antiga + INSERT novo endereço ParseForge (source=PARSEFORGE_CONFIRMED)
  6. INSERT company_field_evidence: legal_name SUPPORTS REGISTRY_LOOKUP
  7. (FaithWorks somente) INSERT company_events: LEGAL_NAME_RESOLVED (divergência Advisor→Advisory)
  8. flush + expire_all
  9. evaluate_ca_dossier_readiness_nopdf (pure calculation — não escreve)
 10. DOSSIER_BUILDING → READY_NO_PDF ou PARTIAL  (transition_dossier + UPDATE partial_reasons)

⚠️ Restrições:
  - Só DayOne (ydvjtgvrbajarrefcfdl)
  - NÃO toca FL / Legatus
  - evaluate_ca_dossier_readiness original (com PDF) NÃO usada
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

import sqlalchemy as sa
from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.sql import text

load_dotenv(Path(__file__).parent.parent.parent / ".env")

# Importações do projeto (engine criado manualmente — sem depender de app.core.config)
from app.domain.models import CompanyAddress, CompanyFieldEvidence
from app.services.ca_readiness import evaluate_ca_dossier_readiness_nopdf
from app.services.state_machines import transition_dossier

# ── Dados ParseForge dos 6 ALTO ───────────────────────────────────────────────

@dataclass
class AltoData:
    company_id: str
    commercial_name: str          # nome no DB
    legal_name: str               # nome OFICIAL SOS (ParseForge entityName)
    entity_number: str
    street_line1: str
    suite: str | None
    city: str
    state: str
    zip_code: str
    faithworks_divergence: bool = False


ALTO_COMPANIES = [
    AltoData(
        company_id="7362726d-833b-4c68-b497-5be9855911b7",
        commercial_name="Mantax Financial, LLC",
        legal_name="Mantax Financial, LLC",
        entity_number="202354611723",
        street_line1="1350 W 25TH STREET",
        suite="SUITE 4",
        city="SAN PEDRO",
        state="CA",
        zip_code="90732",
    ),
    AltoData(
        company_id="8ab012a1-9af8-4005-93b1-ab91b57e1197",
        commercial_name="Tax N More Services LLC",
        legal_name="Tax N More Services LLC",
        entity_number="202252210480",
        street_line1="13545 VAN NUYS BLVD",
        suite="SUITE B21 - 2ND FLOOR",
        city="PACOIMA",
        state="CA",
        zip_code="91331",
    ),
    AltoData(
        company_id="f9c8aa50-43e3-406a-837a-5acf216eca1f",
        commercial_name="Duty Ink",
        legal_name="Duty Ink LLC",
        entity_number="202358113658",
        street_line1="6080 CENTER DRIVE",
        suite="6TH FLOOR STE. 616",
        city="LOS ANGELES",
        state="CA",
        zip_code="90045",
    ),
    AltoData(
        company_id="b7e5173d-4360-456a-87c5-be9138524be0",
        commercial_name="NexusWorks LLC",
        legal_name="NexusWorks LLC",
        entity_number="202463614122",
        street_line1="20100 SOUTH WESTERN AVENUE",
        suite="R211",
        city="TORRANCE",
        state="CA",
        zip_code="90501",
    ),
    AltoData(
        company_id="3ce251b1-6cf4-4ec9-b24a-56af5f5c1f2c",
        commercial_name="Sunset Financial Group, LLC",
        legal_name="SUNSET FINANCIAL GROUP, LLC",
        entity_number="201103310342",
        street_line1="5757 W. CENTURY BLVD.",
        suite="STE 812",
        city="LOS ANGELES",
        state="CA",
        zip_code="90045",
    ),
    AltoData(
        company_id="4c2886c5-7f22-4dde-a5c5-add335b22c1b",
        commercial_name="FaithWorks Tax & Advisor LLC",
        legal_name="FaithWorks Tax & Advisory LLC",  # com Y — registro CA SOS
        entity_number="B20250361776",
        street_line1="841 1/2 W 64TH STREET",
        suite=None,
        city="LOS ANGELES",
        state="CA",
        zip_code="90044",
        faithworks_divergence=True,
    ),
]

ACTOR_ID = "nata_parseforge_20260626"
MATCH_REASON = (
    "MANUAL_PARSEFORGE_CONFIRMED: entidade LLC-CA ativa confirmada via CA SOS "
    "(ParseForge SOMBRA) — score=90 atribuído manualmente, não calculado por algoritmo"
)


async def ingest_one(
    data: AltoData,
    session: AsyncSession,
) -> tuple[str, list[str]]:
    """Processa uma empresa. Retorna (decision, partial_reasons). Lança em caso de erro."""

    cid = uuid.UUID(data.company_id)

    # ── 1. DISCOVERED → MATCHED ───────────────────────────────────────────────
    await transition_dossier(
        cid, "MATCHED", session,
        actor_type="USER",
        actor_id=ACTOR_ID,
        reason=MATCH_REASON,
    )

    # ── 2. UPDATE match_score, legal_name, entity_number ─────────────────────
    await session.execute(
        text(
            "UPDATE companies "
            "SET match_score = 90, legal_name = :ln, entity_number = :en "
            "WHERE id = :id"
        ),
        {"ln": data.legal_name, "en": data.entity_number, "id": cid},
    )

    # ── 3. Event: proveniência do match_score ─────────────────────────────────
    await session.execute(
        text(
            "INSERT INTO company_events "
            "(company_id, event_type, old_value, new_value, actor_type, actor_id, reason) "
            "VALUES (:cid, 'MATCH_SCORE_SET', "
            "CAST(:ov AS jsonb), CAST(:nv AS jsonb), 'USER', :aid, :r)"
        ),
        {
            "cid": cid,
            "ov": "null",
            "nv": json.dumps({
                "match_score": 90,
                "entity_number": data.entity_number,
                "legal_name": data.legal_name,
                "source": "MANUAL_PARSEFORGE_CONFIRMED",
                "note": (
                    "Score atribuído manualmente após revisão ParseForge CA SOS — "
                    "NÃO calculado por algoritmo de similaridade"
                ),
            }),
            "aid": ACTOR_ID,
            "r": MATCH_REASON,
        },
    )

    # ── 4. MATCHED → DOSSIER_BUILDING ────────────────────────────────────────
    session.expire_all()
    await transition_dossier(
        cid, "DOSSIER_BUILDING", session,
        actor_type="USER",
        actor_id=ACTOR_ID,
        reason="PARSEFORGE_CONFIRMED: avançando para avaliação READY_NO_PDF",
    )

    # ── 5. UPSERT endereço PRINCIPAL (ParseForge → substituiu Maps) ───────────
    await session.execute(
        text("DELETE FROM company_addresses WHERE company_id = :cid AND address_type = 'PRINCIPAL'"),
        {"cid": cid},
    )
    session.add(CompanyAddress(
        id=uuid.uuid4(),
        company_id=cid,
        address_type="PRINCIPAL",
        street_line1=data.street_line1,
        suite=data.suite,
        city=data.city,
        state=data.state,
        zip_code=data.zip_code,
        country="US",
        source="PARSEFORGE_CONFIRMED",
    ))

    # ── 6. Evidence: legal_name SUPPORTS REGISTRY_LOOKUP ─────────────────────
    session.add(CompanyFieldEvidence(
        id=uuid.uuid4(),
        company_id=cid,
        field_name="legal_name",
        field_value=data.legal_name,
        source_type="PARSEFORGE_CONFIRMED",
        evidence_category="REGISTRY_LOOKUP",
        evidence_direction="SUPPORTS",
        confidence=90,
        metadata_json={
            "entity_number": data.entity_number,
            "entity_type": "Limited Liability Company - CA",
            "status": "Active",
            "source": "CA SOS via ParseForge SOMBRA",
        },
    ))

    # ── 7. FaithWorks: event registrando divergência de nome ─────────────────
    if data.faithworks_divergence:
        await session.execute(
            text(
                "INSERT INTO company_events "
                "(company_id, event_type, old_value, new_value, actor_type, actor_id, reason) "
                "VALUES (:cid, 'LEGAL_NAME_RESOLVED', "
                "CAST(:ov AS jsonb), CAST(:nv AS jsonb), 'USER', :aid, :r)"
            ),
            {
                "cid": cid,
                "ov": json.dumps({
                    "commercial_name": "FaithWorks Tax & Advisor LLC",
                    "source": "Google Maps",
                    "note": "Usa 'Advisor' (sem Y)",
                }),
                "nv": json.dumps({
                    "legal_name": "FaithWorks Tax & Advisory LLC",
                    "entity_number": "B20250361776",
                    "source": "CA SOS via ParseForge SOMBRA",
                    "note": "Registro oficial usa 'Advisory' (com Y)",
                }),
                "aid": ACTOR_ID,
                "r": (
                    "Divergência resolvida: Google Maps usa 'Advisor' mas CA SOS registra "
                    "'Advisory' (com Y) — entity_number B20250361776 confirma identidade"
                ),
            },
        )

    # ── 8. Flush + expire → readiness function vê dados frescos ──────────────
    await session.flush()
    session.expire_all()

    # ── 9. Pure calculation — NENHUMA ESCRITA ─────────────────────────────────
    result = await evaluate_ca_dossier_readiness_nopdf(cid, session)

    # ── 10. Transição final e persistência da decisão ─────────────────────────
    session.expire_all()
    if result.decision == "READY_NO_PDF":
        await transition_dossier(
            cid, "READY_NO_PDF", session,
            actor_type="USER",
            actor_id=ACTOR_ID,
            reason="evaluate_ca_dossier_readiness_nopdf: 10 critérios atendidos (sem PDF)",
        )
        await session.execute(
            text("UPDATE companies SET partial_reasons = '[]'::jsonb WHERE id = :id"),
            {"id": cid},
        )
    else:
        reasons_str = "; ".join(result.partial_reasons)
        await transition_dossier(
            cid, "PARTIAL", session,
            actor_type="USER",
            actor_id=ACTOR_ID,
            reason=f"evaluate_ca_dossier_readiness_nopdf PARTIAL: {reasons_str}",
        )
        await session.execute(
            text(
                "UPDATE companies SET partial_reasons = CAST(:pr AS jsonb) WHERE id = :id"
            ),
            {"pr": json.dumps(result.partial_reasons), "id": cid},
        )

    return result.decision, result.partial_reasons


async def main() -> None:
    direct_url = os.environ["DIRECT_URL"]
    engine = create_async_engine(direct_url, echo=False, pool_pre_ping=True)
    SessionFactory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    print("=" * 70)
    print("PASSO 2 — Ingestão ALTO (DISCOVERED → READY_NO_PDF)")
    print("=" * 70)

    results = []
    for data in ALTO_COMPANIES:
        try:
            async with SessionFactory() as session:
                async with session.begin():
                    decision, reasons = await ingest_one(data, session)
            ok = decision == "READY_NO_PDF"
            results.append((data.commercial_name, decision, ok, reasons))
            icon = "✅" if ok else "⚠️"
            print(f"{icon} {data.commercial_name:<45} → {decision}")
            if reasons:
                for r in reasons:
                    print(f"    └─ {r}")
        except Exception as exc:
            results.append((data.commercial_name, "ERROR", False, [str(exc)]))
            print(f"❌ {data.commercial_name:<45} ERRO: {exc}")

    # ── Relatório final ────────────────────────────────────────────────────────
    print()
    print("─" * 70)
    print("| {:<43} | {:<16} | {} |".format("Empresa", "dossier_status final", "READY_NO_PDF?"))
    print("─" * 70)
    for name, decision, ok, reasons in results:
        check = "✅ SIM" if ok else ("❌ ERRO" if decision == "ERROR" else "⚠️ NÃO")
        print(f"| {name:<43} | {decision:<20} | {check} |")
    print("─" * 70)

    ready_count = sum(1 for _, _, ok, _ in results if ok)
    print(f"\n{ready_count}/6 viraram READY_NO_PDF")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
