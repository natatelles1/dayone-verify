"""Libera empresas prontas (READY/READY_NO_PDF) pra API externa do Gabriel.
Marca released_to_client=TRUE. Só isso — não muda dossier_status nem mais nada.
⚠️ Só DayOne. NÃO toca FL/Legatus/Impetus.

Uso:
    python -m app.scripts.release_companies --entity-numbers B20260224824 C1234567
    python -m app.scripts.release_companies --all-ready
    python -m app.scripts.release_companies --all-ready --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.sql import text

ACTOR = "release_companies"


async def main(entity_numbers: list[str] | None, all_ready: bool, dry_run: bool) -> None:
    engine = create_async_engine(os.environ["DIRECT_URL"], echo=False, pool_pre_ping=True)
    SF = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with SF() as s:
        if all_ready:
            rows = (await s.execute(text(
                "SELECT id, legal_name, entity_number, dossier_status, released_to_client "
                "FROM companies "
                "WHERE source_state='CA' AND dossier_status IN ('READY','READY_NO_PDF') "
                "ORDER BY legal_name"
            ))).fetchall()
        else:
            rows = (await s.execute(text(
                "SELECT id, legal_name, entity_number, dossier_status, released_to_client "
                "FROM companies "
                "WHERE source_state='CA' AND dossier_status IN ('READY','READY_NO_PDF') "
                "AND entity_number = ANY(:nums) "
                "ORDER BY legal_name"
            ), {"nums": entity_numbers})).fetchall()

    if not rows:
        print("Nenhuma empresa pronta encontrada com esses critérios. Nada a fazer.")
        return

    already = [r for r in rows if r.released_to_client]
    to_release = [r for r in rows if not r.released_to_client]

    print(f"\n{'='*72}\nLIBERAÇÃO PRO GABRIEL{'  (DRY RUN — nada será alterado)' if dry_run else ''}\n{'='*72}")
    print(f"Encontradas: {len(rows)} | já liberadas: {len(already)} | a liberar agora: {len(to_release)}\n")

    for r in to_release:
        print(f"  → {r.legal_name[:55]:<55} {r.entity_number:<16} {r.dossier_status}")
    if already:
        print(f"\n  (já liberadas, sem mudança: {', '.join(r.entity_number for r in already)})")

    if dry_run or not to_release:
        print("\nNenhuma alteração aplicada." if dry_run else "\nJá estavam todas liberadas — nada a fazer.")
        await engine.dispose()
        return

    ids = [r.id for r in to_release]
    async with SF() as s:
        async with s.begin():
            await s.execute(
                text("UPDATE companies SET released_to_client = TRUE WHERE id = ANY(:ids)"),
                {"ids": ids},
            )
            for r in to_release:
                await s.execute(text("""
                    INSERT INTO company_events
                    (company_id, event_type, old_value, new_value, actor_type, actor_id, reason)
                    VALUES (:cid, 'RELEASED_TO_CLIENT', CAST('false' AS jsonb), CAST('true' AS jsonb),
                            'USER', :actor, :reason)
                """), {
                    "cid": r.id,
                    "actor": ACTOR,
                    "reason": "Liberado pro Gabriel via release_companies.py",
                })

    print(f"\n✅ {len(to_release)} empresa(s) liberada(s) (released_to_client=TRUE).")
    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--entity-numbers", nargs="+", help="Entity numbers específicos a liberar")
    group.add_argument("--all-ready", action="store_true", help="Libera TODAS as prontas (READY/READY_NO_PDF)")
    parser.add_argument("--dry-run", action="store_true", help="Só mostra o que faria, não altera nada")
    args = parser.parse_args()
    asyncio.run(main(args.entity_numbers, args.all_ready, args.dry_run))
