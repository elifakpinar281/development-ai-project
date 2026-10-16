from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Optional
from urllib.parse import urlsplit

from src.exceptions import InvalidToolCallError
from src.schemas.responses import ParsedEmail, ToolResult

logger = logging.getLogger(__name__)

DOMAIN_ARGUMENTS = {"domain", "sender_domain"}
URL_ARGUMENTS = {"url"}
_EMPTY_SCHEMA = {"type": "object", "properties": {}, "required": []}


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict = field(default_factory=lambda: dict(_EMPTY_SCHEMA))


@dataclass
class PreparedCall:
    tool_name: str
    arguments: dict

    def signature(self) -> str:
        return self.tool_name + json.dumps(self.arguments, sort_keys=True, ensure_ascii=False)


class ToolSource(ABC):
    @abstractmethod
    def specs(self) -> list[ToolSpec]:
        """The tools this source offers."""

    @abstractmethod
    def call(self, tool_name: str, arguments: dict, email: ParsedEmail) -> ToolResult:
        """Run one tool. Should return a ToolResult with status "unavailable" or "error" instead of raising."""

EmailCheck = Callable[[ParsedEmail], ToolResult]


@dataclass
class LocalTool:
    name: str
    description: str
    function: EmailCheck


class LocalToolSource(ToolSource):

    def __init__(self, tools: list[LocalTool]):
        self._tools: dict[str, LocalTool] = {}
        for tool in tools:
            self._tools[tool.name] = tool

    def specs(self) -> list[ToolSpec]:
        specs = []
        for tool in self._tools.values():
            specs.append(ToolSpec(name=tool.name, description=tool.description))
        return specs

    def call(self, tool_name: str, arguments: dict, email: ParsedEmail) -> ToolResult:
        return self._tools[tool_name].function(email)


class McpToolSource(ToolSource):

    def __init__(self, client: Any):
        self._client = client

    def specs(self) -> list[ToolSpec]:
        specs = []
        for definition in self._client.tool_definitions():
            function = definition.get("function", definition)
            specs.append(
                ToolSpec(
                    name=function["name"],
                    description=function.get("description") or "",
                    parameters=function.get("parameters") or dict(_EMPTY_SCHEMA),
                )
            )
        return specs

    def call(self, tool_name: str, arguments: dict, email: ParsedEmail) -> ToolResult:
        return self._client.call(tool_name, arguments)


class EmailScope:

    def __init__(self, email: ParsedEmail):
        self.hosts: set[str] = set()
        self.urls: set[str] = set()

        addresses = [email.sender, email.reply_to, email.return_path]
        for address in email.recipients:
            addresses.append(address)
        for address in addresses:
            if address is None:
                continue
            self._add_host(address.domain)
            if "@" in address.address:
                self._add_host(address.address.rsplit("@", 1)[1])

        for link in email.links:
            self._add_host(link.domain)
            url = link.url.strip()
            if url:
                self.urls.add(_normalize_url(url))
                try:
                    self._add_host(urlsplit(url).hostname or "")
                except ValueError:
                    pass

    def _add_host(self, value: str) -> None:
        host = normalize_host(value)
        if host:
            self.hosts.add(host)

    def contains_domain(self, value: str) -> bool:
        candidate = normalize_host(value)
        if not candidate or "." not in candidate: return False
        for host in self.hosts:
            if host == candidate or host.endswith("." + candidate): return True
        return False

    def contains_url(self, value: str) -> bool:
        return _normalize_url(value) in self.urls

    def domains_for_prompt(self) -> list[str]:
        return sorted(self.hosts)

    def urls_for_prompt(self) -> list[str]:
        return sorted(self.urls)


def normalize_host(value: str) -> str:
    """Lowercase host name without "www.", port or trailing dot."""
    if not isinstance(value, str):
        return ""
    host = value.strip().lower()
    if "://" in host:
        try:
            host = urlsplit(host).hostname or ""
        except ValueError:
            return ""
    if "@" in host:
        host = host.rsplit("@", 1)[1]
    host = host.split("/", 1)[0]
    if host.count(":") == 1:
        host = host.split(":", 1)[0]
    host = host.rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    return host


def _normalize_url(value: str) -> str:
    return value.strip().rstrip("/")


ArgumentFiller = Callable[[ParsedEmail], dict]


