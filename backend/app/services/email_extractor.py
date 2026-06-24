"""Extração e classificação de e-mails e telefones de HTML.

Regra 11CC: e-mails de registered agents são SILENCIOSAMENTE DESCARTADOS —
nunca devem aparecer como e-mail comercial de uma empresa.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import phonenumbers
from bs4 import BeautifulSoup

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

_NOISE_FRAGMENTS = (
    "example.com",
    "sentry.io",
    "wixpress.com",
    "squarespace.com",
    "wpengine.com",
    "@2x",
    ".png",
    ".jpg",
    ".gif",
    ".svg",
    "schema.org",
    "w3.org",
)

REGISTERED_AGENT_DOMAINS: frozenset[str] = frozenset(
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

_GENERIC_DOMAINS: frozenset[str] = frozenset(
    {
        "gmail.com",
        "yahoo.com",
        "outlook.com",
        "hotmail.com",
        "icloud.com",
        "aol.com",
        "live.com",
        "msn.com",
        "me.com",
        "mac.com",
    }
)


def classify_email(email: str) -> str:
    """Classifica um e-mail em: COMPANY_DOMAIN | REGISTERED_AGENT | GENERIC_FREEMAIL | UNKNOWN."""
    if not email or "@" not in email:
        return "UNKNOWN"
    domain = email.rsplit("@", 1)[1].lower()
    if domain in REGISTERED_AGENT_DOMAINS:
        return "REGISTERED_AGENT"
    if domain in _GENERIC_DOMAINS:
        return "GENERIC_FREEMAIL"
    return "COMPANY_DOMAIN"


@dataclass
class ExtractedContact:
    emails: list[str] = field(default_factory=list)         # COMPANY_DOMAIN
    generic_emails: list[str] = field(default_factory=list) # GENERIC_FREEMAIL
    phones: list[str] = field(default_factory=list)         # E.164


def _extract_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "meta", "link"]):
        tag.decompose()
    return soup.get_text(" ", strip=True)


def extract_contacts(html: str, *, page_url: str = "") -> ExtractedContact:
    """Extrai e-mails e telefones de HTML.

    11CC: e-mails de REGISTERED_AGENT são descartados silenciosamente.
    Busca tanto no texto visível quanto no HTML bruto (para mailto: obfuscados).
    """
    text = _extract_text(html)
    result = ExtractedContact()
    seen: set[str] = set()

    # Buscar no texto extraído E no HTML bruto para pegar mailto: e atributos
    for raw in EMAIL_RE.findall(text + " " + html):
        email = raw.lower()
        if email in seen:
            continue
        if any(n in email for n in _NOISE_FRAGMENTS):
            continue
        seen.add(email)
        cls = classify_email(email)
        if cls == "REGISTERED_AGENT":
            continue  # Regra 11CC: descarte silencioso
        elif cls == "COMPANY_DOMAIN":
            result.emails.append(email)
        elif cls == "GENERIC_FREEMAIL":
            result.generic_emails.append(email)
        # UNKNOWN: ignora

    # Telefones — busca no texto legível, normaliza para E.164
    phone_re = re.compile(r"[\+\(]?[\d\s\-\(\)\.]{7,20}")
    seen_phones: set[str] = set()
    for raw in phone_re.findall(text):
        try:
            p = phonenumbers.parse(raw, "US")
            if phonenumbers.is_valid_number(p):
                e164 = phonenumbers.format_number(p, phonenumbers.PhoneNumberFormat.E164)
                if e164 not in seen_phones:
                    seen_phones.add(e164)
                    result.phones.append(e164)
        except phonenumbers.NumberParseException:
            continue

    return result
