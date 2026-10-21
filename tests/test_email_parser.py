"""Tests for email parser edge cases and format handling."""

from pathlib import Path
from src.email_parser import decode_mime_header, parse_email

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_multipart_html_link_mismatch_is_found():
    """Multipart email with deceptive anchor text is properly extracted."""
    multipart_raw = """From: PayPal <service@paypa1.example>
To: student@hamk-test.example
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
    p = parse_email(multipart_raw)
    evil = [l for l in p.links if l.domain == "evil-login.example"]
    assert evil and evil[0].text_mismatch


def test_edge_case_no_headers_raw_text():
    """Edge case 1: Raw pasted email body with no RFC headers."""
    raw_body = "Your package is waiting. Click here to confirm delivery: http://bit.ly/pkg-track"
    p = parse_email(raw_body)

    assert p.has_headers is False
    assert p.subject == "(No Subject - Raw Text)"
    assert p.body_plain == raw_body
    assert len(p.links) == 1
    assert p.links[0].is_shortener is True


def test_edge_case_html_only_body():
    """Edge case 2: Email with HTML body only; extracts text and strips scripts/styles."""
    html_email = """From: news@service.example
To: user@hamk-test.example
Subject: Newsletter
MIME-Version: 1.0
Content-Type: text/html; charset="utf-8"

<html>
<head><style>body { color: red; }</style></head>
<body>
<script>alert('test');</script>
<p>Welcome to our service.</p>
<p>Read more at <a href="https://example.com/info">our site</a>.</p>
</body>
</html>
"""
    p = parse_email(html_email)
    assert p.has_headers is True
    assert "alert" not in p.body_plain
    assert "Welcome to our service" in p.body_plain
    assert len(p.links) == 1
    assert p.links[0].url == "https://example.com/info"


def test_edge_case_encoded_subject():
    """Edge case 3: RFC 2047 encoded subject line decoding."""
    # Encoded: "Täällä on viesti" (Finnish UTF-8 base64)
    encoded_header = "=?UTF-8?B?VMOkw6RsbMOkIG9uIHZpZXN0aQ==?="
    decoded = decode_mime_header(encoded_header)
    assert decoded == "Täällä on viesti"

    raw_email = f"""From: sender@hamk.fi
To: user@hamk.fi
Subject: {encoded_header}
MIME-Version: 1.0
Content-Type: text/plain; charset="utf-8"

Hello!
"""
    p = parse_email(raw_email)
    assert p.subject == "Täällä on viesti"


def test_long_single_line_text_does_not_crash():
    p = parse_email("Click here " + "x" * 400)
    assert p.has_headers is False


def test_pasted_path_is_not_read_as_file():
    assert "/etc/hostname" in parse_email("/etc/hostname").body_plain


def test_eml_path_and_path_object_still_work():
    case = PROJECT_ROOT / "evaluation" / "cases" / "29-legit-hamk.eml"
    assert parse_email(case).sender.domain == "hamk.fi"
    assert parse_email(str(case)).sender.domain == "hamk.fi"