from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx
import tldextract

from src.capabilities import memory
from src.capabilities.eval_cache import cached_online
from src.capabilities.known_domains import KNOWN_BRANDS
from src.capabilities.tool_boundary import tool_boundary
from src.config import config
from src.exceptions import (InvalidDomainError, InvalidToolInputError, InvalidUrlError, MissingApiKeyError, RateLimitError, ServiceResponseError, ServiceTimeoutError,)
from src.schemas.responses import ToolResult

import logging

for _name in ("httpx", "httpcore"):
    logging.getLogger(_name).setLevel(logging.WARNING)

_TLD = tldextract.TLDExtract(suffix_list_urls=())
_RESERVED_TLDS = {"example", "test", "invalid", "localhost"}
_DOMAIN_RE = re.compile(
    r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z0-9-]{2,63}$"
)
_URL_RE = re.compile(r"^https?://[^\s]+$", re.IGNORECASE)

WARN_THRESHOLD = 40
MIN_BRAND_LABEL_EMBEDDED = 4
MIN_BRAND_LABEL_FUZZY = 5


def normalize_domain(value: str) -> str:
    if not isinstance(value, str):
        raise InvalidDomainError()

    value = value.strip().lower()
    if not value or len(value) > 300:
        raise InvalidDomainError()

    if "@" in value and "://" not in value:
        value = value.rsplit("@", 1)[-1]

    if "://" in value:
        try:
            parsed = urlsplit(value)
            value = parsed.hostname or ""
        except ValueError as exc:
            raise InvalidDomainError() from exc

    value = value.strip().rstrip(".").lower()

    value = value.split("/", 1)[0]
    if value.count(":") == 1:
        value = value.rsplit(":", 1)[0]

    if not value:
        raise InvalidDomainError()

    try:
        value = value.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise InvalidDomainError() from exc

    labels = value.split(".")
    if len(labels) >= 2 and labels[-1] in _RESERVED_TLDS:
        if not all(
            label and len(label) <= 63
            and not label.startswith("-")
            and not label.endswith("-")
            and re.fullmatch(r"[a-z0-9-]+", label)
            for label in labels
        ):
            raise InvalidDomainError()
        return ".".join(labels[-2:])

    if not _DOMAIN_RE.fullmatch(value):
        raise InvalidDomainError()

    extracted = _TLD(value)
    if not extracted.domain or not extracted.suffix:
        raise InvalidDomainError()

    return f"{extracted.domain}.{extracted.suffix}"


def require_domain(value: str) -> str:
    return normalize_domain(value)

def _clean_host(value: str) -> str:
    value = value.strip().lower()
    if "@" in value and "://" not in value:
        value = value.rsplit("@", 1)[-1]
    if "://" in value:
        value = urlsplit(value).hostname or ""
    value = value.split("/", 1)[0].rstrip(".")
    if value.count(":") == 1:
        value = value.rsplit(":", 1)[0]
    try:
        return value.encode("idna").decode("ascii")
    except UnicodeError:
        return ""


def require_url(value: str) -> str:
    if not isinstance(value, str):
        raise InvalidUrlError()

    value = value.strip()
    if not value or len(value) > 2000 or not _URL_RE.fullmatch(value):
        raise InvalidUrlError()

    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or not parsed.hostname
        ):
            raise InvalidUrlError()

        _ = parsed.port
    except ValueError as exc:
        raise InvalidUrlError() from exc

    return value


def _status(risk_score: int) -> str:
    return "warning" if risk_score >= WARN_THRESHOLD else "success"


def _levenshtein(left: str, right: str) -> int:
    if len(left) < len(right):
        left, right = right, left

    previous = list(range(len(right) + 1))

    for i, char_left in enumerate(left, start=1):
        current = [i]
        for j, char_right in enumerate(right, start=1):
            insertion = current[j - 1] + 1
            deletion = previous[j] + 1
            substitution = previous[j - 1] + (
                char_left != char_right
            )
            current.append(min(insertion, deletion, substitution))
        previous = current

    return previous[-1]


def _to_unicode(domain: str) -> str:
    try:
        return domain.encode("ascii").decode("idna")
    except UnicodeError:
        return domain


