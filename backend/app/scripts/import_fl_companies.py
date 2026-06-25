"""Script de importação FL legacy.

Uso:
  # Preflight (sem gravar)
  python -m app.scripts.import_fl_companies --source ../../data.json --dry-run

  # Importação real (Etapa B — apenas após OK do Natã)
  python -m app.scripts.import_fl_companies --source ../../data.json

Saída:
  - Relatório de preflight no stdout.
  - CSV em /tmp/fl_preflight_<timestamp>.csv (dry-run) ou após confirmação.
"""
import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.services.fl_import.importer import (
    ImportReport,
    PreflightReport,
    import_fl_companies,
    load_fl_records,
    preflight,
)


def _engine():
    return create_async_engine(
        settings.direct_url,
        echo=False,
        connect_args={"ssl": "require", "statement_cache_size": 0},
    )


def _print_preflight(report: PreflightReport, csv_path: Path) -> None:
    print("\n" + "=" * 72)
    print("PREFLIGHT FL LEGACY IMPORT")
    print("=" * 72)
    print(f"Total registros   : {report.total}")
    print(f"Parseados OK      : {len(report.ok)}")
    print(f"Com erros         : {len(report.errors)}")
    print(f"Preflight PASSED  : {report.passed}")
    print()
    print(f"Prefixo flal      : {report.flal_count}")
    print(f"Prefixo forl      : {report.forl_count}")
    print(f"Endereços c/ suite: {len(report.suite_cases)}")
    print(f"Trailing ', FL'   : {len(report.trailing_fl_cases)}")
    print()

    if report.suite_cases:
        print("── Suite cases ─────────────────────────────────────────────────")
        for a in report.suite_cases:
            print(f"  {a}")
        print()

    if report.trailing_fl_cases:
        print("── Trailing ', FL' (primeiros 5) ────────────────────────────────")
        for a in report.trailing_fl_cases[:5]:
            print(f"  {a}")
        if len(report.trailing_fl_cases) > 5:
            print(f"  ... e mais {len(report.trailing_fl_cases) - 5}")
        print()

    if report.errors:
        print("── ERROS ───────────────────────────────────────────────────────")
        for e in report.errors:
            print(f"  [{e.index}] {e.nome[:40]:<40} | {e.field}: {e.message}")
        print()

    # Amostra dos 3 primeiros registros OK
    if report.ok:
        print("── Amostra (3 primeiros OK) ────────────────────────────────────")
        for r in report.ok[:3]:
            print(f"  [{r.index}] {r.nome[:38]}")
            print(f"        entity: {r.entity.entity_number} ({r.entity.prefix})")
            print(f"        street: {r.address.street_line1}"
                  + (f"  suite: {r.address.suite}" if r.address.suite else ""))
            print(f"        city  : {r.address.city}, {r.address.state} {r.address.zip_code}")
            print(f"        phone : {r.phone_e164 or 'NULL'}")
        print()

    print(f"CSV preflight     : {csv_path}")
    print("=" * 72)

    if report.passed:
        print("✓  Preflight PASSOU — 106/106 registros válidos.")
    else:
        print(f"✗  Preflight FALHOU — {len(report.errors)} erro(s). Abortando.")


async def _run(source: Path, dry_run: bool) -> None:
    records_raw = load_fl_records(source)
    report = preflight(records_raw)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    csv_path = Path(f"/tmp/fl_preflight_{stamp}.csv")
    csv_path.write_text(report.csv_content, encoding="utf-8")

    _print_preflight(report, csv_path)

    if not report.passed:
        print("\nABORTADO: preflight falhou. Nenhuma escrita realizada.", file=sys.stderr)
        sys.exit(1)

    if dry_run:
        print("\n[DRY-RUN] Nenhuma escrita realizada. Rode sem --dry-run para importar.")
        return

    # ── Etapa B — importação real ──────────────────────────────────────────────
    print("\n[IMPORT] Iniciando importação em transação única...")
    engine = _engine()
    Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with Session() as session:
        async with session.begin():
            imp_report = await import_fl_companies(report.ok, session)

    await engine.dispose()
    _print_import(imp_report)


def _print_import(r: ImportReport) -> None:
    print("\n" + "=" * 72)
    print("IMPORT REPORT")
    print("=" * 72)
    print(f"Processados       : {r.total_processed}")
    print(f"Companies created : {r.companies_created}")
    print(f"Companies existing: {r.companies_existing}")
    print(f"Addresses created : {r.addresses_created}")
    print(f"Addresses existing: {r.addresses_existing}")
    print(f"Documents created : {r.documents_created}")
    print(f"Documents existing: {r.documents_existing}")
    print(f"Evidences created : {r.evidences_created}")
    if r.errors:
        print(f"ERRORS            : {r.errors}")
    print("=" * 72)


def main() -> None:
    parser = argparse.ArgumentParser(description="Importa FL companies do data.json")
    parser.add_argument(
        "--source",
        type=Path,
        default=Path(__file__).parents[4] / "data.json",
        help="Caminho para data.json",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Só preflight, sem escrever no banco",
    )
    args = parser.parse_args()

    if not args.source.exists():
        print(f"ERRO: {args.source} não encontrado", file=sys.stderr)
        sys.exit(1)

    asyncio.run(_run(args.source, args.dry_run))


if __name__ == "__main__":
    main()
