from __future__ import annotations

import atexit
import logging
import threading
from dataclasses import dataclass, field
from typing import Optional

from src.capabilities import domain_tools, memory
from src.capabilities import tools as email_tools
from src.capabilities.agent import AgentAnalyzer
from src.capabilities.analyzer import Analyzer
from src.capabilities.email_fields import real_subject
from src.capabilities.fraud_scan import scan_for_payment_fraud
from src.capabilities.injection_scan import scan_for_prompt_injection
from src.capabilities.mcp_client import DomainToolsClient
from src.capabilities.offline_analyzer import OfflineAnalyzer, OfflineCheck
from src.capabilities.tool_registry import (
    EmailCheck,
    LocalTool,
    LocalToolSource,
    McpToolSource,
    ToolRegistry,
    ToolSource,
    normalize_host,
)
from src.capabilities.verdict_validator import VerdictValidator
from src.config import Config, config
from src.email_parser import parse_email as default_parse_email
from src.exceptions import McpServerUnavailableError
from src.models.model_client import ModelClient, OllamaModelClient
from src.schemas.responses import ParsedEmail, ToolResult
from src.services.ai_service import AnalysisService, EmailParser

logger = logging.getLogger(__name__)

MAX_OFFLINE_LOOKALIKE_DOMAINS = 10

EMAIL_TOOLS: list[LocalTool] = [
    LocalTool(
        name="check_email_authentication",
        description=(
            "Checks SPF, DKIM and DMARC results and whether From, Reply-To and Return-Path "
            "disagree. Useful whenever the email has headers."
        ),
        function=email_tools.check_email_authentication,
    ),
    LocalTool(
        name="analyze_links",
        description=(
            "Compares each link's visible text with its real target and flags IP-address "
            "links, URL shorteners and punycode. Useful when the email has links."
        ),
        function=email_tools.analyze_links,
    ),
    LocalTool(
        name="inspect_attachments",
        description=(
            "Flags dangerous attachment types (executables, scripts, macros, double "
            "extensions) using only metadata. Useful when the email has attachments."
        ),
        function=email_tools.inspect_attachments,
    ),
]


ANALYZER_NAMES = ("agent",)

@dataclass
class Components:

    settings: Config
    model_client: ModelClient
    registry: ToolRegistry
    validator: VerdictValidator
    fallback: OfflineAnalyzer
    startup_warnings: list[str] = field(default_factory=list)


def build_components(
    settings: Config = config,
    model_client: Optional[ModelClient] = None,
    start_domain_server: bool = True,
) -> Components:
    startup_warnings: list[str] = []

    if model_client is None:
        model_client = OllamaModelClient(
            base_url=settings.ollama_base_url,
            model_name=settings.model_name,
            timeout_seconds=settings.model_timeout,
            temperature=settings.model_temperature,
            context_tokens=settings.model_context_tokens,
            think=settings.model_think,
        )

    sources: list[ToolSource] = [LocalToolSource(EMAIL_TOOLS)]
    if start_domain_server:
        domain_source = _start_domain_server()
        if domain_source is not None:
            sources.append(domain_source)
        else:
            startup_warnings.append(
                "Domain checks (domain age, reputation, case history) are unavailable "
                "because the tool server could not start. The lookalike check still ran."
            )

    registry = ToolRegistry(sources, argument_fillers={"lookup_case_history": case_history_arguments})

    offline_checks: list[OfflineCheck] = [injection_check, fraud_check, lookalike_checks]
    for tool in EMAIL_TOOLS:
        offline_checks.append(as_offline_check(tool.function))

    return Components(
        settings=settings,
        model_client=model_client,
        registry=registry,
        validator=VerdictValidator(),
        fallback=OfflineAnalyzer(checks=offline_checks),
        startup_warnings=startup_warnings,
    )


