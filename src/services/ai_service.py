from __future__ import annotations

import logging
import time
from typing import Callable, Optional

from src.capabilities.analyzer import Analyzer
from src.exceptions import DatabaseUnavailableError, InvalidEmailInputError, ModelError
from src.schemas.agent import AnalysisResult, AnalysisStatus
from src.schemas.responses import ParsedEmail, Verdict

logger = logging.getLogger(__name__)

EmailParser = Callable[[str], ParsedEmail]
CaseRecorder = Callable[[ParsedEmail, Verdict], object]


class AnalysisService:
    def __init__(
        self,
        parse_email: EmailParser,
        main_analyzer: Analyzer,
        fallback_analyzer: Analyzer,
        case_recorder: Optional[CaseRecorder] = None,
        max_input_chars: int = 200_000,
        startup_warnings: Optional[list[str]] = None,
        clock: Callable[[], float] = time.perf_counter,
    ):
        self.parse_email = parse_email
        self.main_analyzer = main_analyzer
        self.fallback_analyzer = fallback_analyzer
        self.case_recorder = case_recorder
        self.max_input_chars = max_input_chars
        self.startup_warnings = list(startup_warnings or [])
        self.clock = clock

    def analyze(self, raw_text: str) -> AnalysisResult:
        started = self.clock()
        try:
            return self._analyze(raw_text, started)
        except Exception:
            logger.exception("Unexpected error during analysis")
            return AnalysisResult.failed(
                "Something went wrong while analysing this email. Please try again; "
                "if it keeps happening, report it to the SpotThePhish team.",
                duration_ms=self._ms_since(started),
            )

    def _analyze(self, raw_text: str, started: float) -> AnalysisResult:
        text = raw_text if isinstance(raw_text, str) else ""
        if not text.strip():
            return AnalysisResult.rejected(
                "Paste an email or upload an .eml file to start.",
                duration_ms=self._ms_since(started),
            )
        if len(text) > self.max_input_chars:
            return AnalysisResult.rejected(
                f"This input is too large ({len(text):,} characters; the limit is "
                f"{self.max_input_chars:,}). Paste a single email.",
                duration_ms=self._ms_since(started),
            )

        try:
            email = self.parse_email(text)
        except InvalidEmailInputError as error:
            logger.info("Input rejected by the parser: %s", error.reason)
            return AnalysisResult.rejected(error.user_message, duration_ms=self._ms_since(started))

        warnings = list(self.startup_warnings)
        try:
            output = self.main_analyzer.analyze(email)
            status = AnalysisStatus.COMPLETED
        except ModelError as error:
            logger.warning("%s failed (%s: %s); using %s",
                           self.main_analyzer.name, type(error).__name__, error.reason,
                           self.fallback_analyzer.name)
            warnings.append(error.user_message)
            output = self.fallback_analyzer.analyze(email)
            status = AnalysisStatus.FALLBACK

        verdict = output.verdict
        if verdict.unavailable_tools:
            warnings.append(
                "Some checks could not run, so this result is less certain: " + ", ".join(verdict.unavailable_tools) + "."
            )
        if not email.has_headers:
            warnings.append(
                "The email has no headers, so the sender could not be verified. "
                "For a better result, upload the .eml file or paste the full source."
            )

        if self.case_recorder is not None:
            try:
                self.case_recorder(email, verdict)
            except DatabaseUnavailableError as error:
                logger.warning("Could not save the case: %s", error.reason)
                warnings.append("Case history is unavailable, so this email was not saved for campaign detection.")

        return AnalysisResult(
            status=status,
            verdict=verdict,
            trace=output.trace,
            observations=output.observations,
            warnings=warnings,
            analyzer_name=output.analyzer_name,
            model_calls=output.model_calls,
            duration_ms=self._ms_since(started),
        )

    def _ms_since(self, started: float) -> int:
        return int((self.clock() - started) * 1000)


def analyze_email(raw_text: str, service: Optional[AnalysisService] = None) -> AnalysisResult:
    if service is None:
        from src.services.service_factory import get_default_service

        service = get_default_service()
    return service.analyze(raw_text)


def generate_response(user_message: str, service: Optional[AnalysisService] = None) -> str:
    result = analyze_email(user_message, service)
    return format_result_as_markdown(result)


def format_result_as_markdown(result: AnalysisResult) -> str:
    if result.verdict is None:
        return result.message

    verdict = result.verdict
    lines = [
        f"## {verdict.verdict.upper()} (risk {verdict.risk_score:.0f}/100)",
    ]
    if verdict.is_fallback:
        lines.append("Offline fallback result: the AI model was not used.")
    lines.append("")
    lines.append(verdict.explanation)
    lines.append("")
    lines.append(f"What to do: {verdict.recommended_action}")
    lines.append("")
    lines.append("Evidence")
    for item in verdict.evidence:
        lines.append(f"- {item}")
    if result.warnings:
        lines.append("")
        lines.append("Warnings")
        for warning in result.warnings:
            lines.append(f"- {warning}")
    lines.append("")
    lines.append("Investigation trace")
    for step in result.trace:
        tool = f" `{step.tool_name}`" if step.tool_name else ""
        line = f"{step.step}. {step.kind} {tool}: {step.summary}"
        if step.reason:
            line += f" (why: {step.reason})"
        lines.append(line)
    return "\n".join(lines)
