"""Baseline analyzers for the evaluation (Person 4).

They answer the question "what do the tools and the agent loop add?". Both
baselines use the same prompts, the same verdict validation and the same safety
rules as the agent. Only the way the evidence is collected differs.

- llm_only:        the model decides from the email alone, with no checks at all.
- fixed_pipeline:  every applicable check runs on every email (no choice), then
                   the model decides once.
"""
from __future__ import annotations

from typing import Callable, Optional

from src.capabilities import prompts
from src.capabilities.fraud_scan import scan_for_payment_fraud
from src.capabilities.injection_scan import scan_for_prompt_injection
from src.capabilities.analyzer import Analyzer
from src.capabilities.model_analyzer import AnalysisRun, AutomaticCheck, ModelAnalyzer
from src.capabilities.tool_registry import ToolRegistry, normalize_host
from src.capabilities.verdict_validator import VerdictValidator
from src.exceptions import InvalidToolCallError
from src.models.model_client import ModelClient
from src.schemas.agent import AnalyzerOutput, ChatMessage
from src.schemas.responses import ParsedEmail

MAX_DOMAINS = 3
MAX_URLS = 3

BASELINE_NAMES = ("llm_only", "fixed_pipeline")


class SinglePassAnalyzer(ModelAnalyzer):
    """Collects evidence (or none), then asks the model for the verdict once."""

    name = "single_pass"
    opening = "Assess the email now."

    def collect_evidence(self, run: AnalysisRun) -> None:
        """Override to add observations to the run. The default adds none."""

    def analyze(self, email: ParsedEmail) -> AnalyzerOutput:
        run = self._start_run(email)
        self.collect_evidence(run)
        run.messages.append(ChatMessage(role="system", content=prompts.SINGLE_PASS_SYSTEM_PROMPT))
        run.messages.append(
            prompts.user_message(
                prompts.build_single_pass_message(
                    email=email,
                    scope=run.scope,
                    boundary=run.boundary,
                    max_body_chars=self.max_body_chars,
                    observations=run.observations,
                    opening=self.opening,
                )
            )
        )
        verdict = self._request_verdict(run)
        return self._output(run, verdict)


class LlmOnlyAnalyzer(SinglePassAnalyzer):
    """No tools, no automatic scans: the model judges the email text and headers it is shown."""

    name = "llm_only"
    opening = "No checks were run. Assess the email from the email data alone."


class FixedPipelineAnalyzer(SinglePassAnalyzer):
    """Runs every applicable tool on every email, then the model decides once."""

    name = "fixed_pipeline"
    opening = "All applicable checks have been run. Assess the email."

    def __init__(
        self,
        model_client: ModelClient,
        registry: ToolRegistry,
        validator: VerdictValidator,
        **kwargs,
    ):
        super().__init__(model_client=model_client, validator=validator, **kwargs)
        self.registry = registry

    def collect_evidence(self, run: AnalysisRun) -> None:
        self._run_automatic_checks(run, "Fixed pipeline: always runs.")
        for tool_name, arguments in self.planned_calls(run.email):
            try:
                call = self.registry.prepare_call(tool_name, arguments, run.email, run.scope)
            except InvalidToolCallError as error:
                run.add_step(kind="rejected_call", tool_name=tool_name, arguments=arguments, summary=error.reason)
                continue
            if call.signature() in run.signatures:
                continue
            started = self.clock()
            result = self.registry.execute(call, run.email)
            observation = run.add_observation(result)
            run.signatures[call.signature()] = observation.observation_id
            run.add_step(
                kind="tool_call",
                reason="Fixed pipeline: always runs.",
                tool_name=call.tool_name,
                arguments=call.arguments,
                observation_id=observation.observation_id,
                status=result.status,
                summary=result.finding,
                duration_ms=self._ms_since(started),
            )

    def planned_calls(self, email: ParsedEmail) -> list[tuple[str, dict]]:
        """Every tool, once per domain / link where the tool needs one."""
        domains: list[str] = []
        for address in (email.sender, email.reply_to, email.return_path):
            if address is not None:
                _add_unique(domains, normalize_host(address.domain or address.address))
        urls: list[str] = []
        for link in email.links:
            _add_unique(domains, normalize_host(link.domain or link.url))
            _add_unique(urls, link.url.strip())

        calls: list[tuple[str, dict]] = []
        for spec in self.registry.specs_for_model():
            properties = (spec.parameters or {}).get("properties") or {}
            if not properties:
                calls.append((spec.name, {}))
            elif "domain" in properties:
                for domain in domains[:MAX_DOMAINS]:
                    calls.append((spec.name, {"domain": domain}))
            elif "url" in properties:
                for url in urls[:MAX_URLS]:
                    calls.append((spec.name, {"url": url}))
        return calls


def _add_unique(items: list[str], value: str) -> None:
    if value and value not in items:
        items.append(value)


def build_baseline(name: str, components) -> Analyzer:
    """components: src.services.service_factory.Components"""
    settings = components.settings
    common = dict(
        model_client=components.model_client,
        validator=components.validator,
        max_body_chars=settings.prompt_max_body_chars,
    )
    if name == "llm_only":
        return LlmOnlyAnalyzer(**common)
    if name == "fixed_pipeline":
        checks: list[AutomaticCheck] = [scan_for_prompt_injection, scan_for_payment_fraud]
        return FixedPipelineAnalyzer(registry=components.registry, automatic_checks=checks, **common)
    raise ValueError(f"Unknown baseline '{name}'. Choose one of: {', '.join(BASELINE_NAMES)}.")
