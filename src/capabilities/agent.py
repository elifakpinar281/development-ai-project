from __future__ import annotations

import time
from typing import Callable, Optional

from pydantic import ValidationError

from src.capabilities import prompts
from src.capabilities.model_analyzer import (
    AnalysisRun,
    AutomaticCheck,
    ModelAnalyzer,
    validation_problems,
)
from src.capabilities.tool_registry import PreparedCall, ToolRegistry
from src.capabilities.verdict_validator import VerdictValidator
from src.exceptions import InvalidModelOutputError, InvalidToolCallError
from src.models.model_client import ModelClient
from src.schemas.agent import AgentDecision, AnalyzerOutput, ChatMessage
from src.schemas.responses import ParsedEmail


class AgentAnalyzer(ModelAnalyzer):
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
        super().__init__(
            model_client=model_client,
            validator=validator,
            max_repairs=max_repairs,
            max_body_chars=max_body_chars,
            automatic_checks=automatic_checks,
            boundary_factory=boundary_factory,
            clock=clock,
        )
        self.registry = registry
        self.max_steps = max_steps


    def analyze(self, email: ParsedEmail) -> AnalyzerOutput:
        run = self._start_run(email)

        automatic_observations = self._run_automatic_checks(run, "Always run before the investigation starts.")

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
                summary=f"Used all {self.max_steps} tool calls. Asking the model to decide.",
            )

        run.messages.append(
            prompts.user_message(prompts.final_answer_prompt(run.observations, step_limit_reached))
        )
        verdict = self._request_verdict(run)
        return self._output(run, verdict)


    def _next_decision(self, run: AnalysisRun) -> AgentDecision:
        schema = prompts.decision_schema(self.registry.tool_names())
        raw = self._ask_model(run, schema)
        for attempt in range(self.max_repairs + 1):
            try:
                decision = AgentDecision.model_validate_json(prompts.extract_json(raw))
            except ValidationError as error:
                problems = validation_problems(error)
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

    def _handle_tool_request(self, run: AnalysisRun, decision: AgentDecision) -> None:
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
                summary=f"Same check as observation #{earlier_id} - not run again.",
            )
            run.messages.append(prompts.user_message(prompts.repeated_call_message(earlier_id, steps_left)))
            return

        self._execute(run, call, decision.reason, steps_left)

    def _execute(self, run: AnalysisRun, call: PreparedCall, reason: str, steps_left: int) -> None:
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