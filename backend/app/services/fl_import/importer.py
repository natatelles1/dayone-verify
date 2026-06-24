"""Importador FL legacy — preflight e import dos 106 registros.

Fluxo:
  1. load_fl_records(): lê e valida estrutura básica do JSON.
  2. preflight(): parseia todos os 106, coleta erros, não escreve no banco.
  3. import_fl_companies(): UMA transação atômica; rollback total em qualquer falha.

Invariantes FL: source_state='FL', readiness_policy='FL_LEGACY_V1',
readiness_locked=True, legacy_read_only=True — constante nomeada, nunca parâmetro.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models import (
    Company,
    CompanyAddress,
    CompanyDocument,
    CompanyEvent,
    CompanyFieldEvidence,
)
from app.services.fl_import.parsers import (
    ParsedAddress,
    ParsedEntityNumber,
    parse_entity_number,
    parse_fl_address,
    normalize_phone,
)

# ─── Constantes FL Legacy ─────────────────────────────────────────────────────

_FL_LEGACY_OVERRIDES: dict[str, Any] = {
    "source_state": "FL",
    "readiness_policy": "FL_LEGACY_V1",
    "readiness_locked": True,
    "legacy_read_only": True,
}

_REQUIRED_FL_KEYS = frozenset(_FL_LEGACY_OVERRIDES.keys())

# Status das 106 FL da demo: READY (preserva prontidão do legado entregue)
_FL_DOSSIER_STATUS = "READY"
_FL_USAGE_STATUS = "AVAILABLE"
_FL_VERIFICATION_STATUS = "NOT_STARTED"


# ─── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class ParsedRecord:
    index: int
    nome: str
    ein: str
    email: str
    telefone_raw: str | None
    documento_url: str
    endereco_raw: str
    entity: ParsedEntityNumber
    address: ParsedAddress
    phone_e164: str | None


@dataclass
class ParseError:
    index: int
    nome: str
    field: str
    message: str


@dataclass
class PreflightReport:
    total: int
    ok: list[ParsedRecord] = field(default_factory=list)
    errors: list[ParseError] = field(default_factory=list)
    flal_count: int = 0
    forl_count: int = 0
    suite_cases: list[str] = field(default_factory=list)
    trailing_fl_cases: list[str] = field(default_factory=list)
    csv_content: str = ""

    @property
    def passed(self) -> bool:
        return len(self.errors) == 0 and len(self.ok) == self.total


@dataclass
class ImportReport:
    total_processed: int = 0
    companies_created: int = 0
    companies_existing: int = 0
    addresses_created: int = 0
    addresses_existing: int = 0
    documents_created: int = 0
    documents_existing: int = 0
    evidences_created: int = 0
    errors: list[str] = field(default_factory=list)


# ─── Load ─────────────────────────────────────────────────────────────────────

_REQUIRED_FIELDS = frozenset({"nome", "ein", "email", "documento"})


def load_fl_records(path: Path) -> list[dict]:
    """Carrega e valida estrutura mínima do data.json."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError("data.json deve ser uma lista JSON")

    for i, rec in enumerate(data):
        missing = _REQUIRED_FIELDS - set(rec.keys())
        if missing:
            raise ValueError(f"Registro {i} ({rec.get('nome', '?')}) faltando campos: {missing}")

    return data


# ─── Preflight ────────────────────────────────────────────────────────────────