class ToolRegistry:

    def __init__(
        self,
        sources: list[ToolSource],
        argument_fillers: Optional[dict[str, ArgumentFiller]] = None,
    ):

        self._argument_fillers = argument_fillers or {}
        self._specs: dict[str, ToolSpec] = {}
        self._sources: dict[str, ToolSource] = {}

        for source in sources:
            for spec in source.specs():
                if spec.name in self._specs:
                    raise ValueError(f"Two tool sources define the tool '{spec.name}'.")
                self._specs[spec.name] = spec
                self._sources[spec.name] = source

    def tool_names(self) -> list[str]:
        return sorted(self._specs)

    def specs_for_model(self) -> list[ToolSpec]:
        specs = []
        for name in self.tool_names():
            spec = self._specs[name]
            if name in self._argument_fillers:
                spec = ToolSpec(
                    name=spec.name,
                    description=spec.description + " (Arguments are filled in automatically - pass {}.)",
                )
            specs.append(spec)
        return specs


    def prepare_call(self, tool_name: Any, arguments: Any, email: ParsedEmail, scope: EmailScope) -> PreparedCall:
        if not isinstance(tool_name, str) or tool_name not in self._specs:
            raise InvalidToolCallError(
                f"Unknown tool '{tool_name}'. Available tools: {', '.join(self.tool_names())}."
            )

        if tool_name in self._argument_fillers:
            filler = self._argument_fillers[tool_name]
            return PreparedCall(tool_name=tool_name, arguments=filler(email))

        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            raise InvalidToolCallError(f"Arguments for '{tool_name}' must be a JSON object.")

        schema = self._specs[tool_name].parameters
        _check_arguments_against_schema(tool_name, arguments, schema)
        _check_arguments_are_in_email(tool_name, arguments, scope)

        cleaned = {}
        for key, value in arguments.items():
            if isinstance(value, str):
                value = value.strip()
            cleaned[key] = value
        return PreparedCall(tool_name=tool_name, arguments=cleaned)


    def execute(self, call: PreparedCall, email: ParsedEmail) -> ToolResult:
        source = self._sources[call.tool_name]
        try:
            result = source.call(call.tool_name, call.arguments, email)
        except Exception as error:
            logger.exception("Tool %s raised instead of returning a result", call.tool_name)
            return ToolResult(
                tool_name=call.tool_name,
                status="unavailable",
                finding=f"Check could not be run: internal error ({type(error).__name__}).",
                is_offline=False,
            )

        if not isinstance(result, ToolResult):
            logger.error("Tool %s returned %s instead of a ToolResult", call.tool_name, type(result).__name__)
            return ToolResult(
                tool_name=call.tool_name,
                status="unavailable",
                finding="Check could not be run: the tool returned an invalid result.",
                is_offline=False,
            )
        return result


_JSON_TYPES: dict[str, tuple] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "array": (list,),
    "object": (dict,),
    "null": (type(None),),
}

def _check_arguments_against_schema(tool_name: str, arguments: dict, schema: dict) -> None:
    properties = schema.get("properties") or {}

    for key in arguments:
        if key not in properties:
            allowed = ", ".join(sorted(properties)) or "none"
            raise InvalidToolCallError(
                f"'{tool_name}' has no argument '{key}'. Allowed arguments: {allowed}."
            )

    for key in schema.get("required") or []:
        if key not in arguments:
            raise InvalidToolCallError(f"'{tool_name}' needs the argument '{key}'.")

    for key, value in arguments.items():
        if not _matches_type(value, properties[key]):
            raise InvalidToolCallError(
                f"Argument '{key}' of '{tool_name}' has the wrong type ({type(value).__name__})."
            )


def _matches_type(value: Any, property_schema: dict) -> bool:
    if "anyOf" in property_schema:
        for option in property_schema["anyOf"]:
            if _matches_type(value, option):
                return True
        return False

    expected = property_schema.get("type")
    if expected is None:
        return True
    if isinstance(expected, str):
        expected = [expected]

    for type_name in expected:
        python_types = _JSON_TYPES.get(type_name)
        if python_types is None:
            continue
        if isinstance(value, bool) and type_name in ("integer", "number"):
            continue
        if isinstance(value, python_types):
            if type_name == "array" and "items" in property_schema:
                for item in value:
                    if not _matches_type(item, property_schema["items"]):
                        return False
            return True
    return False


def _check_arguments_are_in_email(tool_name: str, arguments: dict, scope: EmailScope) -> None:
    for key, value in arguments.items():
        if key in DOMAIN_ARGUMENTS and isinstance(value, str):
            if not scope.contains_domain(value):
                raise InvalidToolCallError(
                    f"'{value}' does not appear in this email. {tool_name} may only check "
                    f"domains from the email: {', '.join(scope.domains_for_prompt()) or 'none'}."
                )
        if key in URL_ARGUMENTS and isinstance(value, str):
            if not scope.contains_url(value):
                raise InvalidToolCallError(
                    f"'{value}' is not a link in this email. {tool_name} may only check "
                    f"the email's own links, copied exactly."
                )
