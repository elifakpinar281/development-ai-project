from src.capabilities.offline_analyzer import OfflineAnalyzer
from src.services.service_factory import case_history_arguments, injection_check, lookalike_checks
from tests.fakes import make_email, result


def analyzer_returning(*results):
    def check(email):
        return list(results)

    return OfflineAnalyzer(checks=[check])


def test_no_warnings_gives_likely_safe_with_a_careful_explanation():
    output = analyzer_returning(result("a", "success", 0)).analyze(make_email())
    assert output.verdict.verdict == "likely safe"
    assert output.verdict.is_fallback is True
    assert "not analysed" in output.verdict.explanation
    assert output.model_calls == 0


def test_score_is_highest_warning_plus_ten_per_extra_warning():
    output = analyzer_returning(
        result("a", "warning", 45),
        result("b", "warning", 30),
        result("c", "success", 0),
    ).analyze(make_email())
    assert output.verdict.risk_score == 55
    assert output.verdict.verdict == "suspicious"
    assert len(output.verdict.evidence) == 2


def test_safety_rules_also_apply_to_the_fallback():
    output = analyzer_returning(
        result("scan_prompt_injection", "warning", 30, injection_detected=True)
    ).analyze(make_email())
    assert output.verdict.verdict == "suspicious"
    assert output.verdict.safety_rules_applied == ["prompt_injection"]


def test_a_crashing_check_does_not_stop_the_fallback():
    def broken(email):
        raise RuntimeError("bug")

    def working(email):
        return [result("ok", "warning", 75)]

    output = OfflineAnalyzer(checks=[broken, working]).analyze(make_email())
    assert output.verdict.verdict == "phishing"
    assert output.verdict.unavailable_tools == ["broken"]
    assert [step.kind for step in output.trace][-1] == "final_answer"


def test_case_history_arguments_come_from_the_email():
    email = make_email(links=("https://login.hamk-helpdesk.com/a", "https://LOGIN.hamk-helpdesk.com/b", "https://evil.net/x"))
    assert case_history_arguments(email) == {
        "sender_domain": "hamk-helpdesk.com",
        "link_domains": ["evil.net", "login.hamk-helpdesk.com"],
        "subject": "Urgent: verify your account",
    }


def test_offline_checks_use_the_real_injection_scan_and_lookalike_tool():
    email = make_email(sender="security@paypa1.com", links=("https://paypa1.com/login",))
    assert injection_check(email)[0].tool_name == "scan_prompt_injection"
    lookalikes = lookalike_checks(email)
    assert len(lookalikes) == 1  # the same domain is checked once
    assert lookalikes[0].status == "warning"
    assert lookalikes[0].details["matched_brand"] == "paypal.com"
