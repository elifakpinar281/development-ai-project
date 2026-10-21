from src.email_parser import parse_email
from src.services.service_factory import scan_lookalike_domains

def _email(sender: str, link: str = "") -> str:
    body = f"Please check {link}" if link else "Hello"
    return (f"From: Someone <info@{sender}>\nTo: a@b.example\nSubject: Test\n"
            f"Date: Mon, 1 Jan 2026 10:00:00 +000\n\n{body}\n")

def test_automatic_scan_reports_the_riskiest_domain():
    result = scan_lookalike_domains(parse_email(_email("project-updates.example", "http://paypa1.example/verify")))
    assert result.tool_name == "check_lookalike_domain"
    assert result.status == "warning"
    assert result.details["matched_brand"] == "paypal.com"

def test_automatic_scan_is_quiet_for_harmless_domains():
    result = scan_lookalike_domains(parse_email(_email("lecture-office.example")))
    assert result.status == "success"