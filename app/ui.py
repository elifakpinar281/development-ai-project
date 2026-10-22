from __future__ import annotations

import base64
import json
import re
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Optional

import gradio as gr

from src.config import config
from src.email_parser import parse_email
from src.schemas.agent import ChatMessage
from src.schemas.responses import ParsedEmail
from src.services.ai_service import analyze_email
from src.services.follow_up import answer_question
from src.services.history import (
    Campaign,
    HistoryEntry,
    domains_to_block,
    first_sentence,
    grouping_rows,
    grouping_text,
    matched_by,
    session_history,
    tactics,
    what_to_do,
)
from src.services.model_choice import available_models, get_model_client, get_service_for_model

FRONTEND = Path(__file__).parent / "frontend"

TOOL_TITLES = {
    "scan_prompt_injection": "Prompt-injection scan",
    "scan_payment_fraud": "Payment-fraud scan",
    "check_lookalike_domain": "Lookalike-domain check",
    "analyze_links": "Link analysis",
    "check_email_authentication": "Sender authentication",
    "inspect_attachments": "Attachment check",
    "check_domain_age": "Domain-age check",
    "check_url_reputation": "URL reputation",
    "lookup_case_history": "Case history lookup",
}

EVIDENCE_TOOL = re.compile(r"^\[#(\d+) ([a-z_]+)\] (.*)$", re.S)
EVIDENCE_QUOTE = re.compile(r'^\[email text\] "(.*)": (.*)$', re.S)



def _ordinal(day: int) -> str:
    if 11 <= day % 100 <= 13:
        return f"{day}th"
    suffixes = {1: "st", 2: "nd", 3: "rd"}
    return f"{day}{suffixes.get(day % 10, 'th')}"


def _short_date(moment: datetime) -> str:
    return f"{moment.day} {moment:%b}"


def _long_date(moment: datetime) -> str:
    return f"{_ordinal(moment.day)} {moment:%B %Y}"


def _analysed_label(moment: datetime) -> str:
    return f"{moment:%b} {_ordinal(moment.day)}, {moment:%H:%M}"


def _email_date(value: Optional[str]) -> str:
    if not value:
        return ""
    try:
        moment = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return value
    return f"{moment.day} {moment:%b %Y, %H:%M}"


def _number_word(count: int) -> str:
    words = {1: "One", 2: "Two", 3: "Three", 4: "Four", 5: "Five", 6: "Six"}
    return words.get(count, str(count))



def _subject(entry: HistoryEntry) -> str:
    if not entry.email.has_headers:
        return "Pasted email text"
    return entry.email.subject or "(no subject)"


def _sender(entry: HistoryEntry) -> str:
    return entry.email.sender.address or str(entry.email.sender) or "unknown sender"


def _campaign_title(campaign: Campaign) -> str:
    return _subject(campaign.first)


def _summary(entry: HistoryEntry, campaign: Optional[Campaign]) -> dict:
    questions = 0
    for message in entry.messages:
        if message.role == "user":
            questions += 1
    if questions == 0:
        conversation = "Verdict only"
    elif questions == 1:
        conversation = "1 question"
    else:
        conversation = f"{questions} questions"
    return {
        "id": entry.entry_id,
        "verdict": entry.verdict,
        "subject": _subject(entry),
        "sender": _sender(entry),
        "date": _short_date(entry.analysed_at),
        "conversation": conversation,
        "campaign": _campaign_title(campaign) if campaign else "",
        "campaign_id": campaign.campaign_id if campaign else None,
    }


def _campaign_view(campaign: Campaign) -> dict:
    risks = [entry.risk for entry in campaign.entries]
    tactic_labels, in_every = tactics(campaign)
    chips: list[str] = []
    for row in grouping_rows(campaign):
        if row["count"] >= 2 and row["value"] not in chips:
            chips.append(row["value"])
    return {
        "id": campaign.campaign_id,
        "number": campaign.number,
        "title": _campaign_title(campaign),
        "description": first_sentence(campaign.latest.result.verdict.explanation),
        "count": len(campaign.entries),
        "avg_risk": round(sum(risks) / len(risks)),
        "first_seen": _long_date(campaign.first.analysed_at),
        "last_seen": _long_date(campaign.latest.analysed_at),
        "last_seen_short": _short_date(campaign.latest.analysed_at),
        "chips": chips[:3],
        "grouping_text": grouping_text(campaign),
        "rows": grouping_rows(campaign),
        "attackers_want": campaign.latest.result.verdict.explanation,
        "tactics": tactic_labels,
        "tactics_in_every": in_every,
        "todo": what_to_do(campaign),
        "domains": domains_to_block(campaign),
        "emails": [entry.entry_id for entry in campaign.entries],
    }


