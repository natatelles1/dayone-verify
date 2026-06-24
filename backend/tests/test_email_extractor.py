"""Testes email_extractor — classify_email + extract_contacts (Regra 11CC)."""
import pytest

from app.services.email_extractor import (
    ExtractedContact,
    classify_email,
    extract_contacts,
)


# ─── classify_email ───────────────────────────────────────────────────────────


class TestClassifyEmail:
    def test_company_domain(self):
        assert classify_email("billing@acme-accounting.com") == "COMPANY_DOMAIN"

    def test_gmail_is_generic(self):
        assert classify_email("owner@gmail.com") == "GENERIC_FREEMAIL"

    def test_outlook_is_generic(self):
        assert classify_email("user@outlook.com") == "GENERIC_FREEMAIL"

    def test_legalzoom_is_registered_agent(self):
        assert classify_email("agent@legalzoom.com") == "REGISTERED_AGENT"

    def test_northwest_is_registered_agent(self):
        assert classify_email("service@northwestregisteredagent.com") == "REGISTERED_AGENT"

    def test_empty_string_is_unknown(self):
        assert classify_email("") == "UNKNOWN"

    def test_no_at_sign_is_unknown(self):
        assert classify_email("notanemail") == "UNKNOWN"


# ─── extract_contacts ─────────────────────────────────────────────────────────


class TestExtractContacts:
    def test_extracts_company_email(self):
        html = "<html><body>Contact us: info@bestcpa.com</body></html>"
        result = extract_contacts(html)
        assert "info@bestcpa.com" in result.emails

    def test_extracts_generic_email(self):
        html = "<html><body>Email us at owner@gmail.com</body></html>"
        result = extract_contacts(html)
        assert "owner@gmail.com" in result.generic_emails
        assert "owner@gmail.com" not in result.emails

    def test_rule_11cc_registered_agent_silently_discarded(self):
        """Regra 11CC: e-mail de registered agent NÃO deve aparecer em nenhuma lista."""
        html = "<html><body>Agent: service@legalzoom.com, Contact: info@firm.com</body></html>"
        result = extract_contacts(html)
        # legalzoom descartado silenciosamente
        assert not any("legalzoom" in e for e in result.emails)
        assert not any("legalzoom" in e for e in result.generic_emails)
        # e-mail da empresa mantido
        assert "info@firm.com" in result.emails

    def test_deduplication(self):
        html = "<html><body>info@firm.com info@firm.com info@firm.com</body></html>"
        result = extract_contacts(html)
        assert result.emails.count("info@firm.com") == 1

    def test_noise_fragments_filtered(self):
        html = "<html><body>icon@2x.png placeholder@example.com</body></html>"
        result = extract_contacts(html)
        assert not any("@2x" in e for e in result.emails)
        assert not any("example.com" in e for e in result.emails)

    def test_extracts_us_phone(self):
        html = "<html><body>Call us: (415) 555-1234</body></html>"
        result = extract_contacts(html)
        assert "+14155551234" in result.phones

    def test_no_contacts_returns_empty(self):
        html = "<html><body><p>No contact info here.</p></body></html>"
        result = extract_contacts(html)
        assert result.emails == []
        assert result.generic_emails == []
        assert result.phones == []
