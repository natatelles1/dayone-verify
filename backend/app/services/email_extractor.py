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
        "yahoo.com.br",
        "outlook.com",
        "hotmail.com",
        "hotmail.com.br",
        "icloud.com",
        "aol.com",
        "live.com",
        "msn.com",
        "me.com",
        "mac.com",
        "protonmail.com",
        "proton.me",
        "ymail.com",
    }
)

# Domínios que são claramente placeholders de template/formulário.
# Nunca são e-mails reais de empresa — descartar silenciosamente.
_PLACEHOLDER_DOMAINS: frozenset[str] = frozenset(
    {
        "domain.com",       # user@domain.com, name@domain.com
        "yourdomain.com",   # contact@yourdomain.com
        "yourcompany.com",  # info@yourcompany.com
        "yourname.com",     # yourname@yourname.com
        "example.com",      # defesa redundante (já em _NOISE_FRAGMENTS)
        "example.org",
        "example.net",
        "test.com",         # placeholders de testes
        "email.com",        # your@email.com
        "sample.com",
        "acme.com",         # placeholder clássico
        "address.com",      # email@address.com — template genérico de formulário
        "godaddy.com",      # filler@godaddy.com — auto-fill padrão do GoDaddy Website Builder
                            # (e-mails reais via GoDaddy usam domínio próprio, nunca @godaddy.com)
    }
)

# Partes locais que indicam placeholder independente do domínio.
# Ex.: filler@*, noreply@* — nunca são contatos comerciais reais.
_PLACEHOLDER_LOCAL_PARTS: frozenset[str] = frozenset(
    {
        "filler",
        "noreply",
        "no-reply",
        "donotreply",
        "do-not-reply",
        "null",
        "none",
    }
)

# Plataformas de site-builder cujos e-mails aparecem como SUBdomínios gerados
# (ex: hello@minha-empresa-976359.hostingersite.com).
# Verificação por sufixo: domain == suffix OU domain.endswith('.'+suffix).
_BUILDER_SUBDOMAIN_SUFFIXES: frozenset[str] = frozenset(
    {
        "hostingersite.com",   # Hostinger: slug-gerado.hostingersite.com
        "wixsite.com",         # Wix: usuario.wixsite.com/site
        "weebly.com",          # Weebly: meusite.weebly.com
        "godaddysites.com",    # GoDaddy Website Builder: slug.godaddysites.com
    }
)


def classify_email(email: str) -> str:
    """Classifica e-mail em: COMPANY_DOMAIN | REGISTERED_AGENT | GENERIC_FREEMAIL | PLACEHOLDER | UNKNOWN.

    PLACEHOLDER: domínio de template, subdomínio de site-builder, ou parte-local de auto-fill.
    GENERIC_FREEMAIL: nunca promovido a COMPANY_DOMAIN.
    """
    if not email or "@" not in email:
        return "UNKNOWN"
    local, domain = email.rsplit("@", 1)
    domain = domain.lower()
    local_lc = local.lower()
    if domain in REGISTERED_AGENT_DOMAINS:
        return "REGISTERED_AGENT"
    if domain in _PLACEHOLDER_DOMAINS:
        return "PLACEHOLDER"
    if any(domain == s or domain.endswith(f".{s}") for s in _BUILDER_SUBDOMAIN_SUFFIXES):
        return "PLACEHOLDER"
    if local_lc in _PLACEHOLDER_LOCAL_PARTS:
        return "PLACEHOLDER"
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
        if cls in ("REGISTERED_AGENT", "PLACEHOLDER"):
            continue  # Regra 11CC + placeholder: descarte silencioso
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
