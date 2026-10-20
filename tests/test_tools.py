"""Unit test for email inspection tools and pipeline fallback."""

from pathlib import Path
from src.capabilities.tools import (analyze_links, check_email_authentication, inspect_attachments)
from src.email_parser import parse_email
from src.pipeline import run_offline_fallback

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CASES_DIR = PROJECT_ROOT / "evaluation" / "cases"

def test_tool_auth_legit_email():
    """Legitimate email should pass SPF, DKIM, DMARC and have low risk."""

    email_data = parse_email(CASES_DIR / "p1-01-legit-hamk.eml")
    result = check_email_authentication(email_data)
    assert result.status == "success"
    assert result.risk_score < 20.0
    assert "no signs of sender forgery" in " ".join(result.evidence)

def test_tool_auth_spoofing_detection():
    """Spoofed helpdesk display name and failed SPF should trigger warning."""

    email_data = parse_email(CASES_DIR / "p1-02-auth-spoof-moodle.eml")
    result = check_email_authentication(email_data)
    assert result.status == "warning"
    assert result.risk_score >= 70.0
    assert any("helpdesk" in ev.lower() or "reply-to" in ev.lower() for ev in result.evidence)

def test_tool_attachment_double_extension():
    """Double extension must be caught by metadata inspection."""

    email_data = parse_email(CASES_DIR / "p1-03-attachment-double-ext.eml")
    result = inspect_attachments(email_data)
    assert result.status == "warning"
    assert result.risk_score >= 90.0
    assert any("double extension" in ev.lower() for ev in result.evidence)

def test_tool_links_mismatch_punycode():
    """Deceptive anchor text and Punycode homographs must be flagged."""

    email_data = parse_email(CASES_DIR / "p1-04-deceptive-links-punycode.eml")
    result = analyze_links(email_data)
    assert result.status == "warning"
    assert result.risk_score >= 80.0
    assert result.details["ip_links"] >= 1
    assert result.details["punycode_domains"] >= 1

def test_tool_headerless_raw_text():
    """Headerless raw pasted text returns warning on auth check and catches shorteners."""

    email_data = parse_email(CASES_DIR / "p1-05-headerless-raw-body.txt")
    auth_res = check_email_authentication(email_data)
    assert auth_res.status == "warning"
    assert not email_data.has_headers
    link_res = analyze_links(email_data)
    assert link_res.details["shorteners"] >=1

def test_offline_fallback_verdict():
    """Offline fallback should produce valid Verdict objects."""

    email_data = parse_email(CASES_DIR / "p1-03-attachment-double-ext.eml")
    verdict = run_offline_fallback(email_data)
    assert verdict.is_fallback is True
    assert verdict.verdict == "phishing"
    assert verdict.risk_score >= 65.0
    assert len(verdict.tools_run) == 3