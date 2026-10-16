from __future__ import annotations

import importlib
import logging
import threading
from typing import Callable, Optional

from src.capabilities.agent import AgentAnalyzer
from src.capabilities.injection_scan import scan_for_prompt_injection
from src.capabilities.offline_analyzer import OfflineAnalyzer
from src.capabilities.tool_registry import (
    LocalTool,
    LocalToolSource,
    McpToolSource,
    ToolRegistry,
    ToolSource,
    normalize_host,
)
from src.capabilities.verdict_validator import VerdictValidator
from src.config import Config, config
from src.exceptions import McpServerUnavailableError
from src.models.model_client import OllamaModelClient
from src.schemas.responses import ParsedEmail, ToolResult
from src.services.ai_service import AnalysisService

logger = logging.getLogger(__name__)

EMAIL_TOOL_DESCRIPTIONS = [
    (
        "check_email_authentication",
        "Checks SPF, DKIM and DMARC results and whether From, Reply-To and Return-Path disagree. "
        "Useful whenever the email has headers.",
    ),
    (
        "analyze_links",
        "Compares each link's visible text with its real target and flags IP-address links, "
        "URL shorteners and punycode. Useful when the email has links.",
    ),
    (
        "inspect_attachments",
        "Flags dangerous attachment types (executables, scripts, macros, double extensions) "
        "using only metadata. Useful when the email has attachments.",
    ),
]

MAX_OFFLINE_LOOKALIKE_DOMAINS = 10


def build_analysis_service(
    settings: Config = config,
    parse_email: Optional[Callable[[str], ParsedEmail]] = None,
) -> AnalysisService:
    startup_warnings: list[str] = []

    model_client = OllamaModelClient(
        base_url=settings.ollama_base_url,
        model_name=settings.model_name,
        timeout_seconds=settings.model_timeout,
        temperature=settings.model_temperature,
        context_tokens=settings.model_context_tokens,
        think=settings.model_think,
    )

    sources: list[ToolSource] = []
    email_functions = _email_tool_functions()
    email_tools = []
    for name, description, function in email_functions:
        email_tools.append(LocalTool(name=name, description=description, function=function))
    if email_tools:
        sources.append(LocalToolSource(email_tools))
    else:
        startup_warnings.append("Email header, link and attachment checks are not installed yet.")

    domain_source = _start_domain_tools()
    if domain_source is not None:
        sources.append(domain_source)
    else:
        startup_warnings.append(
            "Domain checks (lookalike, domain age, reputation, case history) are unavailable "
            "because the tool server could not start."
        )

    registry = ToolRegistry(
        sources,
        argument_fillers={"lookup_case_history": case_history_arguments},
    )

    agent = AgentAnalyzer(
        model_client=model_client,
        registry=registry,
        validator=VerdictValidator(),
        max_steps=settings.agent_max_tool_calls,
        max_body_chars=settings.prompt_max_body_chars,
        automatic_checks=[scan_for_prompt_injection],
    )

    offline_checks = [injection_check, lookalike_checks]
    for _name, _description, function in email_functions:
        offline_checks.append(_as_offline_check(function))
    fallback = OfflineAnalyzer(checks=offline_checks)

    return AnalysisService(
        parse_email=parse_email or _load_parser(),
        main_analyzer=agent,
        fallback_analyzer=fallback,
        case_recorder=_case_recorder(),
        max_input_chars=settings.max_email_chars,
        startup_warnings=startup_warnings,
    )

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
        "subject": email.subject or "",
    }


def injection_check(email: ParsedEmail) -> list[ToolResult]:
    return [scan_for_prompt_injection(email)]


def lookalike_checks(email: ParsedEmail) -> list[ToolResult]:
    from src.capabilities import domain_tools

    domains: list[str] = []
    candidates = [email.sender, email.reply_to, email.return_path]
    for address in candidates:
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


def _as_offline_check(function):
    def check(email: ParsedEmail) -> list[ToolResult]:
        return [function(email)]

    check.__name__ = getattr(function, "__name__", "email_check")
    return check

def _email_tool_functions() -> list[tuple]:
    try:
        module = importlib.import_module("src.capabilities.email_tools")
    except ImportError:
        logger.warning("src/capabilities/email_tools.py not found, email checks are disabled.")
        return []
    functions = []
    for name, description in EMAIL_TOOL_DESCRIPTIONS:
        function = getattr(module, name, None)
        if function is None:
            logger.warning("email_tools.%s not found, this check is disabled.", name)
            continue
        functions.append((name, description, function))
    return functions


def _start_domain_tools() -> Optional[McpToolSource]:
    try:
        from src.capabilities.mcp_client import DomainToolsClient

        return McpToolSource(DomainToolsClient())
    except McpServerUnavailableError as error:
        logger.warning("Domain tool server unavailable: %s", error.reason)
        return None
    except ImportError as error:
        logger.warning("MCP client could not be imported: %s", error)
        return None


def _load_parser():
    module = importlib.import_module("src.email_parser")
    parse_email = getattr(module, "parse_email", None)
    if parse_email is None:
        raise RuntimeError(
            "src/email_parser.py has no parse_email(text) function yet. "
            "The service cannot start without the email parser."
        )
    return parse_email


def _case_recorder():
    try:
        from src.capabilities import memory
    except ImportError as error:
        logger.warning("Case history disabled: %s", error)
        return None
    return memory.save_verdict
