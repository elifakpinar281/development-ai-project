from __future__ import annotations

import re

from src.capabilities.email_fields import real_subject
from src.schemas.responses import ParsedEmail, ToolResult

TOOL_NAME = "scan_payment_fraud"

_REQUESTS: list[tuple[str, re.Pattern]] = [
    ("asks for gift cards", re.compile(r"\b(buy|purchase|get|pick up)\b[^.\n]{0,40}\b(gift\s?cards?|vouchers?|itunes|google play|steam) ?(cards?)?\b", re.IGNORECASE)),
    ("asks for a money transfer", re.compile(
        r"\b(wire|bank|money)\s+transfers?\b|\btransfer\b[^.\n]{0,25}(\bEUR\b|\bUSD\b|€|\$|\d)|\bpay(ment)?\b[^.\n]{0,20}\bnew (supplier|account|bank)\b",
        re.IGNORECASE)),
    ("asks to change or confirm bank details", re.compile(
        r"\b(change|update|confirm|verify)\b[^.\n]{0,40}\bbank (details|account|information)\b", re.IGNORECASE)),
    ("asks to send codes", re.compile(
        r"\b(send|share|reply with|photos? of|forward)\b[^.\n]{0,40}\b(codes?|card numbers?|pins?)\b", re.IGNORECASE)),
]

_PRESSURE: list[tuple[str, re.Pattern]] = [
    ("urgency", re.compile(
        r"\b(urgent(ly)?|immediately|as soon as possible|asap|right now|within \d+ (minutes|hours))\b", re.IGNORECASE)),
    ("secrecy", re.compile(
        r"\b(confidential|keep (this|it) (between us|to yourself|quiet)|do not tell|don't tell)\b", re.IGNORECASE)),
    ("sender cannot be reached", re.compile(
        r"\b(do not|don't|cannot|can't|can not) (call|phone|reach) me\b|\bin a meeting\b|\bnot available by phone\b",
        re.IGNORECASE)),
]

_RISK_SCORE = 60
_SNIPPET_CHARS = 80


def scan_for_payment_fraud(email: ParsedEmail) -> ToolResult:
    text = "\n".join(part for part in (real_subject(email), email.body_plain, _plain_html(email.body_html)) if part)
    if not text:
        text = email.raw_text

    requests = _find(text, _REQUESTS)
    pressure = _find(text, _PRESSURE)

    if not requests or not pressure:
        return ToolResult(
            tool_name=TOOL_NAME,
            status="success",
            finding="No request for money, gift cards, bank details or codes combined with pressure was found.",
            risk_score=0,
            details={"payment_fraud_detected": False},
            is_offline=True,
        )

    evidence: list[str] = []
    for label, snippet in requests + pressure:
        evidence.append(f'{label}: "{snippet}"')

    request_labels = [label for label, _snippet in requests]
    pressure_labels = [label for label, _snippet in pressure]
    return ToolResult(
        tool_name=TOOL_NAME,
        status="warning",
        finding=(
            "The email " + " and ".join(request_labels) + ", combined with " + " and ".join(pressure_labels) + ". "
            "This is the typical pattern of gift card and payment fraud, even when the sender address is real."
        ),
        risk_score=_RISK_SCORE,
        evidence=evidence,
        details={"payment_fraud_detected": True, "requests": request_labels, "pressure": pressure_labels},
        is_offline=True,
    )


def _find(text: str, patterns: list[tuple[str, re.Pattern]]) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for label, pattern in patterns:
        match = pattern.search(text)
        if match is not None:
            found.append((label, _snippet(text, match.start(), match.end())))
    return found


def _plain_html(body_html: str) -> str:
    if not body_html:
        return ""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body_html))


def _snippet(text: str, start: int, end: int) -> str:
    left = max(0, start - 15)
    right = min(len(text), max(end, left + _SNIPPET_CHARS))
    snippet = re.sub(r"\s+", " ", text[left:right]).strip()
    if len(snippet) > _SNIPPET_CHARS:
        snippet = snippet[:_SNIPPET_CHARS].rstrip() + "..."
    return snippet