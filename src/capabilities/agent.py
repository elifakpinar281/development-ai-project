from __future__ import annotations

import logging
import time
from typing import Callable, Optional

from pydantic import ValidationError

from src.capabilities import prompts
from src.capabilities.analyzer import Analyzer
from src.capabilities.tool_registry import EmailScope, PreparedCall, ToolRegistry
from src.capabilities.verdict_validator import VerdictValidator
from src.exceptions import InvalidModelOutputError, InvalidToolCallError, VerdictValidationError
from src.models.model_client import ModelClient
from src.schemas.agent import (
    AgentDecision,
    AgentStep,
    AnalyzerOutput,
    ChatMessage,
    ModelVerdict,
    Observation,
)
from src.schemas.responses import ParsedEmail, ToolResult

logger = logging.getLogger(__name__)

AutomaticCheck = Callable[[ParsedEmail], ToolResult]


class _Run:
    def __init__(self, email: ParsedEmail, boundary: str):
        self.email = email
        self.scope = EmailScope(email)
        self.boundary = boundary
        self.messages: list[ChatMessage] = []
        self.observations: list[Observation] = []
        self.trace: list[AgentStep] = []
        self.signatures: dict[str, int] = {}  # call signature -> observation id
        self.steps_used = 0
        self.model_calls = 0

    def add_observation(self, result: ToolResult) -> Observation:
        observation = Observation(observation_id=len(self.observations) + 1, result=result)
        self.observations.append(observation)
        return observation

    def add_step(self, **fields) -> None:
        self.trace.append(AgentStep(step=len(self.trace) + 1, **fields))