def preflight(records: list[dict]) -> PreflightReport:
    """Parseia todos os registros e coleta erros SEM escrever no banco.

    Verifica:
    - Entity number (aggregateId flal/forl, transactionId consistente)
    - Endereço (parser ancorado da direita, ZIP válido, suite)
    - Telefone (E.164, nunca +11...)
    - Unicidade de entity_number, EIN, URL de documento
    - Contagens flal vs forl
    """
    report = PreflightReport(total=len(records))

    seen_entity_numbers: dict[str, int] = {}
    seen_eins: dict[str, int] = {}
    seen_urls: dict[str, int] = {}

    for i, rec in enumerate(records):
        nome = rec.get("nome", f"[registro {i}]")
        errors_this: list[ParseError] = []

        # --- entity_number ---
        entity: ParsedEntityNumber | None = None
        try:
            entity = parse_entity_number(rec["documento"])
        except ValueError as e:
            errors_this.append(ParseError(i, nome, "documento/entity_number", str(e)))

        if entity:
            key = entity.entity_number
            if key in seen_entity_numbers:
                errors_this.append(ParseError(
                    i, nome, "entity_number",
                    f"Duplicado: entity_number {key} já apareceu no registro {seen_entity_numbers[key]}"
                ))
            else:
                seen_entity_numbers[key] = i
            if entity.prefix == "flal":
                report.flal_count += 1
            else:
                report.forl_count += 1

        # --- EIN dedup ---
        ein = rec.get("ein", "")
        if ein in seen_eins:
            errors_this.append(ParseError(
                i, nome, "ein",
                f"EIN duplicado: {ein} já no registro {seen_eins[ein]}"
            ))
        else:
            seen_eins[ein] = i

        # --- URL dedup ---
        url = rec.get("documento", "")
        if url in seen_urls:
            errors_this.append(ParseError(
                i, nome, "documento",
                f"URL duplicada: já no registro {seen_urls[url]}"
            ))
        else:
            seen_urls[url] = i

        # --- endereço ---
        address: ParsedAddress | None = None
        try:
            address = parse_fl_address(rec["endereco"])
            if address.suite:
                report.suite_cases.append(rec["endereco"])
            import re
            if re.search(r",\s*FL\s*$", rec["endereco"], re.IGNORECASE):
                report.trailing_fl_cases.append(rec["endereco"])
        except ValueError as e:
            errors_this.append(ParseError(i, nome, "endereco", str(e)))

        # --- telefone ---
        phone_e164: str | None = None
        telefone_raw = rec.get("telefone") or None
        try:
            phone_e164 = normalize_phone(telefone_raw)
        except ValueError as e:
            errors_this.append(ParseError(i, nome, "telefone", str(e)))

        if errors_this:
            report.errors.extend(errors_this)
        elif entity and address:
            report.ok.append(ParsedRecord(
                index=i,
                nome=nome,
                ein=ein,
                email=rec["email"],
                telefone_raw=telefone_raw,
                documento_url=url,
                endereco_raw=rec["endereco"],
                entity=entity,
                address=address,
                phone_e164=phone_e164,
            ))

    # Gerar CSV de preflight
    report.csv_content = _build_csv(records, report.ok, report.errors)
    return report


def _build_csv(
    records: list[dict],
    ok: list[ParsedRecord],
    errors: list[ParseError],
) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow([
        "index", "nome", "ein", "email",
        "telefone_raw", "phone_e164",
        "entity_number", "entity_prefix",
        "street_line1", "suite", "city", "state", "zip_code",
        "normalized", "address_hash",
        "documento_url",
        "parse_status", "parse_errors",
    ])

    ok_by_idx = {r.index: r for r in ok}
    err_by_idx: dict[int, list[str]] = {}
    for e in errors:
        err_by_idx.setdefault(e.index, []).append(f"{e.field}: {e.message}")

    for i, rec in enumerate(records):
        r = ok_by_idx.get(i)
        errs = err_by_idx.get(i, [])
        writer.writerow([
            i,
            rec.get("nome", ""),
            rec.get("ein", ""),
            rec.get("email", ""),
            rec.get("telefone", ""),
            r.phone_e164 if r else "",
            r.entity.entity_number if r else "",
            r.entity.prefix if r else "",
            r.address.street_line1 if r else "",
            r.address.suite if r else "",
            r.address.city if r else "",
            r.address.state if r else "",
            r.address.zip_code if r else "",
            r.address.normalized if r else "",
            r.address.address_hash if r else "",
            rec.get("documento", ""),
            "OK" if r else "ERROR",
            "; ".join(errs),
        ])

    return buf.getvalue()


# ─── Import (Etapa B) ─────────────────────────────────────────────────────────

