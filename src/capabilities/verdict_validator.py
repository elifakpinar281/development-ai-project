from __future__ import annotations

import html
import re

from src.capabilities.verdict_policy import (
    DEFAULT_ACTIONS,
    apply_safety_rules,
    fit_score_to_verdict,
)
from src.schemas.agent import ModelVerdict, Observation
from src.schemas.responses import ParsedEmail, ToolResult, Verdict

MIN_QUOTE_CHARS = 4
USABLE_STATUSES = {"success", "warning"}


class VerdictValidator:

    def find_problems(
        self,
        answer: ModelVerdict,
        observations: list[Observation],
        email: ParsedEmail,
    ) -> list[str]:
        problems: list[str] = []
        by_id = _observations_by_id(observations)
        email_text = _searchable_email_text(email)

        if not answer.evidence:
            problems.append("The verdict needs at least one evidence item.")

        for number, item in enumerate(answer.evidence, start=1):
            label = f"Evidence item {number}"

            if item.source == "tool":
                if item.observation_id is None:
                    problems.append(f"{label} has source 'tool' but no observation_id.")
                    continue
                observation = by_id.get(item.observation_id)
                if observation is None:
                    problems.append(
                        f"{label} cites observation #{item.observation_id}, which does not exist."
                    )
                    continue
                if observation.result.status not in USABLE_STATUSES:
                    problems.append(
                        f"{label} cites observation #{item.observation_id} "
                        f"({observation.result.tool_name}), but that check did not run "
                        f"(status: {observation.result.status}). Do not use it as evidence."
                    )
                continue

            quote = _normalize(item.quote or "")
            if len(quote) < MIN_QUOTE_CHARS:
                problems.append(
                    f"{label} has source 'email_text' but no quote of at least {MIN_QUOTE_CHARS} characters."
                )
            elif quote not in email_text:
                problems.append(
                    f"{label} quotes \"{item.quote}\", but this text does not appear in the email. "
                    "Copy the words exactly or cite a tool observation instead."
                )

        return problems

    def build_verdict(
        self,
        answer: ModelVerdict,
        observations: list[Observation],
        is_fallback: bool = False,
    ) -> Verdict:
        by_id = _observations_by_id(observations)
        results = _results(observations)

        score = fit_score_to_verdict(answer.verdict, answer.risk_score)
        outcome = apply_safety_rules(answer.verdict, score, results)

        explanation = answer.explanation.strip()
        action = answer.recommended_action.strip()
        if outcome.verdict_raised:
            reasons = []
            for rule in outcome.applied:
                reasons.append(rule.reason)
            explanation += (
                f" SpotThePhish raised this verdict to \"{outcome.verdict}\" because " + "- and ".join(reasons) + "."
            )
            action = DEFAULT_ACTIONS[outcome.verdict]

        evidence: list[str] = []
        for item in answer.evidence:
            if item.source == "tool" and item.observation_id in by_id:
                tool_name = by_id[item.observation_id].result.tool_name
                evidence.append(f"[#{item.observation_id} {tool_name}] {item.claim}")
            else:
                evidence.append(f"[email text] \"{item.quote}\": {item.claim}")

        rule_ids = []
        for rule in outcome.applied:
            rule_ids.append(rule.rule_id)

        return Verdict(
            verdict=outcome.verdict,
            risk_score=outcome.risk_score,
            explanation=explanation,
            recommended_action=action,
            evidence=evidence,
            tools_run=tools_that_ran(results),
            unavailable_tools=tools_that_did_not_run(results),
            safety_rules_applied=rule_ids,
            is_fallback=is_fallback,
        )


# helpers
def tools_that_ran(results: list[ToolResult]) -> list[str]:
    names: list[str] = []
    for result in results:
        if result.status in USABLE_STATUSES and result.tool_name not in names:
            names.append(result.tool_name)
    return names


def tools_that_did_not_run(results: list[ToolResult]) -> list[str]:
    names: list[str] = []
    for result in results:
        if result.status not in USABLE_STATUSES and result.tool_name not in names:
            names.append(result.tool_name)
    return names


def _observations_by_id(observations: list[Observation]) -> dict[int, Observation]:
    by_id: dict[int, Observation] = {}
    for observation in observations:
        by_id[observation.observation_id] = observation
    return by_id


def _results(observations: list[Observation]) -> list[ToolResult]:
    results: list[ToolResult] = []
    for observation in observations:
        results.append(observation.result)
    return results


def _normalize(text: str) -> str:
    text = text.lower()
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    text = re.sub(r"\s+", " ", text)
    return text.strip().strip('"').strip()


def _searchable_email_text(email: ParsedEmail) -> str:
    parts = [email.subject, email.body_plain]
    if email.body_html:
        without_tags = re.sub(r"<[^>]+>", " ", email.body_html)
        parts.append(html.unescape(without_tags))
    if not email.body_plain and not email.body_html:
        parts.append(email.raw_text)
    sender = str(email.sender)
    if sender:
        parts.append(sender)
    return _normalize(" \n ".join(parts))
