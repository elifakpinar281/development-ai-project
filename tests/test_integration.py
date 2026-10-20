import base64
from pathlib import Path

import pytest

from src.config import config
from src.exceptions import ModelUnavailableError
from src.schemas.agent import AnalysisStatus
from src.services.service_factory import build_analysis_service, case_history_arguments
from src.email_parser import parse_email
from tests.fakes import ScriptedModelClient, decide, final

CASES = Path(__file__).resolve().parents[1] / "evaluation" / "cases"

ATTACHMENT = base64.b64encode(b"MZ not a real program").decode()
RICH_PHISHING = f"""Return-Path: <bounce@mailer-xyz.example>
Authentication-Results: mx.hamk.fi; spf=fail smtp.mailfrom=mailer-xyz.example; dkim=fail header.d=paypal.com; dmarc=fail header.from=paypal.com
From: "PayPal Security" <service@paypal.com>
Reply-To: help@collect-data.example
To: student@hamk.fi
Subject: Your account is limited
MIME-Version: 1.0
Content-Type: multipart/mixed; boundary="X"

--X
Content-Type: text/html; charset="utf-8"

<p>Confirm your identity within 24 hours.</p><a href="http://192.168.4.20/login">https://www.paypal.com/login</a>
--X
Content-Type: application/octet-stream; name="invoice.pdf.exe"
Content-Disposition: attachment; filename="invoice.pdf.exe"
Content-Transfer-Encoding: base64

{ATTACHMENT}
--X--
"""


@pytest.fixture(autouse=True)
def no_case_history_writes(monkeypatch):
    """Never write test results into a real case-history database."""
    monkeypatch.setattr(config, "case_history_write", False)


def build(replies):
    model = ScriptedModelClient(replies)
    service = build_analysis_service(model_client=model, start_domain_server=False)
    return service, model


def read_case(name: str) -> str:
    return (CASES / name).read_text(encoding="utf-8")


def test_agent_run_on_a_real_eml_file():
    service, model = build([
        decide("analyze_links", reason="The email asks to click a link."),
        decide("check_email_authentication", reason="Check whether the sender is real."),
        decide(None),
        final("phishing", 85, evidence=[
            {"source": "tool", "observation_id": 2, "quote": None, "claim": "The link was checked."},
            {"source": "email_text", "observation_id": None,
             "quote": "confirm your identity within 24 hours", "claim": "Time pressure."},
        ]),
    ])

    result = service.analyze(read_case("01-lookalike-paypal.eml"))

    assert result.status == AnalysisStatus.COMPLETED
    assert result.verdict.verdict == "phishing"
    assert [step.tool_name for step in result.trace if step.kind == "tool_call"] == [
        "analyze_links", "check_email_authentication",
    ]
    assert "paypa1.example" in model.calls[0][1].content  # the parsed email reached the model


def test_all_email_checks_are_offered_to_the_model():
    service, model = build([decide(None), final(evidence=[
        {"source": "tool", "observation_id": 1, "quote": None, "claim": "Scan ran."}])])
    service.analyze(read_case("01-lookalike-paypal.eml"))
    tool_names = set(model.schemas[0]["properties"]["tool_name"]["anyOf"][0]["enum"])
    assert tool_names == {"check_email_authentication", "analyze_links", "inspect_attachments"}


def test_arguments_a_tool_does_not_take_are_refused():
    service, _ = build([
        decide("analyze_links", {"url": "http://attacker.example"}),
        decide(None),
        final(evidence=[{"source": "tool", "observation_id": 1, "quote": None, "claim": "Scan ran."}]),
    ])
    result = service.analyze(read_case("01-lookalike-paypal.eml"))
    assert result.trace[1].kind == "rejected_call"


def test_ollama_down_uses_the_offline_checks_of_person_1_and_3():
    service, _ = build([ModelUnavailableError("Ollama down")])

    result = service.analyze(RICH_PHISHING)

    assert result.status == AnalysisStatus.FALLBACK
    assert result.verdict.is_fallback is True
    assert result.verdict.verdict == "phishing"
    ran = set(result.verdict.tools_run)
    assert {"check_email_authentication", "analyze_links", "inspect_attachments", "scan_prompt_injection"} <= ran
    assert "is Ollama running?" in result.warnings[0]


def test_offline_fallback_flags_the_lookalike_domain():
    service, _ = build([ModelUnavailableError("Ollama down")])
    result = service.analyze(read_case("01-lookalike-paypal.eml"))
    assert result.verdict.verdict == "phishing"
    assert any("check_lookalike_domain" in item for item in result.verdict.evidence)


def test_model_saying_safe_is_overruled_by_a_high_risk_check():
    service, _ = build([
        decide("inspect_attachments"),
        decide(None),
        final("likely safe", 10, evidence=[
            {"source": "tool", "observation_id": 2, "quote": None, "claim": "Attachment checked."}]),
    ])
    result = service.analyze(RICH_PHISHING)
    assert result.verdict.verdict == "suspicious"
    assert "high_risk_finding" in result.verdict.safety_rules_applied


def test_pasted_body_without_headers():
    service, model = build([decide(None), final("suspicious", 50, evidence=[
        {"source": "email_text", "observation_id": None, "quote": "verify your password", "claim": "Asks for a password."}])])

    result = service.analyze("Dear user, your mailbox is full. Please verify your password at https://bit.ly/x")

    assert result.status == AnalysisStatus.COMPLETED
    assert any("no headers" in warning for warning in result.warnings)
    assert "Subject: (none)" in model.calls[0][1].content


def test_placeholder_subject_is_never_used_for_campaign_matching():
    email = parse_email("Dear user, click https://bit.ly/x now.")
    assert email.has_headers is False
    assert case_history_arguments(email)["subject"] == ""


@pytest.mark.parametrize("text", ["", "   "])
def test_empty_input_is_rejected_before_the_model(text):
    service, model = build([])
    assert service.analyze(text).status == AnalysisStatus.REJECTED
    assert model.calls == []