"""SSRF guard — valida URLs e IPs antes de requisições HTTP.

Cobre: localhost, IPs privados, link-local, DNS rebinding, ULA IPv6.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

_BLOCKED_NETWORKS: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("198.18.0.0/15"),
    ipaddress.ip_network("240.0.0.0/4"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]


class SSRFBlockedError(ValueError):
    """URL ou IP bloqueado pelo SSRF guard."""


def is_private_ip(ip_str: str) -> bool:
    """Retorna True se o IP pertence a uma rede privada/reservada."""
    try:
        addr = ipaddress.ip_address(ip_str)
        return any(addr in net for net in _BLOCKED_NETWORKS)
    except ValueError:
        return True  # falha segura: IP inválido é tratado como privado


def validate_url(url: str) -> None:
    """Valida URL para SSRF. Raises SSRFBlockedError se insegura.

    Resolve DNS e verifica cada IP retornado (proteção contra DNS rebinding).
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise SSRFBlockedError(f"Scheme não permitido: {parsed.scheme!r}")
    host = parsed.hostname
    if not host:
        raise SSRFBlockedError("URL sem host")

    # IP literal — checar antes do DNS para mensagem de erro mais clara
    # Separar parse de validação para que SSRFBlockedError(ValueError)
    # não seja engolido pelo except ValueError abaixo.
    _is_ip_literal = False
    _ip_addr: ipaddress.IPv4Address | ipaddress.IPv6Address | None = None
    try:
        _ip_addr = ipaddress.ip_address(host)
        _is_ip_literal = True
    except ValueError:
        pass  # hostname, não IP literal

    if _is_ip_literal:
        if is_private_ip(str(_ip_addr)):
            raise SSRFBlockedError(f"IP literal privado bloqueado: {host}")
        return  # IP literal público — sem DNS necessário

    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise SSRFBlockedError(f"DNS resolution falhou para {host!r}: {exc}") from exc

    for _family, _type, _proto, _canonname, sockaddr in infos:
        ip = sockaddr[0]
        if is_private_ip(ip):
            raise SSRFBlockedError(
                f"DNS rebinding bloqueado: {host!r} resolve para IP privado {ip}"
            )


def validate_redirect(location: str) -> None:
    """Mesmas regras de validate_url, aplicada a cada hop de redirect."""
    validate_url(location)
