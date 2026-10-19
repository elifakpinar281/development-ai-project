"""
Email parser for SpotThePhish.

Deterministically parses a pasted raw email or an uploaded .eml file into a structured object containing:
- sender
- Reply-To
- Return-Path
- authentication results (SPF, DKIM, DMARC)
- subject
- body text
- links (displayed text and real target)
- attachment metadata
"""
from __future__ import annotations
import email
import email.header
import email.policy
import email.utils
import ipaddress
import re
from pathlib import Path
from typing import List, Optional, Set, Tuple, Union
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from src.schemas.responses import (AttachmentMetadata, AuthenticationResults, EmailAddress, LinkInfo, ParsedEmail)

# Lists of known shorteners and dangerous file extensions

SHORTENER_DOMAINS = {"bit.ly", "tinyurl.com", "goo.gl", "t.co", "is.gd", "buff.ly", "ow.ly", "cutt.ly", "rebrand.ly", "tiny.cc", "rb.gy", "shorturl.at"}

EXECUTABLE_EXTENSIONS = {".exe", ".scr", ".bat", ".cmd", ".vbs", ".js", ".ps1", ".hta", ".cpl", ".jar", ".iso", ".img", ".wsf", ".msi", ".com", ".pif"}

MACRO_EXTENSIONS = {".docm", ".xlsm", ".pptm", ".dotm", ".xltm"}

ARCHIVE_EXTENSIONS = {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".cab", ".iso"}

# Regex to find plain-text URLs in the body
URL_REGEX = re.compile(r"""(?i)\b((?:https?://|www\d{0,3}[.]|[a-z0-9.\-]+[.][a-z]{2,4}/)(?:[^\s()<>]+|\(([^\s()<>]+|(\([^\s()<>]+\)))\))+(?:\(([^\s()<>]+|(\([^\s()<>]+\)))\)|[^\s`!()\[\]{};:'".,<>?«»“”‘’]))""", re.VERBOSE,)

#Decoding and address splitting functions
def decode_mime_header(header_value : Optional[str]) -> str:
    """Decodes RFC 2047 headers, falls back if the encoding is unusual or malformed."""

    if not header_value:
        return ""
    try:
        decode_chunks = email.header.decode_header(header_value)
        parts = []
        for content, encoding in decode_chunks:
            if isinstance(content, bytes):
                encoding = encoding or "utf-8"
                try:
                    parts.append(content.decode(encoding, errors = "replace"))
                except (LookupError, UnicodeDecodeError):
                    parts.append(content.decode("latin-1", errors="replace"))
            else:
                parts.append(str(content))
        return "".join(parts).strip()
    except Exception:
        return str(header_value).strip()

def parse_address_field(raw_field: Optional[str]) -> EmailAddress:
    """Splits an address like PayPal Support <support@paypal.com>
    into display_name='PayPal Support', address='support@paypal.com', and domain='paypal.com'."""

    if not raw_field:
        return EmailAddress()

    decoded = decode_mime_header(raw_field)
    name, addr = email.utils.parseaddr(decoded)
    clean_addr = addr.strip().lower()
    domain = clean_addr.split("@")[-1] if "@" in clean_addr else ""

    return EmailAddress(display_name = name.strip(), address = clean_addr, domain = domain.lower())

def is_ip_host(hostname: str) -> bool:
    """Checks if a hostname is a raw ip address"""

    if not hostname:
        return False

    clean_host = hostname.strip("[]")
    try:
        ipaddress.ip_address(clean_host)
        return True
    except ValueError:
        return False

def is_punycode_host(hostname: str) -> bool:
    """Checks for Punycode homograph domains"""
    if not hostname:
        return False

    return "xn--" in hostname.lower()

def link_mismatch(display_text: str, target_url: str) -> Tuple[bool, List[str]]:
    """Detects deceptive text: When the text displayed to the user looks like a trusted domain (e.g., paypal.com)
    but the href destination actually points to an attacker domain."""

    reasons = []
    text_clean = display_text.strip()
    parsed_target = urlparse(target_url.strip())
    target_domain = (parsed_target.hostname or "").lower()

    url_like = bool(re.search(r"^[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}(/.*)?$", text_clean)
        or text_clean.startswith("http://")
        or text_clean.startswith("https://")
        or text_clean.startswith("www."))

    if url_like and target_domain:
        text_for_parsing = text_clean if "://" in text_clean else f"http://{text_clean}"
        try:
            parsed_display = urlparse(text_for_parsing)
            display_domain = (parsed_display.hostname or "").lower()

            if display_domain and target_domain != display_domain:
                #allow legitimate subdomains
                if not (target_domain.endswith("." + display_domain) or display_domain.endswith("." + target_domain)):
                    reasons.append(f"Display text '{display_text}' shows '{display_domain}' but actually links to '{target_domain}'")
                    return True, reasons
        except Exception:
            pass

    return False, reasons

