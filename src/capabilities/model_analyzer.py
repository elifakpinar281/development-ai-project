
from __future__ import annotations
 
import time
from typing import Callable, Optional
 
from pydantic import ValidationError
 
from src.capabilities import prompts
from src.capabilities.analyzer import Analyzer
from src.capabilities.tool_registry import EmailScope
from src.capabilities.verdict_validator import VerdictValidator
from src.exceptions import InvalidModelOutputError, VerdictValidationError
from src.models.model_client import ModelClient
from src.schemas.agent import (
    AgentStep,
    AnalyzerOutput,
    ChatMessage,
    ModelVerdict,
    Observation,
)
from src.schemas.responses import ParsedEmail, ToolResult, Verdict
 
AutomaticCheck = Callable[[ParsedEmail], ToolResult]
 
 
class AnalysisRun:
 
    def __init__(self, email: ParsedEmail, boundary: str):
        self.email = email
        self.scope = EmailScope(email)
        self.boundary = boundary
        self.messages: list[ChatMessage] = []
        self.observations: list[Observation] = []
        self.trace: list[AgentStep] = []
        self.signatures: dict[str, int] = {} 
        self.steps_used = 0
        self.model_calls = 0
 
    def add_observation(self, result: ToolResult) -> Observation:
        observation = Observation(observation_id=len(self.observations) + 1, result=result)
        self.observations.append(observation)
        return observation
 
    def add_step(self, **fields) -> None:
        self.trace.append(AgentStep(step=len(self.trace) + 1, **fields))
 
 
class ModelAnalyzer(Analyzer):
 
    def __init__(
        self,
        model_client: ModelClient,
        validator: VerdictValidator,
        max_repairs: int = 1,
        max_body_chars: int = 6000,
        automatic_checks: Optional[list[AutomaticCheck]] = None,
        boundary_factory: Callable[[], str] = prompts.new_boundary,
        clock: Callable[[], float] = time.perf_counter,
    ):
        self.model_client = model_client
        self.validator = validator
        self.max_repairs = max_repairs
        self.max_body_chars = max_body_chars
        self.automatic_checks = automatic_checks or []
        self.boundary_factory = boundary_factory
        self.clock = clock
 
 
    def _start_run(self, email: ParsedEmail) -> AnalysisRun:
        return AnalysisRun(email, self.boundary_factory())
 
    def _run_automatic_checks(self, run: AnalysisRun, reason: str) -> list[Observation]:
        observations = []
        for check in self.automatic_checks:
            started = self.clock()
            result = check(run.email)
            observation = run.add_observation(result)
            observations.append(observation)
            run.add_step(
                kind="automatic_check",
                reason=reason,
                tool_name=result.tool_name,
                observation_id=observation.observation_id,
                status=result.status,
                summary=result.finding,
                duration_ms=self._ms_since(started),
            )
        return observations
 
    def _ask_model(self, run: AnalysisRun, schema: dict) -> str:
        run.model_calls += 1
        return self.model_client.chat(run.messages, response_schema=schema)
 
    def _request_verdict(self, run: AnalysisRun) -> Verdict:
        schema = prompts.verdict_schema()
        raw = self._ask_model(run, schema)
 
        for attempt in range(self.max_repairs + 1):
            answer: Optional[ModelVerdict] = None
            parse_error: Optional[ValidationError] = None
            try:
                answer = ModelVerdict.model_validate_json(prompts.extract_json(raw))
                problems = self.validator.find_problems(answer, run.observations, run.email)
            except ValidationError as error:
                parse_error = error
                problems = validation_problems(error)
 
            if not problems:
                verdict = self.validator.build_verdict(answer, run.observations)
                summary = f"{verdict.verdict} (risk {verdict.risk_score:.0f})"
                if verdict.safety_rules_applied:
                    summary += "; safety rules applied: " + ", ".join(verdict.safety_rules_applied)
                run.add_step(kind="final_answer", status=verdict.verdict, summary=summary)
                return verdict
 
            what = "verdict format" if parse_error is not None else "verdict (evidence check failed)"
            run.add_step(kind="invalid_output", summary=f"Invalid {what}: " + "; ".join(problems))
 
            if attempt == self.max_repairs:
                if parse_error is not None:
                    raise InvalidModelOutputError(
                        "The model did not return a valid verdict.", raw_output=raw
                    ) from parse_error
                raise VerdictValidationError(problems)
 
            run.messages.append(prompts.assistant_message(raw[:3000]))
            run.messages.append(prompts.user_message(prompts.invalid_output_message(problems, "verdict")))
            raw = self._ask_model(run, schema)
 
        raise VerdictValidationError(["The model did not return a valid verdict."])
 
    def _output(self, run: AnalysisRun, verdict: Verdict) -> AnalyzerOutput:
        return AnalyzerOutput(
            analyzer_name=self.name,
            verdict=verdict,
            trace=run.trace,
            observations=run.observations,
            model_calls=run.model_calls,
        )
 
    def _ms_since(self, started: float) -> int:
        return int((self.clock() - started) * 1000)
 
 
def validation_problems(error: ValidationError) -> list[str]:
    problems = []
    for item in error.errors()[:5]:
        location = ".".join(str(part) for part in item.get("loc", ())) or "reply"
        message = item.get("msg", "invalid value")
        if item.get("type") == "json_invalid":
            problems.append("The reply is not valid JSON.")
        else:
            problems.append(f"{location}: {message}")
    return problems or ["The reply could not be read."]
 
