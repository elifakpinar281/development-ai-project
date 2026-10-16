
from __future__ import annotations

import html
import re

from src.schemas.responses import ParsedEmail, ToolResult

TOOL_NAME = "scan_prompt_injection"

_PATTERNS: list[tuple[str, re.Pattern]] = [
    (
        "asks to ignore earlier instructions",
        re.compile(
            r"\b(ignore|disregard|forget|override)\b[^.\n]{0,40}\b(previous|prior|earlier|above|your|system)\b"
            r"[^.\n]{0,20}\b(instructions?|prompts?|rules|guidelines)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "addresses an AI system directly",
        re.compile(
            r"\b(as an ai|you are an? (ai|assistant|language model|llm)|dear (ai|assistant|llm|language model)"
            r"|(ai|llm|language model|chatgpt)\s*[:,]\s*(you|ignore|classify|mark|treat))\b",
            re.IGNORECASE,
        ),
    ),
    (
        "tells a scanner how to classify the email",
        re.compile(
            r"\b(classify|mark|label|flag|treat|rate|report)\b[^.\n]{0,30}\b(this|the)\s+(email|message|mail)\b"
            r"[^.\n]{0,20}\bas\s+(safe|legitimate|harmless|trusted|not\s+phishing|clean)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "mentions a system or developer prompt",
        re.compile(r"\b(system\s+prompt|developer\s+(prompt|message|instructions?))\b", re.IGNORECASE),
    ),
    (
        "contains a fake instruction block",
        re.compile(
            r"(\[/?(inst|system)\]|<\|?(im_start|im_end|system)\|?>|###\s*(new\s+)?instructions?\b|\bnew instructions\s*:)",
            re.IGNORECASE,
        ),
    ),
]

_SNIPPET_CHARS = 80
_RISK_SCORE = 60


def scan_for_prompt_injection(email: ParsedEmail) -> ToolResult:
    texts = [email.subject, email.body_plain, _visible_and_hidden_html_text(email.body_html)]
    if not email.body_plain and not email.body_html:
        texts.append(email.raw_text)

    evidence: list[str] = []
    matched_rules: list[str] = []
    for label, pattern in _PATTERNS:
        for text in texts:
            if not text:
                continue
            match = pattern.search(text)
            if match is None:
                continue
            matched_rules.append(label)
            evidence.append(f'{label}: "{_snippet(text, match.start(), match.end())}"')
            break

    if not matched_rules:
        return ToolResult(
            tool_name=TOOL_NAME,
            status="success",
            finding="No instructions aimed at AI tools were found in the email.",
            risk_score=0,
            details={"injection_detected": False},
            is_offline=True,
        )

    return ToolResult(
        tool_name=TOOL_NAME,
        status="warning",
        finding=(
            "The email contains text aimed at AI tools or email scanners. "
            "Legitimate senders have no reason to do this. It is a common trick to evade detection."
        ),
        risk_score=_RISK_SCORE,
        evidence=evidence,
        details={"injection_detected": True, "matched_rules": matched_rules},
        is_offline=True,
    )


def _visible_and_hidden_html_text(body_html: str) -> str:
    if not body_html:
        return ""
    text = re.sub(r"<!--(.*?)-->", r" \1 ", body_html, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text)


def _snippet(text: str, start: int, end: int) -> str:
    left = max(0, start - 15)
    right = min(len(text), max(end, left + _SNIPPET_CHARS))
    snippet = re.sub(r"\s+", " ", text[left:right]).strip()
    if len(snippet) > _SNIPPET_CHARS:
        snippet = snippet[:_SNIPPET_CHARS].rstrip() + "..."
    return snippet
