"""Pipeline and offline fallback routines for SpotThePhish
Provides:
1. run_offline_fallback: Deterministic, rule-based triage when Ollama is offline.
2. run_fixed_pipeline: Baseline pipeline running all 3 tools sequentially for evaluation."""

from __future__ import annotations
from typing import Any, Callable, Dict, List, Optional, Union
from src.capabilities.tools import (analyze_links, check_email_authentication, inspect_attachments)
from src.schemas.responses import ParsedEmail, ToolResult, Verdict

def run_offline_fallback(email_input: Union[ParsedEmail, Dict[str, Any]]) -> Verdict:
    """Evaluates an email using purely local, deterministic rules without calling an LLM."""

    if isinstance(email_input, dict):
        email_data = ParsedEmail(**email_input)
    else:
        email_data = email_input

    auth_result = check_email_authentication(email_data)
    link_result = analyze_links(email_data)
    att_result = inspect_attachments(email_data)
    tool_results: List[ToolResult] = [auth_result, link_result, att_result]

#combine all evidence points
    all_evidence: List[str] = []
    for res in tool_results:
        all_evidence.extend(res.evidence)

#calculate overall risk score
    scores = [auth_result.risk_score, link_result.risk_score, att_result.risk_score]
    max_risk = max(scores)
    secondary_risk = sum(scores) - max_risk
    total_risk = min(100.0, round(max_risk + (secondary_risk * 0.25), 1))

    if total_risk >= 65.0:
        classification = "phishing"
    elif total_risk >= 30.0:
        classification = "suspicious"
    else:
        classification = "likely safe"

    if all_evidence:
        explanation = f"Offline rule-based check detected warning flags: {' '.join(all_evidence[:3])}"
    else:
        explanation = "Offline rule-based check found no critical authentication, link, or attachment threats."

    if classification == "phishing":
        recommendations = ["Do not click links or open attachments. Report email to IT security."]
    elif classification == "suspicious":
        recommendations = ["Verify sender identity through an alternative channel before responding."]
    else:
        recommendations = ["Email appears legitimate based on available metadata. Normal caution applies."]

    return Verdict(verdict=classification,
        confidence=0.85 if all_evidence else 0.70,
        risk_score=total_risk,
        explanation=explanation,
        evidence=all_evidence,
        recommended_action=" ".join(recommendations),
        tools_run=[r.tool_name for r in tool_results],
        is_fallback=True)

def run_fixed_pipeline(email_input: Union[ParsedEmail, Dict[str, Any]], model_callable: Optional[Callable[[str], str]] = None) -> Verdict:
    """Baseline pipeline evaluation:
    Always runs all 3 tools, then feeds findings into the model. If no model is available it falls back offline."""

    if isinstance(email_input, dict):
        email_data = ParsedEmail(**email_input)
    else:
        email_data = email_input

    if model_callable is None:
        return run_offline_fallback(email_data)

    auth_result = check_email_authentication(email_data)
    link_result = analyze_links(email_data)
    att_result = inspect_attachments(email_data)
    tool_results = [auth_result, link_result, att_result]


    try:
        evidence_summary = (
            f"- Authentication: {auth_result.finding} (Risk: {auth_result.risk_score})\n"
            f"- Links: {link_result.finding} (Risk: {link_result.risk_score})\n"
            f"- Attachments: {att_result.finding} (Risk: {att_result.risk_score})")
        sender_desc = (
            f"{email_data.sender.address} ({email_data.sender.display_name})"
            if email_data.sender and email_data.sender.address
            else "Unknown")
        prompt = (
            "You are an email security analyst. Review the email and tool results to provide a final triage assessment.\n\n"
            f"Subject: {email_data.subject}\n"
            f"Sender: {sender_desc}\n"
            f"Body preview: {email_data.body_plain[:300]}\n\n"
            f"Tool Findings:\n{evidence_summary}\n\n"
            "Classify this email as 'likely safe', 'suspicious', or 'phishing' and provide a brief explanation.")

        llm_response = model_callable(prompt)
        resp_lower = llm_response.lower()
        if "phishing" in resp_lower:
            classification = "phishing"
            risk_score = 85.0
        elif "suspicious" in resp_lower:
            classification = "suspicious"
            risk_score = 50.0
        else:
            classification = "likely safe"
            risk_score = 15.0
        all_evidence: List[str] = []
        for r in tool_results:
            all_evidence.extend(r.evidence)

        return Verdict(verdict=classification,
            confidence=0.80,
            risk_score=risk_score,
            explanation=llm_response.strip(),
            evidence=all_evidence,
            recommended_action="Review findings before interacting with sender.",
            tools_run=[r.tool_name for r in tool_results],
            is_fallback=False)

    except Exception:
        return run_offline_fallback(email_data)