#Functions for extracting links from html and text
def extract_links_html(html_content: str) -> List[LinkInfo]:
    """Extracts all hyperlinks from html content."""

    links: List[LinkInfo] = []
    seen = set()
    if not html_content:
        return links

    try:
        soup = BeautifulSoup(html_content, "html.parser")
        for a_tag in soup.find_all("a", href = True):
            href = a_tag["href"].strip()
            #ignore mailto, javascript or page anchors
            if not href or href.startswith("mailto:") or href.startswith("javascript:") or href.startswith("#"):
                continue
            display_text = a_tag.get_text().strip() or href
            parsed = urlparse(href)
            domain = (parsed.hostname or "").lower()
            key = (href, display_text)

            if key in seen:
                continue
            seen.add(key)

            is_ip = is_ip_host(domain)
            is_short = domain in SHORTENER_DOMAINS
            is_puny = is_punycode_host(domain)
            mismatch, mismatch_reasons = link_mismatch(display_text, href)
            risk_reasons = list(mismatch_reasons)
            risk_score = 0.0

            if is_ip:
                risk_score = max(risk_score, 85.0)
                risk_reasons.append(f"Destination hostname is a raw IP address ({domain})")
            if is_puny:
                risk_score = max(risk_score, 80.0)
                risk_reasons.append(f"Destination domain uses Punycode/IDN homograph ({domain})")
            if is_short:
                risk_score = max(risk_score, 50.0)
                risk_reasons.append(f"Destination uses URL shortener service ({domain})")
            if mismatch:
                risk_score = max(risk_score, 90.0)

            links.append(LinkInfo(url=href,
                    display_text=display_text,
                    domain=domain,
                    is_ip_address=is_ip,
                    is_shortener=is_short,
                    is_punycode=is_puny,
                    text_mismatch=mismatch,
                    risk_score=risk_score,
                    risk_reasons=risk_reasons))
    except Exception:
        pass

    return links


def extract_links_text(plain_text: str, seen_urls: Set[str]) -> List[LinkInfo]:
    """Finds plain-text urls in the body of the emailthat weren't found in html."""

    links: List[LinkInfo] = []
    if not plain_text:
        return links

    matches = URL_REGEX.findall(plain_text)
    for match in matches:
        raw_url = match[0] if isinstance(match, tuple) else match
        url = raw_url.strip()
        if not url.startswith("http://") and not url.startswith("https://"):
            url = f"http://{url}"
        #avoids duplicating links already extracted from html
        if url in seen_urls:
            continue
        seen_urls.add(url)
        parsed = urlparse(url)
        domain = (parsed.hostname or "").lower()

        is_ip = is_ip_host(domain)
        is_short = domain in  SHORTENER_DOMAINS
        is_puny = is_punycode_host(domain)
        risk_reasons = []
        risk_score = 0.0

        if is_ip:
            risk_score = max(risk_score, 85.0)
            risk_reasons.append(f"Raw IP address in URL: {domain}")
        if is_puny:
            risk_score = max(risk_score, 80.0)
            risk_reasons.append(f"Punycode homograph domain: {domain}")
        if is_short:
            risk_score = max(risk_score, 50.0)
            risk_reasons.append(f"Shortener service: {domain}")

        links.append(LinkInfo(url=url,
                display_text=raw_url,
                domain=domain,
                is_ip_address=is_ip,
                is_shortener=is_short,
                is_punycode=is_puny,
                text_mismatch=False,
                risk_score=risk_score,
                risk_reasons=risk_reasons))

    return links

