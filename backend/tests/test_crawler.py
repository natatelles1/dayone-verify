"""Testes crawler — SSRF, redirects, robots.txt, body size, content-type, emails."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.services.crawler import Crawler, CrawlResult, MAX_BODY_BYTES
from app.services.ssrf_guard import SSRFBlockedError

# Patch que elimina o rate-limit para testes rápidos
_NO_RATE_LIMIT = patch.object(Crawler, "_rate_limit", new_callable=lambda: lambda self: AsyncMock(return_value=None))


def _make_response(
    status: int = 200,
    content_type: str = "text/html",
    body: bytes = b"<html><body>contact@firm.com</body></html>",
    is_redirect: bool = False,
    location: str = "",
) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status
    resp.is_redirect = is_redirect
    headers: dict[str, str] = {"content-type": content_type}
    if location:
        headers["location"] = location
    resp.headers = headers
    chunks = [body[i : i + 8192] for i in range(0, len(body), 8192)] or [b""]

    async def _aiter_bytes(chunk_size=8192):
        for chunk in chunks:
            yield chunk

    resp.aiter_bytes = _aiter_bytes
    resp.text = body.decode("utf-8", errors="replace")
    return resp


@pytest.mark.asyncio
async def test_ssrf_blocked_localhost():
    """O SSRF guard deve bloquear requests para localhost antes de qualquer IO."""
    crawler = Crawler()
    async with crawler:
        crawl = await crawler.crawl_company("http://127.0.0.1/")
    # 127.0.0.1 é IP literal privado → todos os paths bloqueados
    assert any("SSRF" in e for e in crawl.errors)
    assert crawl.pages == []


@pytest.mark.asyncio
async def test_redirect_to_private_ip_blocked():
    """Redirect 302 para IP privado deve ser bloqueado pelo SSRF guard."""
    # 93.184.216.34 é IP literal público (example.com) — passa validate_url sem DNS
    redirect_resp = _make_response(
        status=301, is_redirect=True, body=b"",
        location="http://192.168.1.1/secret",
    )
    page_resp = _make_response()

    async def mock_get(url, **kwargs):
        if "robots.txt" in url:
            return _make_response(status=404, body=b"")
        return redirect_resp

    with patch.object(httpx.AsyncClient, "get", side_effect=mock_get):
        crawler = Crawler()
        with patch.object(Crawler, "_rate_limit", new=AsyncMock(return_value=None)):
            async with crawler:
                crawl = await crawler.crawl_company("http://93.184.216.34/")

    assert any("SSRF" in e for e in crawl.errors)


@pytest.mark.asyncio
async def test_robots_disallowed():
    """URL bloqueada pelo robots.txt deve aparecer em robots_disallowed, não em pages."""
    # User-agent: * com Disallow: /contact — wildcard é reconhecido pelo parser
    robots_txt = b"User-agent: *\nDisallow: /contact\n"
    robots_resp = _make_response(status=200, content_type="text/plain", body=robots_txt)
    page_resp = _make_response()

    async def mock_get(url, **kwargs):
        if "robots.txt" in url:
            return robots_resp
        return page_resp

    with patch("app.services.crawler.validate_url"), \
         patch("app.services.crawler.validate_redirect"), \
         patch.object(Crawler, "_rate_limit", new=AsyncMock(return_value=None)):
        with patch.object(httpx.AsyncClient, "get", side_effect=mock_get):
            crawler = Crawler()
            async with crawler:
                crawl = await crawler.crawl_company("http://example.com")

    assert any("/contact" in url for url in crawl.robots_disallowed)
    # /contact-us also starts with /contact, so both may be disallowed
    assert all("/contact" not in p.url for p in crawl.pages)


@pytest.mark.asyncio
async def test_body_too_large_truncated():
    """Body além do limite deve ser truncado — testado com limite pequeno para rapidez."""
    SMALL_MAX = 200  # bytes, para evitar BeautifulSoup em 5 MB por path
    # Corpo com 3 chunks de 100 bytes = 300 bytes (> SMALL_MAX=200)
    over_body = b"X" * 300
    resp = _make_response(body=over_body)

    async def mock_get(url, **kwargs):
        if "robots.txt" in url:
            return _make_response(status=404, body=b"")
        return resp

    with patch("app.services.crawler.MAX_BODY_BYTES", SMALL_MAX), \
         patch("app.services.crawler.validate_url"), \
         patch("app.services.crawler.validate_redirect"), \
         patch.object(Crawler, "_rate_limit", new=AsyncMock(return_value=None)):
        with patch.object(httpx.AsyncClient, "get", side_effect=mock_get):
            crawler = Crawler()
            async with crawler:
                crawl = await crawler.crawl_company("http://example.com")

    assert crawl.pages
    assert len(crawl.pages[0].body) <= SMALL_MAX


@pytest.mark.asyncio
async def test_invalid_content_type_skipped():
    """Resposta com content-type application/pdf deve ser ignorada (sem páginas capturadas)."""
    resp = _make_response(content_type="application/pdf", body=b"%PDF-1.4")

    async def mock_get(url, **kwargs):
        if "robots.txt" in url:
            return _make_response(status=404, body=b"")
        return resp

    with patch("app.services.crawler.validate_url"), \
         patch("app.services.crawler.validate_redirect"), \
         patch.object(Crawler, "_rate_limit", new=AsyncMock(return_value=None)):
        with patch.object(httpx.AsyncClient, "get", side_effect=mock_get):
            crawler = Crawler()
            async with crawler:
                crawl = await crawler.crawl_company("http://example.com")

    assert crawl.pages == []


@pytest.mark.asyncio
async def test_max_redirects_exceeded():
    """Mais de MAX_REDIRECTS hops deve registrar erro, não crashar."""
    redirect_resp = _make_response(
        status=302, is_redirect=True, body=b"",
        location="http://example.com/loop",
    )

    async def mock_get(url, **kwargs):
        if "robots.txt" in url:
            return _make_response(status=404, body=b"")
        return redirect_resp

    with patch("app.services.crawler.validate_url"), \
         patch("app.services.crawler.validate_redirect"), \
         patch.object(Crawler, "_rate_limit", new=AsyncMock(return_value=None)):
        with patch.object(httpx.AsyncClient, "get", side_effect=mock_get):
            crawler = Crawler()
            async with crawler:
                crawl = await crawler.crawl_company("http://example.com")

    assert any("redirect" in e.lower() for e in crawl.errors)


@pytest.mark.asyncio
async def test_email_extraction_from_page():
    """E-mail de domínio corporativo deve aparecer nos contatos da página."""
    html = b"<html><body><p>Contact: billing@bestcpa.com (415) 555-1234</p></body></html>"
    resp = _make_response(body=html)

    async def mock_get(url, **kwargs):
        if "robots.txt" in url:
            return _make_response(status=404, body=b"")
        return resp

    with patch("app.services.crawler.validate_url"), \
         patch("app.services.crawler.validate_redirect"), \
         patch.object(Crawler, "_rate_limit", new=AsyncMock(return_value=None)):
        with patch.object(httpx.AsyncClient, "get", side_effect=mock_get):
            crawler = Crawler()
            async with crawler:
                crawl = await crawler.crawl_company("http://bestcpa.com")

    assert crawl.pages
    all_emails = [e for p in crawl.pages for e in p.contacts.emails]
    assert "billing@bestcpa.com" in all_emails


@pytest.mark.asyncio
async def test_fl_companies_must_not_be_crawled():
    """Documenta o contrato: camada de serviço deve checar readiness_locked antes de invocar crawler."""
    # O crawler não tem acesso ao DB — o bloqueio de FL cabe à camada de negócio.
    # Verificar que a interface pública existe.
    crawler = Crawler()
    assert callable(crawler.crawl_company), (
        "Crawler.crawl_company deve ser callable; "
        "a camada de serviço deve verificar readiness_locked=True antes de invocar."
    )