_CONFUSABLES = str.maketrans({ #similar looking digits and cyrillic letters
    "0": "o",
    "1": "l",
    "3": "e",
    "4": "a",
    "5": "s",
    "7": "t",
    "а": "a",
    "е": "e",
    "і": "i",
    "о": "o",
    "р": "p",
    "с": "c",
    "х": "x",
})


def _brand_labels() -> list[tuple[str, str]]:
    result = []
    for brand in sorted(KNOWN_BRANDS):
        try:
            normalized = normalize_domain(brand)
        except InvalidDomainError:
            continue

        extracted = _TLD(normalized)
        if extracted.domain:
            result.append((normalized, extracted.domain))
    return result


@tool_boundary("check_lookalike_domain", offline=True)
def check_lookalike_domain(domain: str) -> ToolResult:
    raw = domain
    domain = require_domain(domain)
    labels = domain.split(".")
    host = _clean_host(raw)
    subdomains = host[: -len(domain)].rstrip(".") if host.endswith(domain) else ""
    subdomain_forms = {subdomains, _to_unicode(subdomains).translate(_CONFUSABLES)} if subdomains else set()

    if domain in KNOWN_BRANDS:
        return ToolResult(
            tool_name="check_lookalike_domain",
            status="success",
            finding="The domain exactly matches a configured known domain.",
            risk_score=0,
            evidence=[f"Exact known-domain match: {domain}"],
            details={"domain": domain, "match_type": "exact"},
            is_offline=True,
        )

    if labels[-1] in _RESERVED_TLDS:  #fictional test domains
        registrable_label = labels[-2]
    else:
        registrable_label = _TLD(domain).domain or labels[0]
    registrable_label = _to_unicode(registrable_label)
    normalized_label = registrable_label.translate(_CONFUSABLES)

    best_brand = None
    best_distance = None
    risk_score = 0
    match_type = "no_close_match"

    for brand, brand_label in _brand_labels():
        candidate_risk = 0
        candidate_type = "no_close_match"

        if normalized_label != registrable_label and normalized_label == brand_label:
            candidate_risk = 90
            candidate_type = "confusable_character"
        elif registrable_label == brand_label:
            candidate_risk = 60
            candidate_type = "different_domain_suffix"
        else:
            for label in {registrable_label, normalized_label}:
                if (len(brand_label) >= MIN_BRAND_LABEL_EMBEDDED
                        and brand_label in label and 70 > candidate_risk):
                    candidate_risk = 60
                    candidate_type = "brand_embedded"
                if (len(brand_label) >= MIN_BRAND_LABEL_FUZZY
                        and len(label) >= MIN_BRAND_LABEL_FUZZY):
                    distance = _levenshtein(label, brand_label)
                    if distance == 1 and 85 > candidate_risk:
                        candidate_risk = 85
                        candidate_type = "edit_distance_1"
                    elif distance == 2 and 65 > candidate_risk:
                        candidate_risk = 65
                        candidate_type = "edit_distance_2"

        if subdomain_forms and candidate_risk < 80 and len(brand_label) >= 3:
            for form in subdomain_forms:
                tokens = set(re.split(r"[.\-]", form))
                if brand_label in tokens or brand in form:
                    candidate_risk = 80
                    candidate_type = "brand_in_subdomain"
                    break

        if candidate_risk > risk_score:
            risk_score = candidate_risk
            best_brand = brand
            best_distance = _levenshtein(registrable_label, brand_label)
            match_type = candidate_type

    if best_brand:
        finding = (
            f"The domain resembles the known domain {best_brand}. "
            "This is a similarity signal, not proof of phishing."
        )
        evidence = [
            f"Submitted domain: {host if match_type == 'brand_in_subdomain' else domain}",
            f"Similar known domain: {best_brand}",
            f"Similarity rule: {match_type}",
        ]
    else:
        finding = "No close match to the configured known domains was found."
        evidence = [f"Checked domain: {domain}"]

    return ToolResult(
        tool_name="check_lookalike_domain",
        status=_status(risk_score),
        finding=finding,
        risk_score=risk_score,
        evidence=evidence,
        details={
            "domain": domain,
            "host": host,
            "matched_brand": best_brand,
            "match_type": match_type,
            "edit_distance": best_distance,
        },
        is_offline=True,
    )


