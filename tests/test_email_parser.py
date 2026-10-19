from pathlib import Path

from src.email_parser import parse_email

PROJECT_ROOT = Path(__file__).resolve().parents[1]

MULTIPART = """From: PayPal <service@paypa1.example>
To: a@b.example
Subject: Alert
MIME-Version: 1.0
Content-Type: multipart/alternative; boundary="B"

--B
Content-Type: text/plain; charset="utf-8"

Click https://www.paypal.com/login now
--B
Content-Type: text/html; charset="utf-8"

<html><body><a href="http://evil-login.example/x">https://www.paypal.com/login</a></body></html>
--B--
"""


def test_multipart_html_link_mismatch_is_found():
    p = parse_email(MULTIPART)
    evil = [l for l in p.links if l.domain == "evil-login.example"]
    assert evil and evil[0].text_mismatch


def test_long_single_line_text_does_not_crash():
    p = parse_email("Click here " + "x" * 400)
    assert p.has_headers is False


def test_pasted_path_is_not_read_as_file():
    assert "/etc/hostname" in parse_email("/etc/hostname").body_plain


def test_eml_path_and_path_object_still_work():
    case = PROJECT_ROOT / "evaluation" / "cases" / "p3-01-lookalike-paypal.eml"
    assert parse_email(case).sender.domain == "paypa1.example"
    assert parse_email(str(case)).sender.domain == "paypa1.example"