def _evidence(items: list[str]) -> list[dict]:
    result = []
    for item in items:
        tool = EVIDENCE_TOOL.match(item)
        quote = EVIDENCE_QUOTE.match(item)
        if tool:
            number, name, claim = tool.groups()
            title = TOOL_TITLES.get(name, name)
            result.append({"claim": claim, "detail": f"Check #{number}: {title}", "quote": False})
        elif quote:
            text, claim = quote.groups()
            result.append({"claim": claim, "detail": f"“{text}”", "quote": True})
        else:
            result.append({"claim": item, "detail": "", "quote": False})
    return result


def _trace(entry: HistoryEntry) -> list[dict]:
    risk_by_observation = {}
    for observation in getattr(entry.result, "observations", []):
        risk_by_observation[observation.observation_id] = observation.result

    steps = []
    for step in entry.result.trace:
        item = {"n": step.step, "title": "", "tool": step.tool_name or "", "meta": "",
                "why": step.reason, "label": "", "tone": "", "text": step.summary,
                "automatic": step.kind == "automatic_check"}
        if step.kind in ("automatic_check", "tool_call"):
            item["title"] = TOOL_TITLES.get(step.tool_name or "", step.tool_name or "Check")
            who = "automatic" if step.kind == "automatic_check" else "chosen by agent"
            item["meta"] = f"{who} - {step.duration_ms} ms"
            result = risk_by_observation.get(step.observation_id)
            if result is None or result.status in ("error", "unavailable"):
                item["label"], item["tone"] = "Unavailable.", "warn"
            elif result.risk_score >= 60:
                item["label"], item["tone"] = "Risk found:", "risk"
            elif result.status == "warning" or result.risk_score >= 25:
                item["label"], item["tone"] = "Warning.", "warn"
            else:
                item["label"], item["tone"] = "Clear.", "clear"
        elif step.kind == "final_answer":
            item["title"] = "Final assessment"
            item["meta"] = "model" if not entry.result.verdict.is_fallback else "rule-based"
            item["why"] = step.reason or "Enough evidence to finish."
            item["label"], item["tone"] = "Done:", "done"
            verdict = entry.result.verdict
            item["text"] = f"Verdict {verdict.verdict} ({verdict.risk_score:.0f}/100)"
        else:
            titles = {"rejected_call": "Rejected check", "repeated_call": "Repeated check",
                      "invalid_output": "Invalid model output", "step_limit": "Step limit reached"}
            item["title"] = titles.get(step.kind, step.kind)
            item["meta"] = "application"
            item["label"], item["tone"] = "Warning.", "warn"
        steps.append(item)
    return steps


def _saved_to_history(result) -> bool:
    saved = getattr(result, "saved_to_history", None)
    if saved is not None:
        return bool(saved)
    if not config.case_history_write or result.verdict.is_fallback:
        return False
    for warning in result.warnings:
        if "case history" in warning.lower():
            return False
    return True


def _entry_view(entry: HistoryEntry) -> dict:
    result = entry.result
    verdict = result.verdict
    campaign = session_history().campaign_of(entry.entry_id)
    campaign_view = None
    if campaign is not None:
        others = [other for other in campaign.entries if other.entry_id != entry.entry_id]
        matched: list[str] = []
        for other in others:
            for part in matched_by(entry, other):
                if part not in matched:
                    matched.append(part)
        if not matched:
            matched_text = "matched the campaign"
        else:
            ordered = [part for part in ("sender", "link", "subject") if part in matched]
            matched_text = "matched the " + (", ".join(ordered[:-1]) + " and " + ordered[-1] if len(ordered) > 1 else ordered[0])
        similar = len(others)
        noun = "similar email" if similar == 1 else "similar emails"
        since = _short_date(others[0].analysed_at)
        campaign_view = {
            "id": campaign.campaign_id,
            "title": _campaign_title(campaign),
            "text": f"{similar} {noun} since {since} {matched_text}",
        }

    warnings = list(result.warnings)
    if verdict.is_fallback:
        warnings.insert(0, "Offline fallback: the AI model was not used, so language-based signs were not analysed.")

    return {
        "id": entry.entry_id,
        "verdict": verdict.verdict,
        "risk": round(verdict.risk_score),
        "confidence": round(verdict.confidence * 100),
        "explanation": verdict.explanation,
        "action": verdict.recommended_action,
        "analysed_at": _analysed_label(entry.analysed_at),
        "from": str(entry.email.sender) or "(missing)",
        "subject": _subject(entry),
        "date": _email_date(entry.email.date),
        "model": entry.model_name,
        "checks": len(verdict.tools_run),
        "seconds": f"{result.duration_ms / 1000:.1f} s",
        "model_calls": result.model_calls,
        "saved": _saved_to_history(result),
        "campaign": campaign_view,
        "evidence": _evidence(verdict.evidence),
        "warnings": warnings,
        "safety_rules": verdict.safety_rules_applied,
        "trace": _trace(entry),
        "messages": [{"role": m.role, "content": m.content} for m in entry.messages],
    }


