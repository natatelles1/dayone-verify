"""Testes SSRF guard — cada bloqueio tem teste comprovando recusa."""
import socket
from unittest.mock import patch

import pytest

from app.services.ssrf_guard import SSRFBlockedError, is_private_ip, validate_url


# ─── is_private_ip ─────────────────────────────────────────────────────────────


class TestIsPrivateIp:
    def test_localhost_127_blocked(self):
        assert is_private_ip("127.0.0.1") is True

    def test_loopback_ipv6_blocked(self):
        assert is_private_ip("::1") is True

    def test_private_10_blocked(self):
        assert is_private_ip("10.0.0.1") is True

    def test_private_172_blocked(self):
        assert is_private_ip("172.16.0.1") is True

    def test_private_192_blocked(self):
        assert is_private_ip("192.168.1.100") is True

    def test_link_local_blocked(self):
        assert is_private_ip("169.254.1.1") is True

    def test_cgnat_blocked(self):
        assert is_private_ip("100.64.0.1") is True

    def test_public_ip_allowed(self):
        assert is_private_ip("8.8.8.8") is False

    def test_invalid_string_returns_true(self):
        # falha segura: string inválida tratada como privada
        assert is_private_ip("not-an-ip") is True


# ─── validate_url ──────────────────────────────────────────────────────────────


class TestValidateUrl:
    def _make_addr_info(self, ip: str) -> list:
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (ip, 0))]

    def test_file_scheme_blocked(self):
        with pytest.raises(SSRFBlockedError, match="Scheme"):
            validate_url("file:///etc/passwd")

    def test_ftp_scheme_blocked(self):
        with pytest.raises(SSRFBlockedError, match="Scheme"):
            validate_url("ftp://example.com/file")

    def test_url_without_host_blocked(self):
        with pytest.raises(SSRFBlockedError, match="sem host"):
            validate_url("http://")

    def test_literal_loopback_blocked(self):
        with pytest.raises(SSRFBlockedError, match="IP literal privado"):
            validate_url("http://127.0.0.1/admin")

    def test_literal_private_10_blocked(self):
        with pytest.raises(SSRFBlockedError, match="IP literal privado"):
            validate_url("http://10.0.0.1/")

    def test_literal_link_local_blocked(self):
        with pytest.raises(SSRFBlockedError, match="IP literal privado"):
            validate_url("http://169.254.169.254/latest/meta-data/")

    def test_dns_failure_blocked(self):
        with patch("socket.getaddrinfo", side_effect=socket.gaierror("NXDOMAIN")):
            with pytest.raises(SSRFBlockedError, match="DNS resolution falhou"):
                validate_url("http://nonexistent-host-xyz.invalid/path")

    def test_dns_rebinding_blocked(self):
        """Host público que resolve para IP privado deve ser bloqueado."""
        with patch(
            "socket.getaddrinfo",
            return_value=self._make_addr_info("192.168.1.1"),
        ):
            with pytest.raises(SSRFBlockedError, match="DNS rebinding"):
                validate_url("http://legit-looking-domain.com/")

    def test_public_hostname_allowed(self):
        with patch(
            "socket.getaddrinfo",
            return_value=self._make_addr_info("93.184.216.34"),
        ):
            validate_url("http://example.com/")  # não deve lançar
