"""Parsers para importação FL legacy.

Nenhum acesso a banco ou I/O externo — apenas transformação pura.
Todas as funções lançam ValueError em caso de entrada inválida,
permitindo preflight completo antes de qualquer escrita.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

# ─── Entity Number ─────────────────────────────────────────────────────────────

# Aceita flal-l... e forl-m...
_AGGREGATE_RE = re.compile(
    r"^(?:flal|forl)-(?P<entity_number>[lm]\d+)-(?P<uuid>[0-9a-f-]{36})$"
)


@dataclass(frozen=True)
class ParsedEntityNumber:
    entity_number: str   # uppercase, ex: "L23000502530"
    prefix: str          # "flal" ou "forl"
    aggregate_id: str    # valor bruto do parâmetro aggregateId
    uuid_part: str       # UUID extraído do aggregateId


def parse_entity_number(sunbiz_url: str) -> ParsedEntityNumber:
    """Extrai e valida o entity_number a partir de uma URL Sunbiz.

    Regras:
    - Parseia a query string; lê 'aggregateId'.
    - Aceita prefixo flal ou forl; entity_number começa com l ou m.
    - Valida que 'transactionId' começa com o mesmo entity_number (lowercase).
    - Armazena em UPPERCASE.
    - Nunca usa hash da URL como substituto silencioso.
    """
    if not sunbiz_url:
        raise ValueError("URL Sunbiz está vazia")

    qs = parse_qs(urlparse(sunbiz_url).query)

    # aggregateId
    agg_list = qs.get("aggregateId", [])
    if not agg_list:
        raise ValueError(f"aggregateId ausente na URL: {sunbiz_url!r}")
    aggregate_id = agg_list[0]

    m = _AGGREGATE_RE.match(aggregate_id)
    if not m:
        raise ValueError(
            f"aggregateId não corresponde ao padrão "
            f"^(flal|forl)-[lm]\\d+-UUID$: {aggregate_id!r}"
        )

    entity_number_raw = m.group("entity_number")   # ex: "l23000502530"
    uuid_part = m.group("uuid")
    prefix = aggregate_id.split("-")[0]             # "flal" ou "forl"

    # Valida UUID: deve ter exatamente 4 hífens e 32 hex chars
    uuid_clean = uuid_part.replace("-", "")
    if len(uuid_clean) != 32 or not all(c in "0123456789abcdef" for c in uuid_clean):
        raise ValueError(f"UUID inválido em aggregateId: {uuid_part!r}")

    # transactionId deve começar com o entity_number (lowercase)
    txn_list = qs.get("transactionId", [])
    if not txn_list:
        raise ValueError(f"transactionId ausente na URL: {sunbiz_url!r}")
    txn = txn_list[0]
    if not txn.startswith(entity_number_raw):
        raise ValueError(
            f"transactionId {txn!r} não começa com entity_number {entity_number_raw!r}"
        )

    return ParsedEntityNumber(
        entity_number=entity_number_raw.upper(),
        prefix=prefix,
        aggregate_id=aggregate_id,
        uuid_part=uuid_part,
    )


# ─── Address ───────────────────────────────────────────────────────────────────

# Padrões de suíte que podem aparecer como segmento separado ou inline no street
_SUITE_SEGMENT_RE = re.compile(
    r"^(?:(?:SUITE|STE)\s+\S+|PH-\S+|UNIT\s+\S+|APT\s+\S+|#\s*\S+)$",
    re.IGNORECASE,
)
_SUITE_INLINE_RE = re.compile(
    r"\s+(?:(?:SUITE|STE|UNIT|APT)\s+\S+|PH-\S+|#\s*\S+)$",
    re.IGNORECASE,
)
# Aceita qualquer estado US: FLAL/FORL são entidades FL mas podem ter endereço em outro estado
_STATE_ZIP_RE = re.compile(r"^([A-Z]{2})\s+(\d{5}(?:-\d{4})?)$", re.IGNORECASE)
# Sunbiz por vezes adiciona o estado FL como sufixo redundante nas exportações
_TRAILING_STATE_RE = re.compile(r",\s*(FL)\s*$", re.IGNORECASE)
_VALID_ZIP_RE = re.compile(r"^\d{5}(?:-\d{4})?$")


def _extract_suite_value(raw: str) -> str:
    """Extrai o identificador da suíte de uma string como 'SUITE 500', 'PH-7', '#3B'."""
    # PH-X: mantém o designador completo
    if re.match(r"^PH-", raw, re.IGNORECASE):
        return raw.upper()
    # SUITE/STE/UNIT/APT/# → pega o token depois do keyword
    m = re.match(r"^(?:SUITE|STE|UNIT|APT|#)\s*(\S+)$", raw, re.IGNORECASE)
    if m:
        return m.group(1).upper()
    return raw.upper()


@dataclass(frozen=True)
class ParsedAddress:
    street_line1: str
    suite: str | None
    city: str
    state: str          # código de 2 letras; maioria FL, mas entidades FL podem ter escritório em outro estado
    zip_code: str
    country: str        # sempre "US"
    normalized: str     # forma canônica para hashing
    address_hash: str   # SHA-256 hex de normalized
    original: str       # string original sem modificação


def parse_fl_address(endereco: str) -> ParsedAddress:
    """Parseia endereço FL no formato 'STREET, CITY, FL ZIP[, FL]'.

    Estratégia ancorada na direita:
    1. Remove trailing ', FL' opcional.
    2. Filtra segmentos vazios (double-comma).
    3. Último segmento = 'FL ZIP' (valida regex).
    4. Penúltimo = city.
    5. Restantes = um ou mais segmentos de street/suite.
    6. Se um segmento isolado parecer suite, extrai.
    7. Se nenhum segmento isolado, busca suite inline no street.
    8. Suite ambígua → NÃO adivinha (mantém no street, suite=None).
    """
    original = endereco.strip()
    s = original

    # 1. Trailing ', FL' opcional
    trailing_state: str | None = None
    m = _TRAILING_STATE_RE.search(s)
    if m:
        trailing_state = m.group(1).upper()
        s = s[: m.start()].strip()

    # 2. Split e filtra segmentos vazios (double-comma)
    parts = [p.strip() for p in s.split(",")]
    parts = [p for p in parts if p]

    if len(parts) < 3:
        raise ValueError(
            f"Endereço com partes insuficientes após parse: {original!r} "
            f"→ {parts}"
        )

    # 3. Último segmento = 'FL ZIP'
    m = _STATE_ZIP_RE.match(parts[-1])
    if not m:
        raise ValueError(
            f"Último segmento não é 'FL ZIP': {parts[-1]!r} "
            f"em {original!r}"
        )
    state = m.group(1).upper()
    zip_code = m.group(2)

    if trailing_state and trailing_state != state:
        raise ValueError(
            f"trailing_state {trailing_state!r} ≠ state {state!r} "
            f"em {original!r}"
        )

    # 4. Penúltimo = city
    city = parts[-2]

    # 5. Segmentos antes de city
    pre_city = parts[:-2]  # pode ser [street], [street, suite], etc.

    # 6. Segmento separado que parece suite
    suite: str | None = None
    street_segments: list[str] = []

    for seg in pre_city:
        if _SUITE_SEGMENT_RE.match(seg):
            if suite is None:
                suite = _extract_suite_value(seg)
            else:
                # Dois possíveis suítes — não adivinha
                street_segments.append(seg)
        else:
            street_segments.append(seg)

    street_line1 = ", ".join(street_segments) if street_segments else pre_city[0]

    # 7. Busca inline apenas se nenhum segmento separado de suite foi encontrado
    if suite is None and len(street_segments) == 1:
        m2 = _SUITE_INLINE_RE.search(street_line1)
        if m2:
            suite_raw = m2.group(0).strip()
            suite = _extract_suite_value(suite_raw)
            street_line1 = street_line1[: m2.start()].strip().rstrip(",").strip()

    # 8. Normaliza e gera hash
    parts_norm = [street_line1.upper()]
    if suite:
        parts_norm.append(suite.upper())
    parts_norm += [city.upper(), f"{state} {zip_code}"]
    normalized = ", ".join(p for p in parts_norm if p)
    address_hash = hashlib.sha256(normalized.encode()).hexdigest()

    return ParsedAddress(
        street_line1=street_line1,
        suite=suite,
        city=city,
        state=state,
        zip_code=zip_code,
        country="US",
        normalized=normalized,
        address_hash=address_hash,
        original=original,
    )


# ─── Phone ─────────────────────────────────────────────────────────────────────


def normalize_phone(raw: str | None) -> str | None:
    """Normaliza telefone para E.164 (+1XXXXXXXXXX).

    Regras:
    - None ou string vazia → None.
    - Remove todos os não-dígitos.
    - 11 dígitos começando com '1' → '+<dígitos>'.
    - 10 dígitos → '+1<dígitos>'.
    - Qualquer outro comprimento → ValueError.
    - Garante que nunca produz '+11...'.
    """
    if not raw:
        return None

    digits = re.sub(r"\D", "", raw)

    if not digits:
        return None

    if len(digits) == 11 and digits[0] == "1":
        e164 = f"+{digits}"
    elif len(digits) == 10:
        e164 = f"+1{digits}"
    else:
        raise ValueError(
            f"Telefone em formato inválido (esperado 10 ou 11 dígitos): "
            f"{raw!r} → {len(digits)} dígitos"
        )

    # Dupla verificação: nunca +11...
    if e164.startswith("+11"):
        raise ValueError(
            f"Telefone produziria '+11...' (double country code): {raw!r} → {e164}"
        )

    return e164
