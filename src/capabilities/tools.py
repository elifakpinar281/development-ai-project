"""Tools for SpotThePhish

Contains the 3 local, network independent email inspection tools:

1. check_email_authentication: SPF, DKIM, DMARC and sender header mismatches
2.analyze_links: anchor text deception, raw IP URLs, shorteners and Punycode
3.inspect_attachments: safe metadata inspection(double extensions, executables, macros)
"""

from __future__ import annotations
from typing import Any, Callable, Dict, List, Union
from src.schemas.responses import (AttachmentMetadata, LinkInfo, ParsedEmail, ToolResult)

#Used to catch display name spoofing
BRAND_KEYWORDS = ["paypal", "apple", "google", "microsoft", "amazon", "netflix", "revolut", "gls", "ups", "dhl", "security", "helpdesk", "billing", "admin"]

#first email tool
def check_email_authentication(email_input:Union[ParsedEmail, Dict[str, Any]]) -> ToolResult:
   """Evaluates SPF, DKIM, and DMARC results from headers and checks for identity mismatches between From, Reply-To, and Return-Path."""

   if isinstance(email_input, dict):
      email_data = ParsedEmail(**email_input)
   else:
      email_data = email_input

   auth = email_data.auth_results
   sender = email_data.sender
   reply_to = email_data.reply_to
   return_path = email_data.return_path
   evidence: List[str] = []
   risk_score = 0.0
   details: Dict[str,Any] = {"spf_status": auth.spf_status or "none",
                             "dkim_status": auth.dkim_status or "none",
                              "dmarc_status": auth.dmarc_status or "none",
                              "from_address": sender.address if sender else "",
                              "reply_to_address": reply_to.address if reply_to else "",
                              "return_path_address": return_path.address if return_path else "",
                              "mismatches": [], "spoofing_flags": []}

#if the user pasted a plain body text without headers, authentication can't be verified
   if not email_data.has_headers:
      return ToolResult(tool_name="check_email_authentication",
            status="warning",
            finding="Email lacks standard RFC 822 authentication headers (raw pasted body).",
            risk_score=35.0,
            evidence=["No RFC 822 email headers found. Authentication cannot be verified."],
            details=details,
            is_offline=True)

   spf_status = (auth.spf_status or "none").lower()
   if spf_status == "fail":
      risk_score += 40.0
      evidence.append("SPF failed: sending IP is not authorized by the sender domain's SPF record.")
   elif spf_status == "softfail":
      risk_score+= 25.0
      evidence.append("SPF softfail: sender IP is not officially designated; domain discourages acceptance.")
   elif spf_status in ("permerror", "temperror"):
      risk_score +=15.0
      evidence.append(f"SPF DNS error occurred during verification ({spf_status}).")

   dkim_status = (auth.dkim_status or "none").lower()
   if dkim_status == "fail":
      risk_score+= 40.0
      evidence.append("DKIM verification failed: digital signature is invalid or message was tampered with.")

   dmarc_status = (auth.dmarc_status or "none").lower()
   if dmarc_status == "fail":
      risk_score += 45.0
      evidence.append("DMARC failed: message failed domain alignment policy.")
   elif dmarc_status == "pass":
      risk_score = max(0.0, risk_score- 10.0)


   from_dom = (sender.domain or "").lower()
   reply_dom = (reply_to.domain or "").lower() if reply_to and reply_to.address else ""
   return_dom = (return_path.domain or "").lower() if return_path and return_path.address else ""

#flag if replies are routed to a completely different domain
   if reply_dom and from_dom and reply_dom != from_dom:
      if not (reply_dom.endswith("."+ from_dom) or from_dom.endswith("."+ reply_dom)):
         risk_score += 45.0
         msg = f"Reply-To mismatch: 'From' is '{from_dom}', but replies go to '{reply_dom}' ({reply_to.address})."
         evidence.append(msg)
         details["mismatches"].append(msg)

