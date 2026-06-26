"""Crawl dos 124 candidatos CA — extrai email/telefone, gera evidências, funil + ranking.

Uso:
  python -m app.scripts.crawl_ca_websites [--limit N] [--dry-run]

Restrições:
  - SOMENTE source_state='CA', dossier_status='DISCOVERED'. FL é intocável.
  - SSRF guard ativo em toda requisição.
  - Paralelo: MAX_CONCURRENT_DOMAINS=5, HOST_DELAY=1.5s.
  - NÃO avança para READY. Apenas NOT_STARTED→IN_PROGRESS (verification_status).
  - NÃO toca matching, PDF, registro estadual — isso é manual.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import gzip
import json
import sys
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.storage import get_r2_client
from app.domain.models import Company, CompanyAddress, CompanyEvent, CompanyFieldEvidence, WebsiteSnapshot
from app.services.crawler import Crawler
from app.services.email_extractor import _extract_text as _html_to_text
from app.services.state_machines import transition_verification

# ─── Constantes ───────────────────────────────────────────────────────────────

SOURCE_TYPE = "COMPANY_WEBSITE_CRAWL"
ACTOR_ID = "crawl_ca_websites"


# ─── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class CrawlRecord:
    company_id: str
    commercial_name: str
    city: str | None
    state_abbrev: str | None
    zip_code: str | None
    website_url: str
    phone_db: str | None
    # resultados do crawl
    site_active: bool = False
    http_status: int | None = None
    best_email: str | None = None
    email_type: str | None = None         # COMPANY_DOMAIN | GENERIC_FREEMAIL | None
    all_emails_company: list[str] = field(default_factory=list)
    all_emails_generic: list[str] = field(default_factory=list)
    phone_on_site: str | None = None      # primeiro E.164 encontrado no site
    all_phones_site: list[str] = field(default_factory=list)
    crawl_errors: list[str] = field(default_factory=list)
    robots_disallowed: int = 0
    pages_crawled: int = 0
    r2_key: str | None = None
    r2_text_key: str | None = None        # R2 key do texto extraído (storage_key_text)
    snapshot_url: str | None = None       # URL da página capturada
    snapshot_sha256: str | None = None    # SHA-256 da página capturada
    snapshot_http_status: int | None = None
    crawl_status: str = "pending"         # success | error | no_content | blocked
    # ranking (preenchido depois)
    rank: int = 0
    rank_score: int = 0


# ─── Engine ───────────────────────────────────────────────────────────────────

def _engine():
    return create_async_engine(
        settings.direct_url,
        echo=False,
        connect_args={"ssl": "require", "statement_cache_size": 0},
        pool_size=10,
        max_overflow=5,
    )


# ─── R2 snapshot ──────────────────────────────────────────────────────────────

def _upload_snapshot(body: bytes, company_id: str, sha256: str, run_date: str) -> str | None:
    """Sobe HTML gzipado para R2. Retorna a key ou None se falhar."""
    try:
        key = f"crawl/{run_date}/{company_id[:8]}/{sha256[:16]}.html.gz"
        gz = gzip.compress(body, compresslevel=6)
        client = get_r2_client()
        client.put_object(
            Bucket=settings.r2_bucket,
            Key=key,
            Body=gz,
            ContentType="text/html",
            ContentEncoding="gzip",
        )
        return key
    except Exception:
        return None


def _upload_text_snapshot(body: bytes, company_id: str, sha256: str, run_date: str) -> str | None:
    """Extrai texto do HTML e sobe gzipado para R2. Retorna a key ou None se falhar."""
    try:
        html = body.decode("utf-8", errors="replace")
        text = _html_to_text(html)
        key = f"crawl/{run_date}/{company_id[:8]}/{sha256[:16]}.txt.gz"
        gz = gzip.compress(text.encode("utf-8"), compresslevel=6)
        client = get_r2_client()
        client.put_object(
            Bucket=settings.r2_bucket,
            Key=key,
            Body=gz,
            ContentType="text/plain",
            ContentEncoding="gzip",
        )
        return key
    except Exception:
        return None


# ─── Evidence builder ─────────────────────────────────────────────────────────

def _build_evidences(rec: CrawlRecord) -> list[dict]:
    """Constrói lista de kwargs para CompanyFieldEvidence."""
    company_id = uuid.UUID(rec.company_id)
    meta_base = {
        "crawl_run_actor": ACTOR_ID,
        "website_url": rec.website_url,
        "pages_crawled": rec.pages_crawled,
        "http_status": rec.http_status,
        "r2_snapshot_key": rec.r2_key,
    }
    evs = []

    # 1. Acessibilidade do site
    evs.append(dict(
        id=uuid.uuid4(),
        company_id=company_id,
        field_name="website_reachable",
        field_value=str(rec.site_active).lower(),
        source_type=SOURCE_TYPE,
        source_url=rec.website_url,
        evidence_category="CONTACT",
        evidence_direction="SUPPORTS" if rec.site_active else "CONTRADICTS",
        confidence=90 if rec.site_active else 70,
        metadata_json={**meta_base, "crawl_status": rec.crawl_status,
                       "errors": rec.crawl_errors[:5]},
    ))

    # 2. Emails COMPANY_DOMAIN
    for email in rec.all_emails_company:
        evs.append(dict(
            id=uuid.uuid4(),
            company_id=company_id,
            field_name="website_email",
            field_value=email,
            source_type=SOURCE_TYPE,
            source_url=rec.website_url,
            evidence_category="CONTACT",
            evidence_direction="SUPPORTS",
            confidence=85,
            metadata_json={**meta_base, "email_type": "COMPANY_DOMAIN"},
        ))

    # 3. Emails GENERIC (NEUTRAL — pode ser pessoal)
    for email in rec.all_emails_generic:
        evs.append(dict(
            id=uuid.uuid4(),
            company_id=company_id,
            field_name="website_generic_email",
            field_value=email,
            source_type=SOURCE_TYPE,
            source_url=rec.website_url,
            evidence_category="CONTACT",
            evidence_direction="NEUTRAL",
            confidence=40,
            metadata_json={**meta_base, "email_type": "GENERIC_FREEMAIL",
                           "note": "freemail — pode ser pessoal, não usar como email da empresa"},
        ))

    # 4. Telefones encontrados no site
    for phone in rec.all_phones_site[:3]:  # máx 3
        evs.append(dict(
            id=uuid.uuid4(),
            company_id=company_id,
            field_name="website_phone",
            field_value=phone,
            source_type=SOURCE_TYPE,
            source_url=rec.website_url,
            evidence_category="CONTACT",
            evidence_direction="SUPPORTS",
            confidence=80,
            metadata_json=meta_base,
        ))

    return evs


# ─── Per-company crawl + DB write ─────────────────────────────────────────────

async def _crawl_one(
    rec: CrawlRecord,
    session_factory: async_sessionmaker,
    crawler: Crawler,
    run_date: str,
    dry_run: bool,
    sem: asyncio.Semaphore,
) -> CrawlRecord:
    """Crawla um candidato CA e persiste evidências."""
    async with sem:
        try:
            result = await crawler.crawl_company(rec.website_url)
        except Exception as exc:
            rec.crawl_status = "error"
            rec.crawl_errors.append(f"crawl exception: {exc}")
            return rec

    # Consolidar resultados de todas as páginas
    for page in result.pages:
        if 200 <= page.status_code < 300:
            if not rec.site_active:
                rec.http_status = page.status_code
            rec.site_active = True
            # Snapshot da primeira página de sucesso → R2 HTML + R2 texto
            if rec.r2_key is None and not dry_run and page.body:
                rec.r2_key = await asyncio.to_thread(
                    _upload_snapshot, page.body, rec.company_id, page.sha256, run_date
                )
                if rec.r2_key:
                    rec.r2_text_key = await asyncio.to_thread(
                        _upload_text_snapshot, page.body, rec.company_id, page.sha256, run_date
                    )
                    rec.snapshot_url = page.url
                    rec.snapshot_sha256 = page.sha256
                    rec.snapshot_http_status = page.status_code

        rec.all_emails_company.extend(page.contacts.emails)
        rec.all_emails_generic.extend(page.contacts.generic_emails)
        rec.all_phones_site.extend(page.contacts.phones)

    rec.pages_crawled = len(result.pages)
    rec.robots_disallowed = len(result.robots_disallowed)
    rec.crawl_errors.extend(result.errors)

    # Deduplicar
    rec.all_emails_company = list(dict.fromkeys(rec.all_emails_company))
    rec.all_emails_generic = list(dict.fromkeys(rec.all_emails_generic))
    rec.all_phones_site = list(dict.fromkeys(rec.all_phones_site))

    # Melhor email e tipo
    if rec.all_emails_company:
        rec.best_email = rec.all_emails_company[0]
        rec.email_type = "COMPANY_DOMAIN"
    elif rec.all_emails_generic:
        rec.best_email = rec.all_emails_generic[0]
        rec.email_type = "GENERIC_FREEMAIL"

    # Melhor telefone do site
    if rec.all_phones_site:
        rec.phone_on_site = rec.all_phones_site[0]

    rec.crawl_status = (
        "success" if rec.site_active
        else ("blocked" if any("SSRF" in e for e in rec.crawl_errors) else "no_content")
    )

    if dry_run:
        return rec

    # ── Persistir no DB ───────────────────────────────────────────────────────
    async with session_factory() as session:
        async with session.begin():
            try:
                # Savepoint para que InvalidTransitionError não aborte a tx externa
                async with session.begin_nested():
                    await transition_verification(
                        uuid.UUID(rec.company_id),
                        "IN_PROGRESS",
                        session,
                        actor_type="WORKER",
                        actor_id=ACTOR_ID,
                        reason="website crawl iniciado",
                    )
            except Exception:
                pass  # já estava IN_PROGRESS — savepoint revertido, tx continua

            # WebsiteSnapshot (HTML + texto no R2)
            if rec.r2_key and rec.snapshot_sha256:
                async with session.begin_nested():
                    try:
                        session.add(WebsiteSnapshot(
                            id=uuid.uuid4(),
                            company_id=uuid.UUID(rec.company_id),
                            url=rec.snapshot_url or rec.website_url,
                            storage_key_html=rec.r2_key,
                            storage_key_text=rec.r2_text_key,
                            http_status=rec.snapshot_http_status,
                            content_type="text/html",
                            sha256=rec.snapshot_sha256,
                            extraction_metadata={
                                "crawl_run_actor": ACTOR_ID,
                                "pages_crawled": rec.pages_crawled,
                            },
                        ))
                        await session.flush()
                    except Exception:
                        pass  # (company_id, url, sha256) já existe — ON CONFLICT ignorado

            # Evidências
            evs = _build_evidences(rec)
            for ev_kwargs in evs:
                session.add(CompanyFieldEvidence(**ev_kwargs))

            # Evento de crawl
            session.add(CompanyEvent(
                id=uuid.uuid4(),
                company_id=uuid.UUID(rec.company_id),
                event_type="CRAWL_COMPLETED",
                new_value={
                    "site_active": rec.site_active,
                    "crawl_status": rec.crawl_status,
                    "email_type": rec.email_type,
                    "best_email": rec.best_email,
                    "phone_on_site": rec.phone_on_site,
                    "pages": rec.pages_crawled,
                    "errors": len(rec.crawl_errors),
                },
                actor_type="WORKER",
                actor_id=ACTOR_ID,
                reason="Etapa 1 — crawl de verificação CA",
            ))

    return rec


# ─── Ranking ──────────────────────────────────────────────────────────────────

def _score(rec: CrawlRecord) -> int:
    s = 0
    if rec.site_active:          s += 100
    if rec.email_type == "COMPANY_DOMAIN": s += 50
    if rec.phone_on_site:        s += 20
    if rec.email_type == "GENERIC_FREEMAIL": s += 10
    return s


# ─── Report builders ──────────────────────────────────────────────────────────

def _build_funnel(records: list[CrawlRecord]) -> dict:
    total = len(records)
    site_active = sum(1 for r in records if r.site_active)
    email_company = sum(1 for r in records if r.email_type == "COMPANY_DOMAIN")
    email_generic_only = sum(
        1 for r in records if r.site_active and r.email_type == "GENERIC_FREEMAIL"
    )
    no_email = sum(1 for r in records if r.site_active and not r.best_email)
    phone_on_site = sum(1 for r in records if r.phone_on_site)
    no_active_site = sum(1 for r in records if not r.site_active)
    crawl_errors_count = sum(1 for r in records if r.crawl_errors)

    return {
        "total": total,
        "site_active": site_active,
        "email_company_domain": email_company,
        "email_generic_only": email_generic_only,
        "no_email_active_site": no_email,
        "phone_confirmed_on_site": phone_on_site,
        "no_active_site": no_active_site,
        "crawl_errors": crawl_errors_count,
    }


def _print_report(records: list[CrawlRecord], funnel: dict, dry_run: bool) -> None:
    sep = "=" * 74
    print(f"\n{sep}")
    mode = "DRY-RUN" if dry_run else "REAL"
    print(f"CRAWL CA — FUNIL DE VERIFICAÇÃO ({mode})")
    print(sep)
    print(f"  Total processado       : {funnel['total']}")
    print(f"  Site ativo (2xx)       : {funnel['site_active']}")
    print(f"  Email COMPANY_DOMAIN   : {funnel['email_company_domain']}  ← os bons")
    print(f"  Email só GENERIC       : {funnel['email_generic_only']}")
    print(f"  Sem email (site ativo) : {funnel['no_email_active_site']}")
    print(f"  Telefone confirmado    : {funnel['phone_confirmed_on_site']}")
    print(f"  Sem site ativo         : {funnel['no_active_site']}  ← candidatos a PARTIAL")
    print(f"  Com erros de crawl     : {funnel['crawl_errors']}")

    ranked = sorted(records, key=_score, reverse=True)
    print(f"\n── TOP 40 RANQUEADOS ─────────────────────────────────────────────────")
    print(f"{'#':>3}  {'Nome':<42}  {'Cidade':<16}  {'Melhor Email':<30}  {'Tel site'}")
    print("─" * 130)
    for i, r in enumerate(ranked[:40], 1):
        city = (r.city or "-")[:16]
        email = (r.best_email or "-")[:30]
        tel = r.phone_on_site or "-"
        name = (r.commercial_name or "-")[:42]
        star = "★" if r.email_type == "COMPANY_DOMAIN" else ("○" if r.email_type else "✗")
        print(f"{i:>3}  {name:<42}  {city:<16}  {star} {email:<30}  {tel}")
    print(sep)


# ─── Main ─────────────────────────────────────────────────────────────────────

async def _run(limit: int | None, dry_run: bool, company_ids: list[str] | None = None) -> None:
    run_date = datetime.now(timezone.utc).strftime("%Y%m%d")
    run_ts = datetime.now(timezone.utc).isoformat()

    engine = _engine()
    Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    # ── Buscar candidatos CA ─────────────────────────────────────────────────
    async with Session() as s:
        if company_ids:
            # Filtro explícito por lista de IDs — usado para crawl pontual (ex.: piloto)
            id_placeholders = ", ".join(f":id{i}" for i in range(len(company_ids)))
            params = {f"id{i}": cid for i, cid in enumerate(company_ids)}
            rows = await s.execute(sa.text(f"""
                SELECT c.id, c.commercial_name, c.website_url, c.phone_e164,
                       ca.city, ca.state, ca.zip_code
                FROM companies c
                LEFT JOIN company_addresses ca
                    ON ca.company_id = c.id AND ca.address_type='PRINCIPAL'
                WHERE c.id IN ({id_placeholders})
                  AND c.source_state = 'CA'
                  AND c.legacy_read_only = false
                  AND c.website_url IS NOT NULL
                ORDER BY c.created_at
            """), params)
        else:
            rows = await s.execute(sa.text("""
                SELECT c.id, c.commercial_name, c.website_url, c.phone_e164,
                       ca.city, ca.state, ca.zip_code
                FROM companies c
                LEFT JOIN company_addresses ca
                    ON ca.company_id = c.id AND ca.address_type='PRINCIPAL'
                WHERE c.source_state = 'CA'
                  AND c.dossier_status = 'DISCOVERED'
                  AND c.legacy_read_only = false
                  AND c.website_url IS NOT NULL
                ORDER BY c.created_at
            """))
        candidates = rows.fetchall()

    if not candidates:
        print("Nenhum candidato CA encontrado.")
        await engine.dispose()
        return

    if limit:
        candidates = candidates[:limit]

    records: list[CrawlRecord] = [
        CrawlRecord(
            company_id=str(row[0]),
            commercial_name=row[1] or "",
            website_url=row[2],
            phone_db=row[3],
            city=row[4],
            state_abbrev=row[5],
            zip_code=row[6],
        )
        for row in candidates
    ]

    print(f"\n[INFO] Candidatos CA a crawlar : {len(records)}")
    print(f"[INFO] Mode                     : {'DRY-RUN (sem DB)' if dry_run else 'REAL'}")
    print(f"[INFO] Paralelo                 : 5 domínios / HOST_DELAY=1.5s")
    print(f"[INFO] Iniciando crawl...\n")

    # ── Crawl paralelo ────────────────────────────────────────────────────────
    sem = asyncio.Semaphore(5)  # MAX_CONCURRENT_DOMAINS
    async with Crawler() as crawler:
        tasks = [
            _crawl_one(rec, Session, crawler, run_date, dry_run, sem)
            for rec in records
        ]
        done = 0
        results: list[CrawlRecord] = []
        for coro in asyncio.as_completed(tasks):
            rec = await coro
            results.append(rec)
            done += 1
            status = "✓" if rec.site_active else ("✗" if not rec.crawl_errors else "!")
            email_tag = f"[{rec.email_type or 'no-email'}]" if rec.site_active else "[dead]"
            print(f"  [{done:>3}/{len(records)}] {status} {rec.commercial_name[:45]:<45}  {email_tag}")

    # ── Score e ranking ───────────────────────────────────────────────────────
    results_sorted = sorted(results, key=_score, reverse=True)
    for i, r in enumerate(results_sorted, 1):
        r.rank = i
        r.rank_score = _score(r)

    funnel = _build_funnel(results)
    _print_report(results_sorted, funnel, dry_run)

    # ── Salvar JSON ───────────────────────────────────────────────────────────
    out_dir = Path("data/discovery")
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    out_json = out_dir / f"crawl_funil_{ts}.json"
    out_csv  = out_dir / f"crawl_funil_{ts}.csv"

    report = {
        "meta": {
            "run_at": run_ts,
            "mode": "dry_run" if dry_run else "real",
            "total_candidates": len(records),
            "limit": limit,
        },
        "funnel": funnel,
        "ranked_candidates": [
            {
                "rank": r.rank,
                "rank_score": r.rank_score,
                "company_id": r.company_id,
                "commercial_name": r.commercial_name,
                "city": r.city,
                "state": r.state_abbrev,
                "zip_code": r.zip_code,
                "website_url": r.website_url,
                "best_email": r.best_email,
                "email_type": r.email_type,
                "all_emails_company": r.all_emails_company,
                "all_emails_generic": r.all_emails_generic,
                "phone_on_site": r.phone_on_site,
                "phone_db": r.phone_db,
                "all_phones_site": r.all_phones_site,
                "site_active": r.site_active,
                "http_status": r.http_status,
                "crawl_status": r.crawl_status,
                "pages_crawled": r.pages_crawled,
                "r2_key": r.r2_key,
                "crawl_errors": r.crawl_errors[:5],
            }
            for r in results_sorted
        ],
        "no_active_site": [
            {
                "company_id": r.company_id,
                "commercial_name": r.commercial_name,
                "website_url": r.website_url,
                "crawl_status": r.crawl_status,
                "errors": r.crawl_errors[:3],
            }
            for r in results_sorted
            if not r.site_active
        ],
    }

    if not dry_run:
        with open(out_json, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        # CSV dos ranqueados
        with open(out_csv, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=[
                "rank", "rank_score", "commercial_name", "city", "state", "zip_code",
                "website_url", "best_email", "email_type", "phone_on_site", "phone_db",
                "site_active", "crawl_status", "r2_key",
            ])
            w.writeheader()
            for r in results_sorted:
                w.writerow({
                    "rank": r.rank, "rank_score": r.rank_score,
                    "commercial_name": r.commercial_name, "city": r.city,
                    "state": r.state_abbrev, "zip_code": r.zip_code,
                    "website_url": r.website_url, "best_email": r.best_email,
                    "email_type": r.email_type, "phone_on_site": r.phone_on_site,
                    "phone_db": r.phone_db, "site_active": r.site_active,
                    "crawl_status": r.crawl_status, "r2_key": r.r2_key,
                })
        print(f"\n  JSON: {out_json}")
        print(f"  CSV : {out_csv}")
    else:
        print(f"\n  [DRY-RUN] arquivos NÃO salvos.")

    await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Crawl dos candidatos CA DISCOVERED — extrai email/telefone e gera evidências."
    )
    parser.add_argument("--limit", type=int, default=None,
                        help="Crawlar apenas os primeiros N candidatos (teste).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Crawla e mostra resultados sem escrever no DB.")
    parser.add_argument("--companies", type=str, default=None,
                        help="UUID(s) separados por vírgula — crawl pontual de companies específicas.")
    args = parser.parse_args()
    ids = [cid.strip() for cid in args.companies.split(",")] if args.companies else None
    asyncio.run(_run(args.limit, args.dry_run, company_ids=ids))


if __name__ == "__main__":
    main()
