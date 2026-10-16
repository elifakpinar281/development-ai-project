from __future__ import annotations

import logging
import time
from typing import Callable

from src.capabilities.analyzer import Analyzer
from src.capabilities.verdict_policy import (
    DEFAULT_ACTIONS,
    apply_safety_rules,
    verdict_for_score,
)
from src.capabilities.verdict_validator import tools_that_did_not_run, tools_that_ran
from src.schemas.agent import AgentStep, AnalyzerOutput, Observation
from src.schemas.responses import ParsedEmail, ToolResult, Verdict

logger = logging.getLogger(__name__)

OfflineCheck = Callable[[ParsedEmail], list[ToolResult]]

EXTRA_WARNING_POINTS = 10


class OfflineAnalyzer(Analyzer):
    name = "offline_fallback"

    def __init__(self, checks: list[OfflineCheck], clock: Callable[[], float] = time.perf_counter):
        self.checks = checks
        self.clock = clock

    def analyze(self, email: ParsedEmail) -> AnalyzerOutput:
        observations: list[Observation] = []
        trace: list[AgentStep] = []

        for check in self.checks:
            started = self.clock()
            try:
                results = check(email)
            except Exception as error:  # one broken check must not stop the fallback
                logger.exception("Offline check %s failed", getattr(check, "__name__", check))
                results = [
                    ToolResult(
                        tool_name=getattr(check, "__name__", "offline_check"),
                        status="unavailable",
                        finding=f"Check could not be run: internal error ({type(error).__name__}).",
                    )
                ]
            duration_ms = int((self.clock() - started) * 1000)

            for result in results:
                observation = Observation(observation_id=len(observations) + 1, result=result)
                observations.append(observation)
                trace.append(
                    AgentStep(
                        step=len(trace) + 1,
                        kind="automatic_check",
                        reason="Offline fallback: every offline check is run.",
                        tool_name=result.tool_name,
                        observation_id=observation.observation_id,
                        status=result.status,
                        summary=result.finding,
                        duration_ms=duration_ms,
                    )
                )

        verdict = self._score(observations)
        trace.append(
            AgentStep(
                step=len(trace) + 1,
                kind="final_answer",
                status=verdict.verdict,
                summary=f"{verdict.verdict} (risk {verdict.risk_score:.0f}), rule-based fallback",
            )
        )
        return AnalyzerOutput(
            analyzer_name=self.name,
            verdict=verdict,
            trace=trace,
            observations=observations,
            model_calls=0,
        )

    def _score(self, observations: list[Observation]) -> Verdict:
        results: list[ToolResult] = []
        warnings: list[Observation] = []
        for observation in observations:
            results.append(observation.result)
            if observation.result.status == "warning":
                warnings.append(observation)

        highest = 0.0
        for observation in warnings:
            highest = max(highest, observation.result.risk_score)
        score = 0.0
        if warnings:
            score = min(100.0, highest + EXTRA_WARNING_POINTS * (len(warnings) - 1))

        outcome = apply_safety_rules(verdict_for_score(score), int(round(score)), results)

        evidence: list[str] = []
        for observation in warnings:
            evidence.append(
                f"[#{observation.observation_id} {observation.result.tool_name}] {observation.result.finding}"
            )

        ran = tools_that_ran(results)
        explanation = (
            "The AI model was not available, so SpotThePhish ran only its offline checks "
            f"({len(ran)} ran). "
        )
        if warnings:
            explanation += f"{len(warnings)} of them raised a warning. See the evidence list. "
        else:
            explanation += "None of them raised a warning. "
        explanation += (
            "Language-based signs such as urgency or requests for passwords were not analysed, "
            "so treat this result with extra care."
        )

        rule_ids = []
        for rule in outcome.applied:
            rule_ids.append(rule.rule_id)

        return Verdict(
            verdict=outcome.verdict,
            risk_score=outcome.risk_score,
            explanation=explanation,
            recommended_action=DEFAULT_ACTIONS[outcome.verdict],
            evidence=evidence,
            tools_run=ran,
            unavailable_tools=tools_that_did_not_run(results),
            safety_rules_applied=rule_ids,
            is_fallback=True,
        )
