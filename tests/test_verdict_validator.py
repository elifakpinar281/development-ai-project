import pytest

from src.capabilities.verdict_policy import (
    apply_safety_rules,
    fit_score_to_verdict,
    verdict_for_score,
)
from src.capabilities.verdict_validator import VerdictValidator
from src.schemas.agent import EvidenceCitation, ModelVerdict, Observation
from tests.fakes import make_email, result


def answer(verdict="phishing", score=85, evidence=None):
    if evidence is None:
        evidence = [EvidenceCitation(source="tool", observation_id=1, claim="Lookalike domain.")]
    return ModelVerdict(
        verdict=verdict, risk_score=score, explanation="Explained.",
        recommended_action="Report it.", evidence=evidence,
    )


def observations(*results):
    items = []
    for number, item in enumerate(results, start=1):
        items.append(Observation(observation_id=number, result=item))
    return items


validator = VerdictValidator()

def test_valid_tool_citation_has_no_problems():
    obs = observations(result("check_lookalike_domain", "warning", 90))
    assert validator.find_problems(answer(), obs, make_email()) == []


def test_no_evidence_is_a_problem():
    problems = validator.find_problems(answer(evidence=[]), [], make_email())
    assert problems == ["The verdict needs at least one evidence item."]


@pytest.mark.parametrize("status", ["unavailable", "error"])
def test_citing_a_check_that_did_not_run_is_a_problem(status):
    obs = observations(result("check_url_reputation", status))
    problems = validator.find_problems(answer(), obs, make_email())
    assert "did not run" in problems[0]


def test_tool_citation_without_id_is_a_problem():
    evidence = [EvidenceCitation(source="tool", observation_id=None, claim="x")]
    assert "no observation_id" in validator.find_problems(answer(evidence=evidence), [], make_email())[0]


def test_quote_must_appear_in_the_email_ignoring_case_spaces_and_curly_quotes():
    email = make_email(body="Your account   will be\nclosed TODAY. Click “Verify” now.")
    good = [EvidenceCitation(source="email_text", quote='will be closed today. click "verify"', claim="Urgency.")]
    bad = [EvidenceCitation(source="email_text", quote="send your PIN", claim="x")]
    short = [EvidenceCitation(source="email_text", quote="now", claim="x")]
    assert validator.find_problems(answer(evidence=good), [], email) == []
    assert "does not appear in the email" in validator.find_problems(answer(evidence=bad), [], email)[0]
    assert "at least 4 characters" in validator.find_problems(answer(evidence=short), [], email)[0]


def test_quote_from_the_subject_is_accepted():
    evidence = [EvidenceCitation(source="email_text", quote="Urgent: verify your account", claim="Urgent subject.")]
    assert validator.find_problems(answer(evidence=evidence), [], make_email()) == []


def test_score_is_moved_into_the_band_of_the_verdict():
    obs = observations(result("check_lookalike_domain", "success", 0))
    verdict = validator.build_verdict(answer("phishing", 20), obs)
    assert verdict.verdict == "phishing" and verdict.risk_score == 70


def test_reputation_listing_forces_phishing_and_overrides_the_action():
    obs = observations(
        result("check_lookalike_domain", "success", 0),
        result("check_url_reputation", "warning", 95, listed=True),
    )
    verdict = validator.build_verdict(answer("likely safe", 10), obs)
    assert verdict.verdict == "phishing" and verdict.risk_score >= 90
    assert verdict.safety_rules_applied == ["url_listed_as_malicious"]
    assert "Do not click" in verdict.recommended_action
    assert "raised this verdict" in verdict.explanation


def test_safety_rules_use_all_results_even_if_not_cited():
    obs = observations(
        result("check_lookalike_domain", "success", 0),
        result("lookup_case_history", "warning", 70, same_sender_phishing=True),
    )
    verdict = validator.build_verdict(answer("likely safe", 5), obs)
    assert verdict.verdict == "suspicious" and verdict.risk_score == 60


def test_safety_rules_never_lower_a_verdict():
    obs = observations(result("scan_prompt_injection", "warning", 60, injection_detected=True))
    verdict = validator.build_verdict(answer("phishing", 95), obs)
    assert verdict.verdict == "phishing" and verdict.risk_score == 95
    assert verdict.safety_rules_applied == []


def test_tools_run_and_unavailable_tools_are_reported():
    obs = observations(
        result("check_lookalike_domain", "warning", 90),
        result("check_domain_age", "unavailable"),
    )
    verdict = validator.build_verdict(answer(), obs)
    assert verdict.tools_run == ["check_lookalike_domain"]
    assert verdict.unavailable_tools == ["check_domain_age"]


@pytest.mark.parametrize("score,expected", [(0, "likely safe"), (39, "likely safe"), (40, "suspicious"), (69, "suspicious"), (70, "phishing"), (100, "phishing")])
def test_verdict_for_score(score, expected):
    assert verdict_for_score(score) == expected


def test_fit_score_to_verdict():
    assert fit_score_to_verdict("likely safe", 80) == 39
    assert fit_score_to_verdict("suspicious", 12) == 40
    assert fit_score_to_verdict("phishing", 88.6) == 89


def test_high_risk_warning_rule():
    outcome = apply_safety_rules("likely safe", 0, [result("inspect_attachments", "warning", 85)])
    assert outcome.verdict == "suspicious" and outcome.verdict_raised
    assert [rule.rule_id for rule in outcome.applied] == ["high_risk_finding"]