#Functions for attachment isnpection and authentication headers
def inspect_attachment(part: email.message.Message) -> Optional[AttachmentMetadata]:
    """Inspects email MIME parts for attachment metadata, doesn't execute anything or writes to disk."""

    filename = part.get_filename()
    content_disposition = str(part.get("Content-Disposition", "")).lower()
    if not filename and "attachment" not  in content_disposition:
        return None

    filename = decode_mime_header(filename or "unnamed_attachment")
    content_type = part.get_content_type()

    payload = part.get_payload(decode=True)
    size_bytes = len(payload) if payload is not None else 0
    clean_name = filename.strip()
    name_parts = clean_name.split(".")
    ext = f".{name_parts[-1].lower()}" if len(name_parts) > 1 else ""

#detects double extensions eg: 'invoice.pdf.exe'
    has_double = False
    if len(name_parts) > 2:
        inner_ext = f".{name_parts[-2].strip().lower()}"
        if inner_ext in {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".txt", ".jpg", ".png"}:
            has_double = True

    is_exec = ext in EXECUTABLE_EXTENSIONS
    is_macro = ext in MACRO_EXTENSIONS
    is_arch = ext in ARCHIVE_EXTENSIONS
    risk_flag = False
    risk_reasons = []

    if has_double:
        risk_flag = True
        risk_reasons.append(f"Dangerous double extension detected: '{filename}'")
    if is_exec:
        risk_flag = True
        risk_reasons.append(f"High-risk executable/script attachment: '{filename}'")
    if is_macro:
        risk_flag = True
        risk_reasons.append(f"Macro-enabled document format: '{filename}'")

    return AttachmentMetadata(filename=clean_name,
        content_type=content_type,
        size_bytes=size_bytes,
        extension=ext,
        double_extension=has_double,
        is_executable_or_script=is_exec,
        is_archive=is_arch,
        is_macro_enabled=is_macro,
        risk_flag=risk_flag,
        risk_reasons=risk_reasons)

def parse_auth_headers(msg: email.message.Message) -> AuthenticationResults:
    """Extracts SPF, DKIM, DMARC results from standard email headers."""

    raw_headers = {}
    auth_results = AuthenticationResults()
    auth_headers = msg.get_all("Authentication-Results", [])
    spf_headers = msg.get_all("Received-SPF", [])
    arc_headers = msg.get_all("ARC-Authentication-Results", [])

    raw_headers["Authentication-Results"] = [str(h) for h in auth_headers]
    raw_headers["Received-SPF"] = [str(h) for h in spf_headers]
    raw_headers["ARC-Authentication-Results"] = [str(h) for h in arc_headers]

#parse SPF header
    for spf in spf_headers:
        spf_str = str(spf).strip().lower()
        for token in ["pass", "fail", "softfail", "neutral", "none", "temperror", "permerror"]:
            if spf_str.startswith(token) or f" {token} " in f" {spf_str} ":
                auth_results.spf_status = token
                auth_results.spf_details = str(spf).strip()
                break
        if auth_results.spf_status:
            break

#parse authentication results header
    for auth in auth_headers:
        auth_text = str(auth).lower()
        if not auth_results.spf_status:
            m = re.search(r"\bspf=(\w+)", auth_text)
            if m:
                auth_results.spf_status = m.group(1)
        if not auth_results.dkim_status:
            m = re.search(r"\bdkim=(\w+)", auth_text)
            if m:
                auth_results.dkim_status = m.group(1)
        if not auth_results.dmarc_status:
            m = re.search(r"\bdmarc=(\w+)", auth_text)
            if m:
                auth_results.dmarc_status = m.group(1)

#fallback: check if a DKIM signature header is physically attached
    if not auth_results.dkim_status:
        dkim_sigs = msg.get_all("DKIM-Signature", [])
        auth_results.dkim_status = "present_unverified" if dkim_sigs else "none"
    if not auth_results.spf_status:
        auth_results.spf_status = "none"
    if not auth_results.dmarc_status:
        auth_results.dmarc_status = "none"

    auth_results.raw_headers = raw_headers
    return auth_results

#Main parser function
def parse_email(content: Union[str, bytes, Path]) -> ParsedEmail:
    """Accepts:
    - Path object or string file path pointing to an .eml file
    - Raw RFC 822 string or bytes
    - Raw pasted email body text (without headers)

    Returns:
    - ParsedEmail object with all extracted components."""

    raw_str = ""

