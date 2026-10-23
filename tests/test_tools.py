"""Unit tests for the email inspection tools and the offline fallback of the app."""

from pathlib import Path
from src.capabilities.tools import (analyze_links, check_email_authentication, inspect_attachments)
from src.email_parser import parse_email
from src.services.service_factory import build_components
from tests.fakes import ScriptedModelClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CASES_DIR = PROJECT_ROOT / "evaluation" / "cases"

def test_tool_auth_legit_email():
    """Legitimate email should pass SPF, DKIM, DMARC and have low risk."""

    email_data = parse_email(CASES_DIR / "29-legit-hamk.eml")
    result = check_email_authentication(email_data)
    assert result.status == "success"
    assert result.risk_score < 20.0
    assert "no signs of sender forgery" in " ".join(result.evidence)

def test_tool_auth_spoofing_detection():
    """Spoofed helpdesk display name and failed SPF should trigger warning."""

    email_data = parse_email(CASES_DIR / "30-auth-spoof-moodle.eml")
    result = check_email_authentication(email_data)
    assert result.status == "warning"
    assert result.risk_score >= 70.0
    assert any("helpdesk" in ev.lower() or "reply-to" in ev.lower() for ev in result.evidence)

def test_tool_attachment_double_extension():
    """Double extension must be caught by metadata inspection."""

    email_data = parse_email(CASES_DIR / "31-attachment-double-ext.eml")
    result = inspect_attachments(email_data)
    assert result.status == "warning"
    assert result.risk_score >= 90.0
    assert any("double extension" in ev.lower() for ev in result.evidence)

def test_tool_links_mismatch_punycode():
    """Deceptive anchor text and Punycode homographs must be flagged."""

    email_data = parse_email(CASES_DIR / "32-deceptive-links-punycode.eml")
    result = analyze_links(email_data)
    assert result.status == "warning"
    assert result.risk_score >= 80.0
    assert result.details["ip_links"] >= 1
    assert result.details["punycode_domains"] >= 1

def test_tool_headerless_raw_text():
    """Headerless raw pasted text returns warning on auth check and catches shorteners."""

    email_data = parse_email(CASES_DIR / "33-headerless-raw-body.eml")
    auth_res = check_email_authentication(email_data)
    assert auth_res.status == "warning"
    assert not email_data.has_headers
    link_res = analyze_links(email_data)
    assert link_res.details["shorteners"] >=1

def test_offline_fallback_verdict():
    """The offline fallback that the app really uses should produce a valid Verdict."""

    email_data = parse_email(CASES_DIR / "31-attachment-double-ext.eml")
    components = build_components(model_client=ScriptedModelClient([]), start_domain_server=False)
    verdict = components.fallback.analyze(email_data).verdict
    assert verdict.is_fallback is True
    assert verdict.verdict == "phishing"
    assert verdict.risk_score >= 65.0
    assert "inspect_attachments" in verdict.tools_run
    assert "scan_prompt_injection" in verdict.tools_run