#flag if bounce address routes to a different domain
   if return_dom and from_dom and return_dom != from_dom:
      if not (return_dom.endswith("." + from_dom) or from_dom.endswith("." + return_dom)):
         risk_score += 25.0
         msg = f"Return-Path mismatch: 'From' is '{from_dom}', but bounces route to '{return_dom}'."
         evidence.append(msg)
         details["mismatches"].append(msg)

   display_name = (sender.display_name or "").lower()
   if display_name and from_dom:
      for brand in BRAND_KEYWORDS:
         if brand in display_name and brand not in from_dom:
            if any(free in from_dom for free in ["gmail", "yahoo", "hotmail", "outlook", "mail.ru", "proton"]):
               risk_score += 50.0
               msg = f"Brand impersonation: display name contains '{brand}' but email domain is free webmail ({from_dom})."
               evidence.append(msg)
               details["spoofing_flags"].append(msg)
               break

   final_risk = min(100.0, round(risk_score, 1))
   if final_risk >= 70.0:
      status = "warning"
      finding = f"High authentication risk ({final_risk:.0f}/100): Spoofing or authentication failure detected."
   elif final_risk >= 30.0:
      status = "warning"
      finding = f"Moderate authentication risk ({final_risk:.0f}/100): Inconsistencies found in sender headers."
   else:
      status = "success"
      finding = f"Low authentication risk ({final_risk:.0f}/100): Headers appear authentic."
      if not evidence:
         evidence.append("SPF, DKIM, and DMARC alignments show no signs of sender forgery.")

   return ToolResult(tool_name="check_email_authentication",
        status=status,
        finding=finding,
        risk_score=final_risk,
        evidence=evidence,
        details=details,
        is_offline=True)

#second email tool
def analyze_links(email_input: Union[ParsedEmail, Dict[str, Any]]) -> ToolResult:
   """Analyzes all hyperlinks in the email for deceptive patterns:

    - Text mismatches (href destination differs from displayed text)
    - Raw IP address destinations
    - URL shorteners (bit.ly, tinyurl)
    - Punycode homographs (xn--)"""

   if isinstance(email_input, ParsedEmail):
      links = email_input.links
   elif isinstance(email_input, dict):
      links = [LinkInfo(**item) if isinstance(item, dict) else item for item in email_input.get("links", [])]
   else:
      links = []

   if not links:
      return ToolResult(tool_name="analyze_links",
            status="success",
            finding="No hyperlinks found in the email body.",
            risk_score=0.0,
            evidence=["Zero hyperlinks identified; no URL-based threat vectors present."],
            details={"total_links": 0, "flagged_links": 0},
            is_offline=True)

   evidence: List[str] = []
   max_risk = 0.0
   flagged_urls: List[Dict[str, Any]] = []
   mismatch_count = 0
   ip_count = 0
   shortener_count = 0
   punycode_count = 0

   for link in links:
      link_risk = link.risk_score
      if link.text_mismatch:
         mismatch_count += 1
         link_risk = max(link_risk, 90.0)
         msg = f"Deceptive anchor text: displayed '{link.display_text}' but links to '{link.url}'"
         if msg not in evidence:
            evidence.append(msg)

      if link.is_ip_address:
         ip_count += 1
         link_risk = max(link_risk, 85.0)
         msg = f"Raw IP address link destination: '{link.url}'"
         if msg not in evidence:
            evidence.append(msg)

      if link.is_punycode:
         punycode_count += 1
         link_risk = max(link_risk, 80.0)
         msg = f"Punycode homograph domain detected: '{link.domain}'"
         if msg not in evidence:
            evidence.append(msg)

      if link.is_shortener:
         shortener_count += 1
         link_risk = max(link_risk, 50.0)
         msg = f"URL shortener hides actual destination: '{link.domain}' ({link.url})"
         if msg not in evidence:
            evidence.append(msg)

      if link_risk > 0.0:
         flagged_urls.append({"url" : link.url, "display_text" : link.display_text, "risk": link_risk})

      max_risk = max(max_risk, link_risk)

   final_risk = min(100.0, round(max_risk, 1))
   total_links = len(links)
   flagged_count = len(flagged_urls)
   details = {"total_links": total_links,
        "flagged_links": flagged_count,
        "mismatches": mismatch_count,
        "ip_links": ip_count,
        "shorteners": shortener_count,
        "punycode_domains": punycode_count,
        "flagged_url_details": flagged_urls[:5]}

   if final_risk >= 70.0:
      status = "warning"
      finding = f"High link risk ({final_risk:.0f}/100): Deceptive links or severe obfuscation ({flagged_count}/{total_links} links flagged)."
   elif final_risk >= 35.0:
      status = "warning"
      finding = f"Moderate link risk ({final_risk:.0f}/100): Shorteners or suspicious URLs found ({flagged_count}/{total_links} links flagged)."
   else:
      status = "success"
      finding = f"Low link risk ({final_risk:.0f}/100): {total_links} link(s) checked; no deceptive patterns found."
      if not evidence:
         evidence.append(f"All {total_links} links match their displayed domains without obfuscation.")

   return ToolResult(tool_name="analyze_links",
        status=status,
        finding=finding,
        risk_score=final_risk,
        evidence=evidence,
        details=details,
        is_offline=True)