class AgentAnalyzer(Analyzer):
    name = "agent"

    def __init__(
        self,
        model_client: ModelClient,
        registry: ToolRegistry,
        validator: VerdictValidator,
        max_steps: int = 6,
        max_repairs: int = 1,
        max_body_chars: int = 6000,
        automatic_checks: Optional[list[AutomaticCheck]] = None,
        boundary_factory: Callable[[], str] = prompts.new_boundary,
        clock: Callable[[], float] = time.perf_counter,
    ):
        if max_steps < 1:
            raise ValueError("max_steps must be at least 1")
        self.model_client = model_client
        self.registry = registry
        self.validator = validator
        self.max_steps = max_steps
        self.max_repairs = max_repairs
        self.max_body_chars = max_body_chars
        self.automatic_checks = automatic_checks or []
        self.boundary_factory = boundary_factory
        self.clock = clock


    def analyze(self, email: ParsedEmail) -> AnalyzerOutput:
        run = _Run(email, self.boundary_factory())

        automatic_observations = self._run_automatic_checks(run)

        run.messages.append(
            ChatMessage(
                role="system",
                content=prompts.build_system_prompt(self.registry.specs_for_model(), self.max_steps),
            )
        )
        run.messages.append(
            prompts.user_message(
                prompts.build_email_message(
                    email=email,
                    scope=run.scope,
                    boundary=run.boundary,
                    max_body_chars=self.max_body_chars,
                    automatic_observations=automatic_observations,
                    max_steps=self.max_steps,
                )
            )
        )

        step_limit_reached = True
        while run.steps_used < self.max_steps:
            decision = self._next_decision(run)
            if decision.action == "finish":
                step_limit_reached = False
                break
            run.steps_used += 1
            self._handle_tool_request(run, decision)

        if step_limit_reached:
            run.add_step(
                kind="step_limit",
                summary=f"Used all {self.max_steps} tool calls; asking the model to decide.",
            )

        verdict = self._final_verdict(run, step_limit_reached)
        return AnalyzerOutput(
            analyzer_name=self.name,
            verdict=verdict,
            trace=run.trace,
            observations=run.observations,
            model_calls=run.model_calls,
        )


    def _run_automatic_checks(self, run: _Run) -> list[Observation]:
        observations = []
        for check in self.automatic_checks:
            started = self.clock()
            result = check(run.email)
            observation = run.add_observation(result)
            observations.append(observation)
            run.add_step(
                kind="automatic_check",
                reason="Always run before the investigation starts.",
                tool_name=result.tool_name,
                observation_id=observation.observation_id,
                status=result.status,
                summary=result.finding,
                duration_ms=self._ms_since(started),
            )
        return observations

    def _next_decision(self, run: _Run) -> AgentDecision:
        schema = prompts.decision_schema(self.registry.tool_names())
        raw = self._ask_model(run, schema)
        for attempt in range(self.max_repairs + 1):
            try:
                decision = AgentDecision.model_validate_json(prompts.extract_json(raw))
            except ValidationError as error:
                problems = _validation_problems(error)
                run.add_step(kind="invalid_output", summary="Invalid decision: " + "; ".join(problems))
                if attempt == self.max_repairs:
                    raise InvalidModelOutputError(
                        "The model did not return a valid decision.", raw_output=raw
                    ) from error
                run.messages.append(prompts.assistant_message(raw[:2000]))
                run.messages.append(
                    prompts.user_message(prompts.invalid_output_message(problems, "decision"))
                )
                raw = self._ask_model(run, schema)
                continue

            run.messages.append(prompts.assistant_message(decision.model_dump_json()))
            return decision

        raise InvalidModelOutputError("The model did not return a valid decision.", raw_output=raw)

    def _handle_tool_request(self, run: _Run, decision: AgentDecision) -> None:
        steps_left = self.max_steps - run.steps_used

        try:
            call = self.registry.prepare_call(decision.tool_name, decision.arguments, run.email, run.scope)
        except InvalidToolCallError as error:
            run.add_step(
                kind="rejected_call",
                reason=decision.reason,
                tool_name=decision.tool_name,
                arguments=decision.arguments,
                summary=error.reason,
            )
            run.messages.append(prompts.user_message(prompts.rejected_call_message(error.reason, steps_left)))
            return

        earlier_id = run.signatures.get(call.signature())
        if earlier_id is not None:
            run.add_step(
                kind="repeated_call",
                reason=decision.reason,
                tool_name=call.tool_name,
                arguments=call.arguments,
                observation_id=earlier_id,
                summary=f"Same check as observation #{earlier_id}; not run again.",
            )
            run.messages.append(prompts.user_message(prompts.repeated_call_message(earlier_id, steps_left)))
            return

        self._execute(run, call, decision.reason, steps_left)

    def _execute(self, run: _Run, call: PreparedCall, reason: str, steps_left: int) -> None:
        started = self.clock()
        result = self.registry.execute(call, run.email)
        observation = run.add_observation(result)
        run.signatures[call.signature()] = observation.observation_id
        run.add_step(
            kind="tool_call",
            reason=reason,
            tool_name=call.tool_name,
            arguments=call.arguments,
            observation_id=observation.observation_id,
            status=result.status,
            summary=result.finding,
            duration_ms=self._ms_since(started),
        )
        run.messages.append(
            prompts.user_message(prompts.observation_message(observation, run.boundary, steps_left))
        )

    def _final_verdict(self, run: _Run, step_limit_reached: bool):
        run.messages.append(
            prompts.user_message(prompts.final_answer_prompt(run.observations, step_limit_reached))
        )
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
                problems = _validation_problems(error)

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

    # helpers

    def _ask_model(self, run: _Run, schema: dict) -> str:
        run.model_calls += 1
        return self.model_client.chat(run.messages, response_schema=schema)

    def _ms_since(self, started: float) -> int:
        return int((self.clock() - started) * 1000)


def _validation_problems(error: ValidationError) -> list[str]:
    problems = []
    for item in error.errors()[:5]:
        location = ".".join(str(part) for part in item.get("loc", ())) or "reply"
        message = item.get("msg", "invalid value")
        if item.get("type") == "json_invalid":
            problems.append("The reply is not valid JSON.")
        else:
            problems.append(f"{location}: {message}")
    return problems or ["The reply could not be read."]