@cached_online("check_domain_age")
def _lookup_domain_age(domain: str) -> ToolResult:
    try:
        response = httpx.get(
            f"https://rdap.org/domain/{domain}",
            timeout=config.http_timeout,
            follow_redirects=True,
            headers={"Accept": "application/rdap+json"},
        )
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        raise ServiceTimeoutError() from exc
    except httpx.HTTPError as exc:
        raise ServiceResponseError() from exc

    if response.status_code == 429:
        raise RateLimitError()

    if response.status_code == 404:
        return ToolResult(
            tool_name="check_domain_age",
            status=_status(25),
            finding="No domain registration record was found.",
            risk_score=25,
            evidence=[f"RDAP returned HTTP 404 for {domain}"],
            details={"domain": domain, "registered": False},
            is_offline=False,
        )

    if response.status_code >= 400:
        raise ServiceResponseError(
            f"RDAP returned HTTP {response.status_code}"
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise ServiceResponseError(
            "RDAP returned invalid JSON"
        ) from exc

    registration_date = None
    for event in payload.get("events", []):
        if event.get("eventAction") in {
            "registration",
            "registered",
            "creation",
        }:
            registration_date = event.get("eventDate")
            if registration_date:
                break

    if not registration_date:
        raise ServiceResponseError(
            "No registration date was present in the RDAP response"
        )

    try:
        registered_at = datetime.fromisoformat(
            registration_date.replace("Z", "+00:00")
        )
        if registered_at.tzinfo is None:
            registered_at = registered_at.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError) as exc:
        raise ServiceResponseError(
            "RDAP returned an invalid registration date"
        ) from exc

    age_days = max(
        0,
        (datetime.now(timezone.utc) - registered_at).days,
    )

    if age_days < 7:
        risk_score = 90
    elif age_days < 30:
        risk_score = 75
    elif age_days < 90:
        risk_score = 45
    elif age_days < 365:
        risk_score = 10
    else:
        risk_score = 0

    return ToolResult(
        tool_name="check_domain_age",
        status=_status(risk_score),
        finding=f"The domain was registered approximately {age_days} days ago.",
        risk_score=risk_score,
        evidence=[
            f"Registration date: {registered_at.date().isoformat()}",
            f"Domain age: {age_days} days",
        ],
        details={
            "domain": domain,
            "age_days": age_days,
            "registered": True,
        },
        is_offline=False,
    )


@tool_boundary("check_domain_age", offline=False)
def check_domain_age(domain: str) -> ToolResult:
    return _lookup_domain_age(require_domain(domain))


@cached_online("check_url_reputation")
def _lookup_reputation(url: str) -> ToolResult:
    api_key = config.safe_browsing_api_key.strip()
    if not api_key:
        raise MissingApiKeyError()

    endpoint = (
        "https://safebrowsing.googleapis.com/v4/threatMatches:find"
    )
    payload = {
        "client": {
            "clientId": "spotthephish",
            "clientVersion": "1.0",
        },
        "threatInfo": {
            "threatTypes": [
                "MALWARE",
                "SOCIAL_ENGINEERING",
                "UNWANTED_SOFTWARE",
            ],
            "platformTypes": ["ANY_PLATFORM"],
            "threatEntryTypes": ["URL"],
            "threatEntries": [{"url": url}],
        },
    }

    try:
        response = httpx.post(
            endpoint,
            headers={"x-goog-api-key": api_key},
            json=payload,
            timeout=config.http_timeout,
        )
    except httpx.TimeoutException as exc:
        raise ServiceTimeoutError() from exc
    except httpx.HTTPError as exc:
        raise ServiceTimeoutError() from exc

    if response.status_code == 429:
        raise RateLimitError()

    if response.status_code >= 400:
        raise ServiceResponseError(
            f"Safe Browsing returned HTTP {response.status_code}"
        )

    try:
        data = response.json()
    except ValueError as exc:
        raise ServiceResponseError(
            "Safe Browsing returned invalid JSON"
        ) from exc

    matches = data.get("matches", [])

    if matches:
        threat_types = sorted({
            item.get("threatType", "UNKNOWN")
            for item in matches
        })
        return ToolResult(
            tool_name="check_url_reputation",
            status="warning",
            finding="Google Safe Browsing reported a threat for this URL.",
            risk_score=95,
            evidence=[
                f"Threat type: {threat_type}"
                for threat_type in threat_types
            ],
            details={
                "url": url,
                "listed": True,
                "threat_types": threat_types,
            },
            is_offline=False,
        )

    return ToolResult(
        tool_name="check_url_reputation",
        status="success",
        finding=(
            "The URL was not listed by Google Safe Browsing. "
            "This does not guarantee that the URL is safe."
        ),
        risk_score=0,
        evidence=["No matching threat was returned by Safe Browsing."],
        details={"url": url, "listed": False},
        is_offline=False,
    )


