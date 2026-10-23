import pytest

from src.capabilities.analyzer import Analyzer
from src.capabilities.offline_analyzer import OfflineAnalyzer
from src.exceptions import (
    DatabaseUnavailableError,
    InvalidEmailInputError,
    InvalidModelOutputError,
    ModelUnavailableError,
    VerdictValidationError,
)
from src.schemas.agent import AnalysisStatus, AnalyzerOutput
from src.schemas.responses import Verdict
from src.services.ai_service import AnalysisService, format_result_as_text, generate_response
from tests.fakes import make_email, result


class StubAnalyzer(Analyzer):
    def __init__(self, name="agent", verdict=None, error=None):
        self.name = name
        self.verdict = verdict or Verdict(verdict="phishing", risk_score=90, explanation="Bad.", evidence=["x"])
        self.error = error
        self.emails = []

    def analyze(self, email):
        self.emails.append(email)
        if self.error is not None:
            raise self.error
        return AnalyzerOutput(analyzer_name=self.name, verdict=self.verdict, model_calls=3)


def parse_ok(text):
    return make_email(body=text)


def make_service(main=None, fallback=None, parser=parse_ok, recorder=None, **kwargs):
    main = main or StubAnalyzer()
    fallback = fallback or StubAnalyzer(
        "offline_fallback",
        Verdict(verdict="suspicious", risk_score=50, explanation="Offline.", is_fallback=True),
    )
    service = AnalysisService(
        parse_email=parser, main_analyzer=main, fallback_analyzer=fallback,
        case_recorder=recorder, max_input_chars=1000, **kwargs,
    )
    return service, main, fallback


@pytest.mark.parametrize("text", ["", "   \n\t", None])
def test_empty_input_is_rejected_without_calling_the_model(text):
    service, main, _ = make_service()
    outcome = service.analyze(text)
    assert outcome.status == AnalysisStatus.REJECTED
    assert "Paste an email" in outcome.message
    assert main.emails == []


def test_too_large_input_is_rejected():
    service, main, _ = make_service()
    outcome = service.analyze("x" * 1001)
    assert outcome.status == AnalysisStatus.REJECTED and "too large" in outcome.message
    assert main.emails == []


def test_parser_rejection_message_is_shown():
    def parser(text):
        raise InvalidEmailInputError("no headers or body", user_message="This does not look like an email.")

    service, main, _ = make_service(parser=parser)
    outcome = service.analyze("hello?")
    assert outcome.status == AnalysisStatus.REJECTED
    assert outcome.message == "This does not look like an email."


def test_successful_analysis_is_saved():
    saved = []
    service, _, _ = make_service(recorder=lambda email, verdict: saved.append(verdict.verdict))
    outcome = service.analyze("Subject: hi\n\nbody")
    assert outcome.status == AnalysisStatus.COMPLETED
    assert outcome.verdict.verdict == "phishing"
    assert outcome.analyzer_name == "agent" and outcome.model_calls == 3
    assert saved == ["phishing"]


@pytest.mark.parametrize("error", [
    ModelUnavailableError("Ollama down"),
    InvalidModelOutputError("bad json", raw_output="{"),
    VerdictValidationError(["invented evidence"]),
])
def test_model_errors_switch_to_the_fallback(error):
    service, main, fallback = make_service(main=StubAnalyzer(error=error))
    outcome = service.analyze("Subject: hi\n\nbody")
    assert outcome.status == AnalysisStatus.FALLBACK
    assert outcome.verdict.is_fallback is True
    assert outcome.warnings[0] == error.user_message
    assert len(fallback.emails) == 1


def test_ollama_down_warning_tells_the_user_what_to_do():
    service, _, _ = make_service(main=StubAnalyzer(error=ModelUnavailableError()))
    outcome = service.analyze("Subject: hi\n\nbody")
    assert "is Ollama running?" in outcome.warnings[0]


def test_database_failure_only_adds_a_warning():
    def recorder(email, verdict):
        raise DatabaseUnavailableError()

    service, _, _ = make_service(recorder=recorder)
    outcome = service.analyze("Subject: hi\n\nbody")
    assert outcome.status == AnalysisStatus.COMPLETED
    assert any("Case history is unavailable" in warning for warning in outcome.warnings)


def test_unavailable_checks_and_missing_headers_produce_warnings():
    verdict = Verdict(verdict="suspicious", risk_score=50, explanation="x", unavailable_tools=["check_domain_age"])
    service, _, _ = make_service(
        main=StubAnalyzer(verdict=verdict),
        parser=lambda text: make_email(has_headers=False),
        startup_warnings=["Domain checks are unavailable."],
    )
    outcome = service.analyze("body only")
    assert outcome.warnings[0] == "Domain checks are unavailable."
    assert any("check_domain_age" in warning for warning in outcome.warnings)
    assert any("no headers" in warning for warning in outcome.warnings)


def test_unexpected_bug_becomes_failed_and_is_never_raised():
    service, _, _ = make_service(main=StubAnalyzer(error=KeyError("bug")))
    outcome = service.analyze("Subject: hi\n\nbody")
    assert outcome.status == AnalysisStatus.FAILED
    assert outcome.verdict is None and "Something went wrong" in outcome.message


def test_unexpected_bug_still_fails_loudly_in_internal_method():
    service, _, _ = make_service(main=StubAnalyzer(error=KeyError("bug")))
    with pytest.raises(KeyError):
        service._analyze("Subject: hi\n\nbody", 0.0)


def test_real_offline_fallback_works_end_to_end():
    def checks(email):
        return [result("check_lookalike_domain", "warning", 90, "Imitates hamk.fi.")]

    service, _, _ = make_service(
        main=StubAnalyzer(error=ModelUnavailableError()),
        fallback=OfflineAnalyzer(checks=[checks]),
    )
    outcome = service.analyze("Subject: hi\n\nbody")
    assert outcome.status == AnalysisStatus.FALLBACK
    assert outcome.verdict.verdict == "phishing"
    assert outcome.verdict.is_fallback is True


def test_text_formatting_for_the_starter_ui():
    service, _, _ = make_service()
    text = generate_response("Subject: hi\n\nbody", service)
    assert text.startswith("PHISHING (risk 90/100)")
    rejected = format_result_as_text(service.analyze(""))
    assert "Paste an email" in rejected


@pytest.mark.parametrize("text", ["hello", "hi there", "test 1 2"])
def test_text_that_is_not_an_email_is_rejected_without_calling_the_model(text):
    def parser(raw):
        return make_email(body=raw, links=(), has_headers=False)

    service, main, fallback = make_service(parser=parser)
    outcome = service.analyze(text)
    assert outcome.status == AnalysisStatus.REJECTED
    assert "does not look like an email" in outcome.message
    assert main.emails == [] and fallback.emails == []


def test_short_text_with_a_link_is_still_analysed():
    def parser(raw):
        return make_email(body=raw, has_headers=False)

    service, main, _ = make_service(parser=parser)
    outcome = service.analyze("Click https://bit.ly/x now")
    assert outcome.status == AnalysisStatus.COMPLETED
    assert len(main.emails) == 1


def test_headerless_body_with_enough_words_is_still_analysed():
    def parser(raw):
        return make_email(body=raw, links=(), has_headers=False)

    service, main, _ = make_service(parser=parser)
    outcome = service.analyze("Please buy four gift cards for me today and send the codes.")
    assert outcome.status == AnalysisStatus.COMPLETED
    assert len(main.emails) == 1