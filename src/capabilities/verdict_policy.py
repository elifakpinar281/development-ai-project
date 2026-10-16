from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from src.capabilities.injection_scan import TOOL_NAME as INJECTION_SCAN_TOOL
from src.schemas.responses import ToolResult, VerdictType

SEVERITY: dict[str, int] = {"likely safe": 0, "suspicious": 1, "phishing": 2}

SCORE_BANDS: dict[str, tuple[int, int]] = {
    "likely safe": (0, 39),
    "suspicious": (40, 69),
    "phishing": (70, 100),
}

DEFAULT_ACTIONS: dict[str, str] = {
    "phishing": (
        "Do not click any links, open attachments or reply. Report the email to "
        "your IT support or security team, then delete it."
    ),
    "suspicious": (
        "Do not click links or open attachments yet. Contact the sender through a "
        "channel you already know (not the details in this email), or ask IT support."
    ),
    "likely safe": (
        "No strong warning signs were found. Stay careful with any requests for "
        "passwords, payments or personal data."
    ),
}


def verdict_for_score(score: float) -> VerdictType:
    for verdict in ("phishing", "suspicious"):
        low, _high = SCORE_BANDS[verdict]
        if score >= low:
            return verdict
    return "likely safe"


def fit_score_to_verdict(verdict: str, score: float) -> int:
    low, high = SCORE_BANDS[verdict]
    return int(min(max(round(score), low), high))


def is_stricter(candidate: str, current: str) -> bool:
    return SEVERITY[candidate] > SEVERITY[current]


def _url_listed_by_reputation_service(results: list[ToolResult]) -> bool:
    for result in results:
        if result.tool_name == "check_url_reputation" and result.details.get("listed") is True:
            return True
    return False


def _known_phishing_campaign(results: list[ToolResult]) -> bool:
    for result in results:
        if result.tool_name != "lookup_case_history":
            continue
        if result.details.get("same_sender_phishing") or result.details.get("same_subject_phishing"):
            return True
    return False


def _prompt_injection_found(results: list[ToolResult]) -> bool:
    for result in results:
        if result.tool_name == INJECTION_SCAN_TOOL and result.details.get("injection_detected") is True:
            return True
    return False


def _high_risk_finding(results: list[ToolResult]) -> bool:
    for result in results:
        if result.status == "warning" and result.risk_score >= 80:
            return True
    return False


@dataclass(frozen=True)
class SafetyRule:
    rule_id: str
    reason: str  # completes the sentence "... because <reason>."
    minimum_verdict: VerdictType
    minimum_score: int
    applies: Callable[[list[ToolResult]], bool]


SAFETY_RULES: list[SafetyRule] = [
    SafetyRule(
        rule_id="url_listed_as_malicious",
        reason="a reputation service lists a link in this email as malicious",
        minimum_verdict="phishing",
        minimum_score=90,
        applies=_url_listed_by_reputation_service,
    ),
    SafetyRule(
        rule_id="known_phishing_campaign",
        reason="an earlier email with the same sender or subject was judged to be phishing",
        minimum_verdict="suspicious",
        minimum_score=60,
        applies=_known_phishing_campaign,
    ),
    SafetyRule(
        rule_id="prompt_injection",
        reason="the email contains hidden instructions aimed at AI tools, a common evasion trick",
        minimum_verdict="suspicious",
        minimum_score=50,
        applies=_prompt_injection_found,
    ),
    SafetyRule(
        rule_id="high_risk_finding",
        reason="at least one check reported a high-risk warning",
        minimum_verdict="suspicious",
        minimum_score=50,
        applies=_high_risk_finding,
    ),
]


@dataclass
class RuleOutcome:
    verdict: VerdictType
    risk_score: int
    applied: list[SafetyRule]
    verdict_raised: bool


def apply_safety_rules(verdict: VerdictType, risk_score: int, results: list[ToolResult]) -> RuleOutcome:
    original_verdict = verdict
    applied: list[SafetyRule] = []

    for rule in SAFETY_RULES:
        if not rule.applies(results):
            continue
        changed = False
        if is_stricter(rule.minimum_verdict, verdict):
            verdict = rule.minimum_verdict
            changed = True
        if risk_score < rule.minimum_score:
            risk_score = rule.minimum_score
            changed = True
        if changed:
            applied.append(rule)

    risk_score = fit_score_to_verdict(verdict, risk_score)
    return RuleOutcome(
        verdict=verdict,
        risk_score=risk_score,
        applied=applied,
        verdict_raised=verdict != original_verdict,
    )