@tool_boundary("check_url_reputation", offline=False)
def check_url_reputation(url: str) -> ToolResult:
    return _lookup_reputation(require_url(url))


@tool_boundary("lookup_case_history", offline=False)
def lookup_case_history(
    sender_domain: str = "",
    link_domains: list[str] | None = None,
    subject: str = "",
) -> ToolResult:
    if not isinstance(sender_domain, str):
        raise InvalidToolInputError("sender_domain must be a string.")
    if not isinstance(subject, str):
        raise InvalidToolInputError("subject must be a string.")
    if link_domains is not None and not isinstance(link_domains, list):
        raise InvalidToolInputError("link_domains must be a list of domains.")

    normalized_sender = ""
    if sender_domain.strip():
        try:
            normalized_sender = require_domain(sender_domain)
        except InvalidDomainError:
            normalized_sender = "" #sender unreadable

    normalized_links: list[str] = []
    for value in link_domains or []:
        if not isinstance(value, str):
            raise InvalidToolInputError(
                "Every link domain must be a string."
            )
        if value.strip():
            try:
                normalized_links.append(require_domain(value))
            except InvalidDomainError:
                continue

    normalized_sender = memory.matchable_sender(normalized_sender)

    normalized_links = sorted({
        domain
        for domain in normalized_links
        if domain not in KNOWN_BRANDS
    })

    rows = memory.find_similar(
        normalized_sender,
        normalized_links,
        memory.subject_fingerprint(subject),
    )

    if not rows:
        return ToolResult(
            tool_name="lookup_case_history",
            status="success",
            finding="No similar previous cases were found.",
            risk_score=0,
            evidence=[],
            details={"matches": 0},
            is_offline=False,
        )

    phishing_rows = [
        row for row in rows
        if str(row["verdict"]).lower() == "phishing"
    ]
    suspicious_rows = [
        row for row in rows
        if str(row["verdict"]).lower() == "suspicious"
    ]

    same_sender_phishing = any(
        row["same_sender"]
        and str(row["verdict"]).lower() == "phishing"
        for row in rows
    )
    same_subject_phishing = any(
        row["same_subject"]
        and str(row["verdict"]).lower() == "phishing"
        for row in rows
    )

    if same_sender_phishing or same_subject_phishing:
        risk_score = 70
    elif phishing_rows:
        risk_score = 55
    elif suspicious_rows:
        risk_score = 35
    else:
        risk_score = 0

    evidence = [
        f"Previous matching cases: {len(rows)}",
        f"Previous phishing cases: {len(phishing_rows)}",
        f"Previous suspicious cases: {len(suspicious_rows)}",
    ]

    if same_sender_phishing:
        evidence.append(
            "A previous phishing case used the same sender domain."
        )
    if same_subject_phishing:
        evidence.append(
            "A previous phishing case had the same subject fingerprint."
        )

    return ToolResult(
        tool_name="lookup_case_history",
        status=_status(risk_score),
        finding=(
            f"Found {len(rows)} similar previous case(s); "
            "history is supporting evidence, not proof."
        ),
        risk_score=risk_score,
        evidence=evidence,
        details={
            "matches": len(rows),
            "phishing_matches": len(phishing_rows),
            "suspicious_matches": len(suspicious_rows),
            "same_sender_phishing": same_sender_phishing,
            "same_subject_phishing": same_subject_phishing,
        },
        is_offline=False,
    )