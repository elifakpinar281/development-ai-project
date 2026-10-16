from __future__ import annotations

import json
from typing import Optional, Union

from src.capabilities.tool_registry import ToolSource, ToolSpec
from src.models.model_client import ModelClient
from src.schemas.agent import ChatMessage
from src.schemas.responses import EmailAddress, LinkInfo, ParsedEmail, ToolResult


class ScriptedModelClient(ModelClient):
    model_name = "scripted-test-model"

    def __init__(self, replies: list[Union[str, dict, Exception]]):
        self.replies = list(replies)
        self.calls: list[list[ChatMessage]] = []
        self.schemas: list[Optional[dict]] = []

    def chat(self, messages: list[ChatMessage], response_schema: Optional[dict] = None) -> str:
        self.calls.append(list(messages))
        self.schemas.append(response_schema)
        if not self.replies:
            raise AssertionError("The test model ran out of scripted replies.")
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, dict):
            return json.dumps(reply)
        return reply


class FakeToolSource(ToolSource):

    def __init__(self, tools: dict[str, dict], results: dict[str, ToolResult]):
        self.tools = tools
        self.results = results
        self.calls: list[tuple[str, dict]] = []

    def specs(self) -> list[ToolSpec]:
        specs = []
        for name, parameters in self.tools.items():
            specs.append(ToolSpec(name=name, description=f"Test tool {name}.", parameters=parameters))
        return specs

    def call(self, tool_name: str, arguments: dict, email: ParsedEmail) -> ToolResult:
        self.calls.append((tool_name, arguments))
        result = self.results[tool_name]
        if isinstance(result, Exception):
            raise result
        return result


DOMAIN_PARAMETERS = {
    "type": "object",
    "properties": {"domain": {"type": "string", "title": "Domain"}},
    "required": ["domain"],
}
URL_PARAMETERS = {
    "type": "object",
    "properties": {"url": {"type": "string"}},
    "required": ["url"],
}
NO_PARAMETERS = {"type": "object", "properties": {}, "required": []}


def make_email(
    subject: str = "Urgent: verify your account",
    body: str = "Your mailbox will be closed today. Verify your password now at the link below.",
    sender: str = "it-support@hamk-helpdesk.com",
    links: tuple = ("https://login.hamk-helpdesk.com/verify",),
    has_headers: bool = True,
) -> ParsedEmail:
    sender_domain = sender.rsplit("@", 1)[1]
    link_infos = []
    for url in links:
        host = url.split("//", 1)[1].split("/", 1)[0]
        link_infos.append(LinkInfo(url=url, display_text="Verify now", domain=host))
    return ParsedEmail(
        raw_text=f"From: {sender}\nSubject: {subject}\n\n{body}",
        subject=subject,
        sender=EmailAddress(display_name="IT Support", address=sender, domain=sender_domain),
        body_plain=body,
        links=link_infos,
        has_headers=has_headers,
    )


def result(tool_name: str, status: str = "success", risk: float = 0, finding: str = "ok", **details) -> ToolResult:
    return ToolResult(tool_name=tool_name, status=status, risk_score=risk, finding=finding, details=details)


def decide(tool_name: Optional[str], arguments: Optional[dict] = None, reason: str = "Check it.") -> dict:
    if tool_name is None:
        return {"reason": "Evidence is clear.", "action": "finish", "tool_name": None, "arguments": {}}
    return {"reason": reason, "action": "call_tool", "tool_name": tool_name, "arguments": arguments or {}}


def final(verdict: str = "phishing", score: int = 85, evidence: Optional[list] = None, **extra) -> dict:
    answer = {
        "verdict": verdict,
        "risk_score": score,
        "explanation": "The link imitates the university login page.",
        "recommended_action": "Do not click the link and report the email.",
        "evidence": evidence if evidence is not None else [
            {"source": "tool", "observation_id": 2, "quote": None, "claim": "The domain imitates hamk.fi."}
        ],
    }
    answer.update(extra)
    return answer