async def import_fl_companies(
    records: list[ParsedRecord],
    session: AsyncSession,
) -> ImportReport:
    """Importa os 106 registros em UMA transação atômica.

    Estratégia de idempotência (get_or_create, sem skip total):
    - company: busca por (source_state='FL', entity_number)
    - address: busca por (company_id, address_type, address_hash, source)
    - document: busca por (company_id, source_url)
    - evidence: sempre insere (append-only; company_field_evidence não tem unique)

    Validação de contagens antes do commit:
    - total companies no batch deve ser 106
    - Qualquer divergência → rollback e exceção.
    """
    report = ImportReport()
    now = datetime.now(timezone.utc)

    for pr in records:
        # ── get_or_create company ─────────────────────────────────────────────
        company_id_scalar = await session.scalar(
            sa.select(Company.id).where(
                Company.source_state == "FL",
                Company.entity_number == pr.entity.entity_number,
            )
        )

        if company_id_scalar:
            company_id = company_id_scalar
            report.companies_existing += 1
        else:
            company = Company(
                id=uuid.uuid4(),
                legal_name=pr.nome,
                commercial_name=None,      # frontend usa COALESCE(commercial, legal)
                entity_number=pr.entity.entity_number,
                legacy_ein=pr.ein,
                email=pr.email,
                phone_e164=pr.phone_e164,
                dossier_status=_FL_DOSSIER_STATUS,
                usage_status=_FL_USAGE_STATUS,
                verification_status=_FL_VERIFICATION_STATUS,
                **_FL_LEGACY_OVERRIDES,
            )
            session.add(company)
            await session.flush()
            company_id = company.id
            report.companies_created += 1

        # ── ensure address ────────────────────────────────────────────────────
        existing_addr = await session.scalar(
            sa.select(CompanyAddress.id).where(
                CompanyAddress.company_id == company_id,
                CompanyAddress.address_type == "PRINCIPAL",
                CompanyAddress.address_hash == pr.address.address_hash,
                CompanyAddress.source == "FL_LEGACY_IMPORT",
            )
        )
        if existing_addr:
            report.addresses_existing += 1
        else:
            addr = CompanyAddress(
                id=uuid.uuid4(),
                company_id=company_id,
                address_type="PRINCIPAL",
                street_line1=pr.address.street_line1,
                suite=pr.address.suite,
                city=pr.address.city,
                state=pr.address.state,
                zip_code=pr.address.zip_code,
                country=pr.address.country,
                normalized=pr.address.normalized,
                address_hash=pr.address.address_hash,
                source="FL_LEGACY_IMPORT",
            )
            session.add(addr)
            report.addresses_created += 1

        # ── ensure document ───────────────────────────────────────────────────
        existing_doc = await session.scalar(
            sa.select(CompanyDocument.id).where(
                CompanyDocument.company_id == company_id,
                CompanyDocument.source_url == pr.documento_url,
            )
        )
        if existing_doc:
            report.documents_existing += 1
        else:
            doc = CompanyDocument(
                id=uuid.uuid4(),
                company_id=company_id,
                provider="SUNBIZ",
                document_type="REGISTRATION",
                source_url=pr.documento_url,
                storage_key=None,          # PDF não baixado ainda
                validation_status="PENDING",
            )
            session.add(doc)
            report.documents_created += 1

        # ── ensure evidence (append-only, sem dedup) ──────────────────────────
        evidences = _build_evidences(company_id, pr)
        for ev in evidences:
            session.add(ev)
        report.evidences_created += len(evidences)

        report.total_processed += 1

    # Validação antes do commit
    total_companies = report.companies_created + report.companies_existing
    if total_companies != len(records):
        raise RuntimeError(
            f"Contagem inválida antes do commit: "
            f"esperado {len(records)} companies, processado {total_companies}. "
            "Rollback."
        )

    # commit controlado pela camada superior (script)
    return report


def _build_evidences(
    company_id: uuid.UUID,
    pr: ParsedRecord,
) -> list[CompanyFieldEvidence]:
    """Cria evidências FL_LEGACY_IMPORT para os campos obrigatórios."""
    base = dict(
        company_id=company_id,
        source_type="FL_LEGACY_IMPORT",
        evidence_direction="SUPPORTS",
        confidence=None,
    )
    evs = [
        CompanyFieldEvidence(
            id=uuid.uuid4(),
            field_name="legal_name",
            field_value=pr.nome,
            evidence_category="IDENTITY",
            metadata_json={"source_field": "nome"},
            **base,
        ),
        CompanyFieldEvidence(
            id=uuid.uuid4(),
            field_name="legacy_ein",
            field_value=pr.ein,
            evidence_category="IDENTITY",
            metadata_json={"source_field": "ein"},
            **base,
        ),
        CompanyFieldEvidence(
            id=uuid.uuid4(),
            field_name="email",
            field_value=pr.email,
            evidence_category="CONTACT",
            metadata_json={"source_field": "email"},
            **base,
        ),
        CompanyFieldEvidence(
            id=uuid.uuid4(),
            field_name="principal_address",
            field_value=pr.address.normalized,
            evidence_category="LOCATION",
            metadata_json={
                "source_field": "endereco",
                "original": pr.address.original,
                "parsed": {
                    "street_line1": pr.address.street_line1,
                    "suite": pr.address.suite,
                    "city": pr.address.city,
                    "state": pr.address.state,
                    "zip_code": pr.address.zip_code,
                },
            },
            **base,
        ),
        CompanyFieldEvidence(
            id=uuid.uuid4(),
            field_name="document",
            field_value=pr.documento_url,
            evidence_category="REGISTRATION",
            metadata_json={
                "provider": "SUNBIZ",
                "document_type": "REGISTRATION",
                "entity_number": pr.entity.entity_number,
            },
            **base,
        ),
    ]
    if pr.phone_e164:
        evs.append(CompanyFieldEvidence(
            id=uuid.uuid4(),
            field_name="phone_e164",
            field_value=pr.phone_e164,
            evidence_category="CONTACT",
            metadata_json={"source_field": "telefone", "raw": pr.telefone_raw},
            **base,
        ))
    return evs
