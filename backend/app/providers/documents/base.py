"""Interface abstrata para providers de documentos.

Apenas ManualUploadDocumentProvider está ATIVO neste bloco.
Os demais ficam como STUB atrás de feature flags DESLIGADAS.
"""
from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class AcceptResult:
    validation_status: str           # VALID | INVALID | MISMATCH | PENDING
    validation_errors: list[str] = field(default_factory=list)
    document_id: uuid.UUID | None = None


class DocumentProvider(ABC):
    """Interface plugável para ingestão de documentos empresariais."""

    @abstractmethod
    async def accept(
        self,
        company_id: uuid.UUID,
        data: bytes,
        filename: str,
        session: AsyncSession,
    ) -> AcceptResult:
        """Valida, armazena e registra o documento para a company.

        Implementações devem:
        - Validar formato e integridade do arquivo.
        - Fazer upload para R2 (storage_key).
        - Inserir em company_documents com validation_status adequado.
        - Inserir company_field_evidence com procedência e direção.
        """
