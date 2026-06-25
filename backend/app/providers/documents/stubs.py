"""Stubs DESLIGADOS de providers de documentos.

Nenhum destes providers tem lógica real implementada neste bloco.
Todos estão atrás de feature flags falsas — não instanciar em produção.

  ENABLE_CALICO_DOCUMENTS   = false
  ENABLE_KYCKR_PROVIDER     = false
  ENABLE_BULK_PROVIDER      = false
  ENABLE_BROWSER_PROVIDER   = false
"""
from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.providers.documents.base import AcceptResult, DocumentProvider


class _DisabledProvider(DocumentProvider):
    _flag: str

    async def accept(
        self,
        company_id: uuid.UUID,
        data: bytes,
        filename: str,
        session: AsyncSession,
    ) -> AcceptResult:
        raise NotImplementedError(
            f"{type(self).__name__}: feature flag {self._flag!r} está desligada. "
            "Não implementar lógica real até habilitação explícita."
        )


class CalicoDocumentProvider(_DisabledProvider):
    """STUB — ENABLE_CALICO_DOCUMENTS=false."""
    _flag = "ENABLE_CALICO_DOCUMENTS"


class BulkDocumentProvider(_DisabledProvider):
    """STUB — ENABLE_BULK_PROVIDER=false."""
    _flag = "ENABLE_BULK_PROVIDER"


class KyckrDocumentProvider(_DisabledProvider):
    """STUB — ENABLE_KYCKR_PROVIDER=false. ThirdParty/Kyckr não ativo."""
    _flag = "ENABLE_KYCKR_PROVIDER"


class BrowserDocumentProvider(_DisabledProvider):
    """STUB — ENABLE_BROWSER_PROVIDER=false. Browser automation não ativo."""
    _flag = "ENABLE_BROWSER_PROVIDER"