def build_analyzer(name: str, components: Components) -> Analyzer:
    settings = components.settings
    if name == "agent":
        return AgentAnalyzer(
            model_client=components.model_client,
            registry=components.registry,
            validator=components.validator,
            max_steps=settings.agent_max_tool_calls,
            max_body_chars=settings.prompt_max_body_chars,
            automatic_checks=[scan_for_prompt_injection, scan_for_payment_fraud, scan_lookalike_domains],
        )
    raise ValueError(f"Unknown analyzer '{name}'. Choose one of: {', '.join(ANALYZER_NAMES)}.")


def build_service_from(
    components: Components,
    analyzer: str = "agent",
    parse_email: EmailParser = default_parse_email,
    save_cases: bool = True,
) -> AnalysisService:
    return AnalysisService(
        parse_email=parse_email,
        main_analyzer=build_analyzer(analyzer, components),
        fallback_analyzer=components.fallback,
        case_recorder=memory.save_verdict if save_cases else None,
        max_input_chars=components.settings.max_email_chars,
        startup_warnings=components.startup_warnings,
    )


def build_analysis_service(
    settings: Config = config,
    parse_email: EmailParser = default_parse_email,
    model_client: Optional[ModelClient] = None,
    start_domain_server: bool = True,
    analyzer: str = "agent",
) -> AnalysisService:
    components = build_components(settings, model_client, start_domain_server)
    return build_service_from(components, analyzer, parse_email)


_default_service: Optional[AnalysisService] = None
_default_lock = threading.Lock()


def get_default_service() -> AnalysisService:
    global _default_service
    with _default_lock:
        if _default_service is None:
            _default_service = build_analysis_service()
        return _default_service


def case_history_arguments(email: ParsedEmail) -> dict:
    link_domains: list[str] = []
    for link in email.links:
        host = normalize_host(link.domain or link.url)
        if host and host not in link_domains:
            link_domains.append(host)
    return {
        "sender_domain": normalize_host(email.sender.domain or email.sender.address),
        "link_domains": sorted(link_domains),
        "subject": real_subject(email),
    }


def injection_check(email: ParsedEmail) -> list[ToolResult]:
    return [scan_for_prompt_injection(email)]


def fraud_check(email: ParsedEmail) -> list[ToolResult]:
    return [scan_for_payment_fraud(email)]


def lookalike_checks(email: ParsedEmail) -> list[ToolResult]:
    domains: list[str] = []
    for address in (email.sender, email.reply_to, email.return_path):
        if address is not None:
            domains.append(normalize_host(address.domain or address.address))
    for link in email.links:
        domains.append(normalize_host(link.domain or link.url))

    results: list[ToolResult] = []
    checked: set[str] = set()
    for domain in domains:
        if not domain or domain in checked or len(checked) >= MAX_OFFLINE_LOOKALIKE_DOMAINS:
            continue
        checked.add(domain)
        result = domain_tools.check_lookalike_domain(domain)
        if result.status in ("success", "warning"):
            results.append(result)
    return results


def as_offline_check(function: EmailCheck) -> OfflineCheck:

    def check(email: ParsedEmail) -> list[ToolResult]:
        return [function(email)]

    check.__name__ = getattr(function, "__name__", "email_check")
    return check


def _start_domain_server() -> Optional[McpToolSource]:
    try:
        client = DomainToolsClient()
    except McpServerUnavailableError as error:
        logger.warning("Domain tool server unavailable: %s", error.reason)
        return None

    atexit.register(client.close)
    return McpToolSource(client)

def scan_lookalike_domains(email: ParsedEmail) -> ToolResult:
    results = lookalike_checks(email)
    if not results:
        return ToolResult(
            tool_name="check_lookalike_domain",
            status="success",
            finding="No sender or link domain could be checked for similarity to known domains.",
            risk_score=0,
            evidence=[],
            details={"checked_domains": 0},
            is_offline=True,
        )
    highest = results[0]
    for result in results:
        if result.risk_score > highest.risk_score:
            highest = result
    return highest