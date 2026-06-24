"""Crawler seguro com SSRF guard, robots.txt, rate limiting e extração de contatos.

Restrições de segurança:
- SSRF guard em TODA requisição HTTP, inclusive redirects (max 3).
- robots.txt respeitado por padrão.
- Tamanho máximo do body: 5 MB.
- Content-type: apenas text/html e text/plain.
- Timeout por requisição: 10 s.
- Rate limit: mínimo 1,5 s entre requests ao mesmo host.
- Máximo 5 domínios concorrentes.
- O crawler NÃO roda sobre empresas FL (readiness_locked).
"""
from __future__ import annotations

import asyncio
import hashlib
import re
import urllib.robotparser
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import httpx

from app.services.email_extractor import ExtractedContact, extract_contacts
from app.services.ssrf_guard import SSRFBlockedError, validate_redirect, validate_url

MAX_BODY_BYTES = 5 * 1024 * 1024  # 5 MB
MAX_REDIRECTS = 3
REQUEST_TIMEOUT = 10.0
HOST_DELAY = 1.5        # segundos mínimos entre requests ao mesmo host
MAX_CONCURRENT_DOMAINS = 5

USER_AGENT = "DayOneVerify/1.0 (piloto; +https://dayone.com.br/bot)"

CRAWL_PATHS = [
    "",
    "/contact",
    "/contact-us",
    "/contato",
    "/about",
    "/about-us",
    "/sobre",
    "/team",
    "/equipe",
]

ALLOWED_CONTENT_TYPES = ("text/html", "text/plain")

_host_semaphores: dict[str, asyncio.Semaphore] = {}
_host_last_request: dict[str, float] = {}
_domain_semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOMAINS)
_host_locks: dict[str, asyncio.Lock] = {}


def _get_host_lock(host: str) -> asyncio.Lock:
    if host not in _host_locks:
        _host_locks[host] = asyncio.Lock()
    return _host_locks[host]


@dataclass
class CrawlPage:
    url: str
    status_code: int
    content_type: str
    body: bytes
    sha256: str
    contacts: ExtractedContact


@dataclass
class CrawlResult:
    base_url: str
    pages: list[CrawlPage] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    robots_disallowed: list[str] = field(default_factory=list)


class Crawler:
    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "Crawler":
        self._client = httpx.AsyncClient(
            follow_redirects=False,
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": USER_AGENT},
        )
        return self

    async def __aexit__(self, *args: object) -> None:
        if self._client:
            await self._client.aclose()

    def _make_client(self) -> httpx.AsyncClient:
        """Cria cliente httpx para uso em contextos sem with-statement."""
        return httpx.AsyncClient(
            follow_redirects=False,
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": USER_AGENT},
        )

    async def _get_robots(
        self, base_url: str, client: httpx.AsyncClient
    ) -> urllib.robotparser.RobotFileParser:
        rp = urllib.robotparser.RobotFileParser()
        robots_url = urljoin(base_url, "/robots.txt")
        try:
            validate_url(robots_url)
            resp = await client.get(robots_url)
            if resp.status_code == 200:
                rp.parse(resp.text.splitlines())
            else:
                rp.allow_all = True  # 4xx/5xx → sem restrições
        except (SSRFBlockedError, httpx.HTTPError):
            rp.allow_all = True  # inacessível → permite tudo
        return rp

    def _can_fetch(self, rp: urllib.robotparser.RobotFileParser, url: str) -> bool:
        return rp.can_fetch(USER_AGENT, url)

    async def _rate_limit(self, host: str) -> None:
        """Garante HOST_DELAY segundos entre requests ao mesmo host."""
        lock = _get_host_lock(host)
        async with lock:
            last = _host_last_request.get(host, 0.0)
            now = asyncio.get_event_loop().time()
            wait = HOST_DELAY - (now - last)
            if wait > 0:
                await asyncio.sleep(wait)
            _host_last_request[host] = asyncio.get_event_loop().time()

    async def _fetch(
        self, url: str, client: httpx.AsyncClient
    ) -> tuple[int, str, bytes] | None:
        """Faz GET com SSRF guard e redirect manual (máx 3 hops).

        Retorna (status_code, content_type, body) ou None em caso de erro.
        """
        try:
            validate_url(url)
        except SSRFBlockedError:
            raise

        host = urlparse(url).hostname or ""
        await self._rate_limit(host)

        current_url = url
        hops = 0
        while hops <= MAX_REDIRECTS:
            try:
                resp = await client.get(current_url)
            except httpx.HTTPError as exc:
                raise httpx.HTTPError(str(exc)) from exc

            if resp.is_redirect:
                hops += 1
                if hops > MAX_REDIRECTS:
                    raise httpx.TooManyRedirects(
                        f"Excedido MAX_REDIRECTS={MAX_REDIRECTS} para {url}"
                    )
                location = resp.headers.get("location", "")
                if not location:
                    return None
                next_url = urljoin(current_url, location)
                try:
                    validate_redirect(next_url)
                except SSRFBlockedError:
                    raise
                current_url = next_url
                next_host = urlparse(current_url).hostname or ""
                await self._rate_limit(next_host)
                continue

            # Verificar content-type
            ct = resp.headers.get("content-type", "")
            ct_base = ct.split(";")[0].strip().lower()
            if ct_base and not any(ct_base.startswith(a) for a in ALLOWED_CONTENT_TYPES):
                return None  # content-type inválido

            # Limitar tamanho do body — bytearray evita cópias O(n²)
            buf = bytearray()
            async for chunk in resp.aiter_bytes(chunk_size=8192):
                buf += chunk
                if len(buf) > MAX_BODY_BYTES:
                    del buf[MAX_BODY_BYTES:]
                    break

            return resp.status_code, ct_base or "text/html", bytes(buf)

        return None

    async def crawl_company(
        self,
        base_url: str,
        *,
        skip_robots: bool = False,
    ) -> CrawlResult:
        """Crawla URLs derivadas de base_url e extrai contatos.

        Nunca deve ser chamado para empresas FL (readiness_locked=True).
        """
        result = CrawlResult(base_url=base_url)

        async with _domain_semaphore:
            client = self._client or self._make_client()
            owns_client = self._client is None
            try:
                rp = await self._get_robots(base_url, client)

                for path in CRAWL_PATHS:
                    url = urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
                    # Normalizar root
                    if not path:
                        url = base_url if base_url.endswith("/") else base_url + "/"
                    full_url = urljoin(base_url, path) if path else base_url

                    if not skip_robots and not self._can_fetch(rp, full_url):
                        result.robots_disallowed.append(full_url)
                        continue

                    try:
                        fetch_result = await self._fetch(full_url, client)
                    except SSRFBlockedError as exc:
                        result.errors.append(f"SSRF blocked {full_url}: {exc}")
                        continue
                    except httpx.TooManyRedirects as exc:
                        result.errors.append(f"Too many redirects {full_url}: {exc}")
                        continue
                    except httpx.HTTPError as exc:
                        result.errors.append(f"HTTP error {full_url}: {exc}")
                        continue

                    if fetch_result is None:
                        continue

                    status, ct, body = fetch_result
                    sha = hashlib.sha256(body).hexdigest()

                    try:
                        html_text = body.decode("utf-8", errors="replace")
                    except Exception:
                        html_text = ""

                    contacts = extract_contacts(html_text, page_url=full_url)

                    result.pages.append(
                        CrawlPage(
                            url=full_url,
                            status_code=status,
                            content_type=ct,
                            body=body,
                            sha256=sha,
                            contacts=contacts,
                        )
                    )
            finally:
                if owns_client:
                    await client.aclose()

        return result