#third email tool
def inspect_attachments(email_input: Union[ParsedEmail, Dict[str, Any]]) -> ToolResult:
   """Inspects attachment metadata safely without opening or executing files:

    - Flags double extensions (e.g. invoice.pdf.exe)
    - Flags executable binaries and scripts (.exe, .scr, .vbs, .js, .bat)
    - Flags macro-enabled documents (.docm, .xlsm)"""

   if isinstance(email_input, ParsedEmail):
      attachments = email_input.attachments
   elif isinstance(email_input, dict):
      attachments = [AttachmentMetadata(**item) if isinstance(item, dict) else item for item in email_input.get("attachments", [])]
   else:
      attachments = []

   if not attachments:
      return ToolResult(tool_name="inspect_attachments",
            status="success",
            finding="No attachments detected in this email.",
            risk_score=0.0,
            evidence=["Zero attachments present; no attachment-based threat."],
            details={"total_attachments": 0, "flagged_attachments": 0},
            is_offline=True)

   evidence: List[str] = []
   max_risk = 0.0
   flagged_count = 0

   for att in attachments:
      att_risk = 0.0
      if att.double_extension:
         att_risk = max(att_risk, 95.0)
         evidence.append(f"Dangerous double extension: '{att.filename}' masquerades as a document but is executable.")

      if att.is_executable_or_script:
         att_risk = max(att_risk, 90.0)
         evidence.append(f"High risk executable/script attachment: '{att.filename}'.")

      if att.is_macro_enabled:
         att_risk = max(att_risk, 75.0)
         evidence.append(f"Macro-enabled document format: '{att.filename}', can carry malicious VBA macros.")

      if att.is_archive:
         att_risk = max(att_risk, 35.0)
         evidence.append(f"Compressed archive attachment: '{att.filename}' (often used to bundle payloads)")

      if att_risk > 0.0:
         flagged_count += 1
      max_risk = max(max_risk, att_risk)

   final_risk = min(100.0, round(max_risk, 1))
   details = {"total_attachments": len(attachments), "flagged_attachments": flagged_count}

   if final_risk >= 75.0:
      status = "warning"
      finding = f"High attachment risk ({final_risk:.0f}/100): Executable or disguised attachment detected."
   elif final_risk>= 30.0:
      status = "warning"
      finding = f"Moderate attachment risk ({final_risk:.0f}/100): Compressed archive format."
   else:
      status = "success"
      finding = f"Low attachment risk ({final_risk:.0f}/100): {len(attachments)} safe non-executable attachment(s)."
      if not evidence:
         evidence.append("All attachments have benign document extensions.")

   return ToolResult(tool_name="inspect_attachments",
        status=status,
        finding=finding,
        risk_score=final_risk,
        evidence=evidence,
        details=details,
        is_offline=True)

#tool schemas passed to Ollama so the local LLM knows what tools it can call
TOOL_DEFINITIONS = [{
        "type": "function",
        "function": {
            "name": "check_email_authentication",
            "description": "Examines SPF, DKIM, and DMARC results from headers and detects From vs. Reply-To mismatches or sender spoofing.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_links",
            "description": "Inspects all hyperlinks in the email for deceptive anchor text, raw IP destinations, URL shorteners, and Punycode homographs.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "inspect_attachments",
            "description": "Safely inspects attachment metadata to flag dangerous double extensions (e.g. invoice.pdf.exe), executables, and macros.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
   }]

#for the agent loop
TOOL_REGISTRY: Dict[str, Callable[[ParsedEmail], ToolResult]] = {
    "check_email_authentication": check_email_authentication,
    "analyze_links": analyze_links,
    "inspect_attachments": inspect_attachments}