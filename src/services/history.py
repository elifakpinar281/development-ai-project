"""Session history for the web UI: previous analyses, follow-up chats and campaigns.

This is session state, not persistent memory. It lives only in the running
application process and is gone after a restart. The persistent case history in
PostgreSQL (src/capabilities/memory.py) still stores fingerprints only.
for privacy reasons, the full email content is not stored in the persistent case history.
Campaigns: phishing or suspicious emails of this session that share a sender
domain, a link domain or a subject pattern are grouped together. The matching
uses the same fingerprint as the persistent case history.
"""

from __future__ import annotations
 
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
 
from src.schemas.agent import ChatMessage
from src.schemas.responses import ParsedEmail
 
CAMPAIGN_VERDICTS = ("phishing", "suspicious")
RISKY_SCORE = 40
MIN_SUBJECT_WORDS = 3
 
TACTICS = {
    "scan_payment_fraud": "Request for money or payment details",
    "scan_prompt_injection": "Hidden instructions aimed at AI tools",
    "check_lookalike_domain": "Lookalike domain",
    "check_domain_age": "Newly registered domains",
    "check_url_reputation": "Link listed as dangerous",
    "analyze_links": "Deceptive links",
    "check_email_authentication": "Sender cannot be verified",
    "inspect_attachments": "Dangerous attachment",
}
 