#load content if given a file path
    if isinstance(content, Path) or (isinstance(content, str)) and "\n" not in content and Path(content).exists():
        p =Path(content)
        try:
            raw_str = p.read_text(encoding= "utf-8", errors="replace")
        except Exception:
            raw_str = p.read_bytes().decode("latin-1", errors="replace")
    elif isinstance(content, bytes):
        raw_str = content.decode("utf-8", errors="replace")
    else:
        raw_str = str(content)

#check if the text has RFC headers or if it's just raw pasted body text
    first_lines = raw_str[:2000].splitlines()
    has_header_indicators = any(re.match(r"^(From|To|Subject|Date|Return-Path|Received|Content-Type):\s*", line, re.IGNORECASE)
        for line in first_lines[:20])

    if not has_header_indicators:
        #user pasted raw text without headers
        links = extract_links_text(raw_str, set())
        return ParsedEmail(raw_text=raw_str,
            subject="(No Subject - Raw Text)",
            sender=EmailAddress(display_name="Unknown", address="", domain=""),
            body_plain=raw_str,
            body_html="",
            links=links,
            attachments=[],
            auth_results=AuthenticationResults(spf_status="none", dkim_status="none", dmarc_status="none"),
            headers={},
            has_headers=False)

#email parsing
    msg = email.message_from_string(raw_str, policy= email.policy.compat32)
    headers_dict = {k: decode_mime_header(v) for k, v in msg.items()}
    subject = decode_mime_header(msg.get("Subject", ""))
    sender = parse_address_field(msg.get("From"))
    reply_to = parse_address_field(msg.get("Reply-To")) if msg.get("Reply-To") else None
    return_path = parse_address_field(msg.get("Return-Path")) if msg.get("Return-Path") else None

#parse recipients
    recipients: List[EmailAddress] = []
    to_field = msg.get("To")
    if to_field:
        for _, addr in email.utils.getaddresses([decode_mime_header(to_field)]):
            if addr:
                recipients.append(parse_address_field(addr))

    date_str = decode_mime_header(msg.get("Date", ""))
    msg_id = decode_mime_header(msg.get("Message-ID", ""))

    plain_chunks: List[str] = []
    html_chunks: List[str] = []
    attachments: List[AttachmentMetadata] = []

#go through MIME compartments for text, html and attachments
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            cd = str(part.get("Content-Disposition", "")).lower()
            att = inspect_attachment(part)

            if att:
                attachments.append(att)
                continue

            if ct == "text/plain" and "attachment" not in cd:
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    plain_chunks.append(payload.decode(charset, errors="replace"))
            elif ct == "text/html" and "attachment" not in cd:
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    html_chunks.append(payload.decode(charset, errors="replace"))
    else:
        ct = msg.get_content_type()
        att = inspect_attachment(msg)
        if att:
            attachments.append(att)
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                charset = msg.get_content_charset() or "utf-8"
                decoded_body = payload.decode(charset, errors="replace")
                if ct == "text/html":
                    html_chunks.append(decoded_body)
                else:
                    plain_chunks.append(decoded_body)

    body_plain = "\n".join(plain_chunks).strip()
    body_html = "\n".join(html_chunks).strip()

#handle html only emails
    derived_from_html = False
    if not body_plain and body_html:
        derived_from_html = True
        try:
            soup = BeautifulSoup(body_html, "html.parser")
            for script_or_style in soup(["script", "style"]):
                script_or_style.decompose()
            body_plain = soup.get_text(separator="\n").strip()
        except Exception:
            body_plain = body_html

 #extract links, first html then plain text
    links: List[LinkInfo] = []
    seen_urls: Set[str] = set()
    if body_html:
        html_links = extract_links_html(body_html)
        for link in html_links:
            links.append(link)
            seen_urls.add(link.url)
            if link.display_text:
                seen_urls.add(link.display_text.strip())

    if not derived_from_html and body_plain:
        text_links = extract_links_text(body_plain, seen_urls)
        links.extend(text_links)

    # Parse SPF, DKIM, and DMARC
    auth_results = parse_auth_headers(msg)
    return ParsedEmail(
        raw_text=raw_str,
        subject=subject,
        sender=sender,
        reply_to=reply_to,
        return_path=return_path,
        recipients=recipients,
        date=date_str or None,
        message_id=msg_id or None,
        body_plain=body_plain,
        body_html=body_html,
        links=links,
        attachments=attachments,
        auth_results=auth_results,
        headers=headers_dict,
        has_headers=True)