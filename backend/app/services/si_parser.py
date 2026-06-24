"""Parser para CA Statement of Information (form SI-200/SI-201).

Campos extraídos com distinção estrita:
  entity_number  — "Entity No." no corpo. NUNCA confundir com File No.
  file_number    — "File No.:" no rodapé (só registrar, não usar como identidade).
  legal_name     — "Limited Liability Company Name".
  filed_date     — "Date Filed:" no rodapé.
  principal_address / mailing_address / agent_address — separados.
  agent_email    — e-mail dentro da seção "Agent for Service of Process".
                   NÃO usar como e-mail comercial (regra 11CC).
  company_email  — e-mail fora da seção do agente (pode ser None).
  page_count     — total de páginas.
  is_no_change   — True se o documento é um "Statement of No Change" (SI-200NC).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from io import BytesIO
from typing import Optional

import pdfplumber

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

_REGISTERED_AGENT_DOMAINS: frozenset[str] = frozenset(
    {
        "northwestregisteredagent.com",
        "incorp.com",
        "legalzoom.com",
        "registeredagents.com",
        "nrai.com",
        "cscglobal.com",
        "corporationservice.com",
        "cogencyglobal.com",
        "myregisteredagent.com",
        "zenbusiness.com",
        "harborcomplianceglobal.com",
        "bizfilings.com",
        "vcorp.com",
        "floridaregisteredagent.com",
        "rocketlawyer.com",
        "usacorporationservice.com",
        "easylinkdataservices.com",
    }
)

_GENERIC_FREEMAIL_DOMAINS: frozenset[str] = frozenset(
    {"gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "icloud.com",
     "aol.com", "live.com", "msn.com", "me.com", "mac.com"}
)

# SI-NO CHANGE: só match em frases específicas para não confundir com
# "No Manager or Member..." ou "for which no appeal..."
_NO_CHANGE_RE = re.compile(
    r"statement\s+of\s+no\s+change"
    r"|no\s+changes?\s+to\s+report"
    r"|no\s+change\s+to\s+(?:the\s+)?information"
    r"|type:\s*no\s+change",
    re.IGNORECASE,
)

_FILE_NO_LABEL_RE = re.compile(r"File\s+No\.:\s*(\S+)", re.IGNORECASE)
_ENTITY_NO_RE = re.compile(r"^Entity\s+No\.\s+(\S+)", re.MULTILINE)
_LEGAL_NAME_RE = re.compile(
    r"^(?:Limited Liability Company|Corporation)\s+Name\s+(.+)$", re.MULTILINE
)
_DATE_FILED_RE = re.compile(r"Date\s+Filed:\s*(\d{1,2}/\d{1,2}/\d{4})", re.IGNORECASE)
_PAGE_N_RE = re.compile(r"\bPage\s+1\s+of\s+(\d+)\b", re.IGNORECASE)

# File No. appearing as the very first token on page 1 (before "STATE OF CALIFORNIA")
# Pattern: 2 uppercase letters followed by digits (e.g. BA20250271262)
_FILE_NO_FIRSTLINE_RE = re.compile(r"^([A-Z]{2}\d{7,})\s*$")


@dataclass
class SIParseResult:
    entity_number: Optional[str]
    file_number: Optional[str]
    legal_name: Optional[str]
    filed_date: Optional[date]
    principal_address: Optional[str]
    mailing_address: Optional[str]
    agent_address: Optional[str]
    agent_email: Optional[str]     # e-mail do agente — NÃO usar como e-mail comercial
    company_email: Optional[str]   # e-mail da empresa fora da seção do agente
    page_count: int
    is_no_change: bool


class SIParseError(ValueError):
    """Lançada quando o PDF não pode ser interpretado como Statement of Information."""


def _extract_pages_text(data: bytes) -> tuple[int, list[str]]:
    """Abre o PDF e retorna (page_count, [text_per_page]).

    Função isolada para facilitar mock em testes.
    """
    with pdfplumber.open(BytesIO(data)) as pdf:
        return len(pdf.pages), [p.extract_text() or "" for p in pdf.pages]


def _extract_address(lines: list[str], label: str) -> Optional[str]:
    """Extrai endereço de duas linhas: 'Label STREET_LINE\\nCITY, ST ZIP'.

    Exclui linhas de cabeçalho como 'Mailing Address of LLC' via lookahead negativo.
    """
    pattern = re.compile(
        rf"^{re.escape(label)}\s+(?!of\s)(.+)",
        re.IGNORECASE,
    )
    for i, line in enumerate(lines):
        m = pattern.match(line.strip())
        if m:
            street = m.group(1).strip()
            if i + 1 < len(lines):
                next_ln = lines[i + 1].strip()
                # Linha de continuidade: "City, ST ZIP" ou "City, ST"
                if re.match(r".+,\s+[A-Z]{2}\b", next_ln):
                    return f"{street}, {next_ln}"
            return street
    return None


def _extract_agent_section_text(full_text: str) -> str:
    """Retorna o texto entre 'Agent for Service of Process' e a próxima seção."""
    start_marker = "Agent for Service of Process"
    end_markers = ["Type of Business", "Email Notifications", "Chief Executive Officer"]

    start_idx = full_text.find(start_marker)
    if start_idx < 0:
        return ""
    start_idx += len(start_marker)

    end_idx = len(full_text)
    for marker in end_markers:
        idx = full_text.find(marker, start_idx)
        if 0 < idx < end_idx:
            end_idx = idx

    return full_text[start_idx:end_idx]


def _classify_domain(email: str) -> str:
    domain = email.rsplit("@", 1)[-1].lower()
    if domain in _REGISTERED_AGENT_DOMAINS:
        return "REGISTERED_AGENT"
    if domain in _GENERIC_FREEMAIL_DOMAINS:
        return "GENERIC_FREEMAIL"
    return "COMPANY_DOMAIN"


def parse_si(data: bytes) -> SIParseResult:
    """Extrai campos de um PDF de Statement of Information da CA SOS.

    Raises SIParseError se o PDF não tiver texto extraível.
    """
    page_count, pages_text = _extract_pages_text(data)
    full_text = "\n".join(pages_text)

    if not full_text.strip():
        raise SIParseError("PDF não contém texto extraível (scanned image sem OCR?)")

    lines = full_text.splitlines()

    # entity_number — "Entity No. 202112310799"
    m = _ENTITY_NO_RE.search(full_text)
    entity_number = m.group(1).strip() if m else None

    # file_number — preferir "File No.: BA20250271262" do rodapé
    m = _FILE_NO_LABEL_RE.search(full_text)
    if m:
        file_number = m.group(1).strip()
    else:
        # Fallback: primeira linha de página 1 com padrão "XX999..."
        first_line = lines[0].strip() if lines else ""
        m2 = _FILE_NO_FIRSTLINE_RE.match(first_line)
        file_number = m2.group(1) if m2 else None

    # legal_name — "Limited Liability Company Name 11CC, LLC"
    m = _LEGAL_NAME_RE.search(full_text)
    legal_name = m.group(1).strip() if m else None

    # filed_date — "Date Filed: 2/6/2025"
    filed_date: Optional[date] = None
    m = _DATE_FILED_RE.search(full_text)
    if m:
        from datetime import datetime
        try:
            filed_date = datetime.strptime(m.group(1), "%m/%d/%Y").date()
        except ValueError:
            pass

    # Endereços separados
    principal_address = _extract_address(lines, "Principal Address")
    mailing_address = _extract_address(lines, "Mailing Address")
    agent_address = _extract_address(lines, "Agent Address")

    # Seção do agente — e-mails aqui NÃO são e-mail comercial (regra 11CC)
    agent_section = _extract_agent_section_text(full_text)
    agent_emails = [e.lower() for e in _EMAIL_RE.findall(agent_section)]
    agent_email = agent_emails[0] if agent_emails else None

    # E-mail da empresa — qualquer e-mail FORA da seção do agente
    text_without_agent = full_text.replace(agent_section, "") if agent_section else full_text
    company_email: Optional[str] = None
    for raw in _EMAIL_RE.findall(text_without_agent):
        candidate = raw.lower()
        # Preferir domínio próprio (COMPANY_DOMAIN); aceitar GENERIC_FREEMAIL como fallback
        if candidate not in agent_emails:
            company_email = candidate
            break

    # SI-NO CHANGE detection
    is_no_change = bool(_NO_CHANGE_RE.search(full_text))

    return SIParseResult(
        entity_number=entity_number,
        file_number=file_number,
        legal_name=legal_name,
        filed_date=filed_date,
        principal_address=principal_address,
        mailing_address=mailing_address,
        agent_address=agent_address,
        agent_email=agent_email,
        company_email=company_email,
        page_count=page_count,
        is_no_change=is_no_change,
    )