NUMBER_WORDS = {2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten"}
 
 
@dataclass
class HistoryEntry:
    entry_id: int
    analysed_at: datetime
    model_name: str
    email: ParsedEmail
    result: object 
    fingerprint: dict
    subject_pattern: str
    messages: list[ChatMessage] = field(default_factory=list)
 
    @property
    def verdict(self) -> str:
        return self.result.verdict.verdict if self.result.verdict else ""
 
    @property
    def risk(self) -> float:
        return self.result.verdict.risk_score if self.result.verdict else 0.0
 
 
@dataclass
class Campaign:
    campaign_id: int
    number: int
    entries: list[HistoryEntry]
 
    @property
    def first(self) -> HistoryEntry:
        return self.entries[0]
 
    @property
    def latest(self) -> HistoryEntry:
        return self.entries[-1]
 
 
def subject_pattern(subject: str) -> str:
    if not isinstance(subject, str):
        return ""
    normalized = subject.strip().lower()
    normalized = re.sub(r"^(?:(?:re|fw|fwd|aw|wg)\s*:\s*)+", "", normalized)
    normalized = re.sub(r"\d+", "#", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if len(normalized.split(" ")) < MIN_SUBJECT_WORDS:
        return ""
    return normalized
 
 
class SessionHistory:
    def __init__(self):
        self._entries: list[HistoryEntry] = []
        self._lock = threading.Lock()
 
    def add(self, email: ParsedEmail, result, model_name: str) -> HistoryEntry:
        fingerprint = email_fingerprint(email)
        with self._lock:
            entry = HistoryEntry(
                entry_id=len(self._entries) + 1,
                analysed_at=datetime.now().astimezone(),
                model_name=model_name,
                email=email,
                result=result,
                fingerprint=fingerprint,
                subject_pattern=subject_pattern(real_subject(email)),
            )
            self._entries.append(entry)
            return entry
 
    def get(self, entry_id: int) -> Optional[HistoryEntry]:
        with self._lock:
            for entry in self._entries:
                if entry.entry_id == entry_id:
                    return entry
        return None
 
    def entries(self) -> list[HistoryEntry]:
        with self._lock:
            return list(self._entries)
 
    def add_message(self, entry_id: int, message: ChatMessage) -> None:
        entry = self.get(entry_id)
        if entry is not None:
            with self._lock:
                entry.messages.append(message)
 
    def campaigns(self) -> list[Campaign]:
        candidates = []
        for entry in self.entries():
            if entry.verdict in CAMPAIGN_VERDICTS:
                candidates.append(entry)
 
        parent = list(range(len(candidates)))
 
        def find(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i
 
        for i in range(len(candidates)):
            for j in range(i + 1, len(candidates)):
                if matched_by(candidates[i], candidates[j]):
                    parent[find(i)] = find(j)
 
        groups: dict[int, list[HistoryEntry]] = {}
        for i, entry in enumerate(candidates):
            groups.setdefault(find(i), []).append(entry)
 
        result: list[Campaign] = []
        ordered = sorted(groups.values(), key=lambda group: group[0].entry_id)
        for group in ordered:
            if len(group) < 2:
                continue
            result.append(Campaign(campaign_id=group[0].entry_id, number=len(result) + 1, entries=group))
        return result
 
    def campaign_of(self, entry_id: int) -> Optional[Campaign]:
        for campaign in self.campaigns():
            for entry in campaign.entries:
                if entry.entry_id == entry_id:
                    return campaign
        return None
 
 
def real_subject(email: ParsedEmail) -> str:
    if not email.has_headers:
        return ""
    return email.subject or ""
 
 
def email_fingerprint(email: ParsedEmail) -> dict:
    try:
        from src.capabilities import memory
 
        return memory.fingerprint(email)
    except Exception:
        links = []
        for link in email.links:
            domain = (link.domain or "").lower()
            if domain and domain not in links:
                links.append(domain)
        return {
            "sender_domain": (email.sender.domain or "").lower(),
            "link_domains": sorted(links),
            "subject_hash": "",
        }
 
 
def matched_by(a: HistoryEntry, b: HistoryEntry) -> list[str]:
    matches: list[str] = []
    sender = a.fingerprint.get("sender_domain", "")
    if sender and sender == b.fingerprint.get("sender_domain", ""):
        matches.append("sender")
    if set(a.fingerprint.get("link_domains", [])) & set(b.fingerprint.get("link_domains", [])):
        matches.append("link")
    if a.subject_pattern and a.subject_pattern == b.subject_pattern:
        matches.append("subject")
    return matches
 
 
def _count_values(values: list[str]) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for value in values:
        if value:
            counts[value] = counts.get(value, 0) + 1
    return sorted(counts.items(), key=lambda item: -item[1])
 
 
def _how_many(count: int, total: int) -> str:
    if count == total:
        return "All " + NUMBER_WORDS.get(total, str(total)) if total > 2 else "Both"
    return NUMBER_WORDS.get(count, str(count)).capitalize()
 
 
def grouping_rows(campaign: Campaign) -> list[dict]:
    total = len(campaign.entries)
    rows: list[dict] = []
    for value, count in _count_values([e.subject_pattern for e in campaign.entries]):
        if count >= 2:
            rows.append({"label": "Subject pattern", "value": value, "count": count, "total": total})
    for value, count in _count_values([e.fingerprint.get("sender_domain", "") for e in campaign.entries]):
        rows.append({"label": "Sender domain", "value": value, "count": count, "total": total})
    sender_domains = {e.fingerprint.get("sender_domain", "") for e in campaign.entries}
    links: list[str] = []
    for entry in campaign.entries:
        links.extend(entry.fingerprint.get("link_domains", []))
    for value, count in _count_values(links):
        if value not in sender_domains:
            rows.append({"label": "Link domain", "value": value, "count": count, "total": total})
    return rows[:6]
 
 
def grouping_text(campaign: Campaign) -> str:
    total = len(campaign.entries)
    sentences: list[str] = []
    subjects = _count_values([e.subject_pattern for e in campaign.entries])
    senders = _count_values([e.fingerprint.get("sender_domain", "") for e in campaign.entries])
    links: list[str] = []
    for entry in campaign.entries:
        links.extend(set(entry.fingerprint.get("link_domains", [])))
    link_counts = _count_values(links)
 
    if subjects and subjects[0][1] >= 2:
        subject_has_number = "#" in subjects[0][0]
        tail = " once the numbers are replaced by #" if subject_has_number else ""
        sentences.append(f"{_how_many(subjects[0][1], total)} emails share the same subject{tail}.")
    if senders and senders[0][1] >= 2:
        also = " also" if sentences else ""
        sentences.append(f"{_how_many(senders[0][1], total)}{also} come from the same sender domain.")
    if link_counts and link_counts[0][1] >= 2:
        also = " also" if sentences else ""
        sentences.append(f"{_how_many(link_counts[0][1], total)}{also} link to the same domain.")
    if len(senders) > 1:
        sentences.append("Switching domains is a common trick to get past blocklists — the wording stays the same.")
    return " ".join(sentences)
 
 
def _risky_tools(entry: HistoryEntry) -> set[str]:
    tools: set[str] = set()
    for observation in getattr(entry.result, "observations", []):
        result = observation.result
        if result.status == "warning" or result.risk_score >= RISKY_SCORE:
            if result.status in ("success", "warning"):
                tools.add(result.tool_name)
    return tools
 
 
def tactics(campaign: Campaign) -> tuple[list[str], bool]:
    per_entry = [_risky_tools(entry) for entry in campaign.entries]
    shared = set.intersection(*per_entry) if per_entry else set()
    in_every = bool(shared)
    chosen = shared if shared else set().union(*per_entry)
    labels = [TACTICS[name] for name in TACTICS if name in chosen]
    return labels, in_every
 
 
def domains_to_block(campaign: Campaign) -> list[str]:
    domains: list[str] = []
    for entry in campaign.entries:
        for domain in [entry.fingerprint.get("sender_domain", "")] + list(entry.fingerprint.get("link_domains", [])):
            if domain and domain not in domains:
                domains.append(domain)
    return domains[:4]
 
 
def what_to_do(campaign: Campaign) -> list[str]:
    tools = set()
    for entry in campaign.entries:
        tools |= _risky_tools(entry)
    if "scan_payment_fraud" in tools:
        first = "Don't pay, send money or enter card details because of these emails."
    else:
        first = "Don't click the links, open attachments or enter any details from these emails."
 
    patterns = {e.subject_pattern for e in campaign.entries}
    senders = {e.fingerprint.get("sender_domain", "") for e in campaign.entries}
    if any("#" in p for p in patterns):
        reason = "the number in the subject changes"
    elif len(senders) > 1:
        reason = "the sender domain changes"
    else:
        reason = "the same attack is sent many times"
    second = f"Warn colleagues — {reason}, so each email can look new."
 
    domains = domains_to_block(campaign)
    if len(domains) > 1:
        third = "Ask IT to block " + ", ".join(domains[:-1]) + " and " + domains[-1] + "."
    elif domains:
        third = f"Ask IT to block {domains[0]}."
    else:
        third = "Report the emails to IT support."
    fourth = "Check real messages only in the organisation's own app or website."
    return [first, second, third, fourth]
 
 
def first_sentence(text: str) -> str:
    text = (text or "").strip()
    match = re.match(r"(.+?[.!?])(\s|$)", text)
    return match.group(1) if match else text
 
 
_history = SessionHistory()
 
 
def session_history() -> SessionHistory:
    return _history