def get_overview(request: dict) -> dict:
    history = session_history()
    campaigns = history.campaigns()
    campaign_by_entry = {}
    for campaign in campaigns:
        for entry in campaign.entries:
            campaign_by_entry[entry.entry_id] = campaign
    emails = []
    for entry in reversed(history.entries()):
        emails.append(_summary(entry, campaign_by_entry.get(entry.entry_id)))
    overview = {
        "emails": emails,
        "campaigns": [_campaign_view(campaign) for campaign in campaigns],
        "default_model": config.model_name,
    }
    if isinstance(request, dict) and request.get("with_models"):
        overview["models"] = available_models()
    return overview


def analyze(request: dict) -> dict:
    text = str(request.get("text") or "")
    model = str(request.get("model") or config.model_name)
    result = analyze_email(text, get_service_for_model(model))
    if result.verdict is None:
        return {"rejected": True, "message": result.message}
    try:
        email = parse_email(text)
    except Exception:
        email = ParsedEmail(raw_text=text, body_plain=text, has_headers=False)
    entry = session_history().add(email, result, model)
    return _entry_view(entry)


def get_entry(request: dict) -> dict:
    entry = session_history().get(int(request.get("id") or 0))
    if entry is None:
        return {"missing": True}
    return _entry_view(entry)


def ask(request: dict) -> dict:
    history = session_history()
    entry = history.get(int(request.get("id") or 0))
    if entry is None:
        return {"missing": True}
    question = str(request.get("question") or "").strip()
    if not question:
        return _entry_view(entry)
    answer = answer_question(get_model_client(entry.model_name), entry, question)
    history.add_message(entry.entry_id, ChatMessage(role="user", content=question))
    history.add_message(entry.entry_id, ChatMessage(role="assistant", content=answer))
    return _entry_view(entry)


def _read(name: str) -> str:
    return (FRONTEND / name).read_text(encoding="utf-8")


def _logo_data_uri() -> str:
    data = base64.b64encode((FRONTEND / "logo.png").read_bytes()).decode("ascii")
    return "data:image/png;base64," + data


FONT_FILES = [
    ("DM Serif Display", "DMSerifDisplay-Regular.ttf", "400", "normal"),
    ("Inria Serif", "InriaSerif-Bold.ttf", "700", "normal"),
    ("Inria Serif", "InriaSerif-BoldItalic.ttf", "700", "italic"),
    ("Inter", "Inter-Variable.ttf", "100 900", "normal"),
    ("JetBrains Mono", "JetBrainsMono-Variable.ttf", "100 800", "normal"),
]


def _font_css() -> str:
    rules = []
    for family, filename, weight, style in FONT_FILES:
        url = "/gradio_api/file=" + (FRONTEND / "fonts" / filename).resolve().as_posix()
        rules.append(
            f"@font-face {{ font-family: '{family}'; src: url('{url}') format('truetype'); "
            f"font-weight: {weight}; font-style: {style}; font-display: swap; }}"
        )
    return "\n".join(rules)


SCOPE_MARKER = "/* @scope-start"
SCOPE = "#stp-app"


def _scope_selectors(css: str) -> str:
    output: list[str] = []
    buffer = ""
    depth = 0
    for char in css:
        if char == "{":
            header = buffer.strip()
            if header.startswith("@"):
                output.append(buffer + "{")
            elif depth <= 1:
                selectors = []
                for selector in header.split(","):
                    selector = selector.strip()
                    if selector:
                        selectors.append(SCOPE + " " + selector)
                output.append(_leading_space(buffer) + ", ".join(selectors) + " {")
            else:
                output.append(buffer + "{")
            buffer = ""
            depth += 1
        elif char == "}":
            output.append(buffer + "}")
            buffer = ""
            depth -= 1
        else:
            buffer += char
    output.append(buffer)
    return "".join(output)


def _leading_space(text: str) -> str:
    stripped = text.lstrip()
    return text[: len(text) - len(stripped)]


def _page_css() -> str:
    css = _read("app.css")
    marker = css.find(SCOPE_MARKER)
    if marker >= 0:
        css = css[:marker] + _scope_selectors(_strip_comments(css[marker:]))
    return _font_css() + "\n" + css


def _strip_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def _page_js() -> str:
    return "const STP_CSS = " + json.dumps(_page_css()) + ";\n" + _read("app.js")


def launch_options() -> dict:
    return {}


def build_ui() -> gr.Blocks:
    gr.set_static_paths(paths=[FRONTEND / "fonts"])
    shell = _read("shell.html").replace("{{LOGO}}", _logo_data_uri())
    with gr.Blocks(title="Spot the phish.", fill_width=True) as demo:
        gr.HTML(
            html_template=shell,
            js_on_load=_page_js(),
            server_functions=[get_overview, analyze, get_entry, ask],
            apply_default_css=False,
            container=False,
            padding=False,
            elem_id="stp-app",
        )
    demo.queue(default_concurrency_limit=4)
    return demo