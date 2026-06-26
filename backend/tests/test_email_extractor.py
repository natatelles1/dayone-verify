"""Testes email_extractor — classify_email + extract_contacts (Regra 11CC + placeholders)."""
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

    # ── Placeholders ──────────────────────────────────────────────────────────

    def test_user_at_domain_com_is_placeholder(self):
        assert classify_email("user@domain.com") == "PLACEHOLDER"

    def test_name_at_domain_com_is_placeholder(self):
        assert classify_email("name@domain.com") == "PLACEHOLDER"

    def test_your_at_email_com_is_placeholder(self):
        assert classify_email("your@email.com") == "PLACEHOLDER"

    def test_contact_at_yourdomain_is_placeholder(self):
        assert classify_email("contact@yourdomain.com") == "PLACEHOLDER"

    def test_info_at_yourcompany_is_placeholder(self):
        assert classify_email("info@yourcompany.com") == "PLACEHOLDER"

    def test_example_org_is_placeholder(self):
        assert classify_email("admin@example.org") == "PLACEHOLDER"

    def test_real_company_email_not_placeholder(self):
        """Endereço com domínio próprio real nunca deve ser confundido com placeholder."""
        assert classify_email("info@myaccountaholics.com") == "COMPANY_DOMAIN"
        assert classify_email("info@faithworkstax.com") == "COMPANY_DOMAIN"

    # ── Site-builder subdomínios ──────────────────────────────────────────────

    def test_hostingersite_subdomain_is_placeholder(self):
        assert classify_email("hello@nexusworksllc-com-976359.hostingersite.com") == "PLACEHOLDER"

    def test_wixsite_subdomain_is_placeholder(self):
        assert classify_email("contact@mysite.wixsite.com") == "PLACEHOLDER"

    def test_weebly_subdomain_is_placeholder(self):
        assert classify_email("info@mycompany.weebly.com") == "PLACEHOLDER"

    def test_godaddysites_subdomain_is_placeholder(self):
        assert classify_email("hello@myslug.godaddysites.com") == "PLACEHOLDER"

    def test_builder_root_domain_is_placeholder(self):
        """Domínio raiz do builder também é placeholder (sem subdomínio)."""
        assert classify_email("user@wixsite.com") == "PLACEHOLDER"

    def test_builder_email_discarded_from_extract(self):
        """E-mail de builder não aparece em nenhuma lista de extract_contacts."""
        html = (
            "<html><body>"
            "hello@nexusworksllc-com-976359.hostingersite.com | "
            "info@nexusworksllc.com"
            "</body></html>"
        )
        result = extract_contacts(html)
        assert not any("hostingersite" in e for e in result.emails)
        assert not any("hostingersite" in e for e in result.generic_emails)
        assert "info@nexusworksllc.com" in result.emails

    # ── Freemail nunca vira COMPANY_DOMAIN ────────────────────────────────────

    def test_gmail_is_never_company_domain(self):
        assert classify_email("owner@gmail.com") != "COMPANY_DOMAIN"

    def test_hotmail_is_never_company_domain(self):
        assert classify_email("taxes@hotmail.com") != "COMPANY_DOMAIN"

    def test_protonmail_is_generic(self):
        assert classify_email("private@protonmail.com") == "GENERIC_FREEMAIL"


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

    def test_placeholder_domain_not_in_any_list(self):
        """user@domain.com não deve aparecer em emails nem generic_emails."""
        html = "<html><body>Contact: user@domain.com or name@domain.com</body></html>"
        result = extract_contacts(html)
        assert not any("domain.com" in e for e in result.emails)
        assert not any("domain.com" in e for e in result.generic_emails)

    def test_real_email_survives_alongside_placeholder(self):
        """Email real não é descartado quando aparece junto com placeholder."""
        html = "<html><body>user@domain.com | info@salestaxhelper.com</body></html>"
        result = extract_contacts(html)
        assert "info@salestaxhelper.com" in result.emails
        assert not any("domain.com" in e for e in result.emails)

    def test_freemail_never_in_emails_list(self):
        """Gmail/hotmail nunca deve aparecer na lista emails (COMPANY_DOMAIN)."""
        html = "<html><body>taxes@hotmail.com owner@gmail.com info@realfirm.com</body></html>"
        result = extract_contacts(html)
        assert not any("gmail" in e or "hotmail" in e for e in result.emails)
        assert "info@realfirm.com" in result.emails

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
