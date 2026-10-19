import httpx
import pytest

from src.capabilities import domain_tools as t
from src.config import config
from src.exceptions import (
    InvalidDomainError,
    MissingApiKeyError,
    RateLimitError,
    ServiceResponseError,
    ServiceTimeoutError,
)


@pytest.fixture(autouse=True)
def no_cache(monkeypatch):
    monkeypatch.setattr(config, "eval_cache_mode", "off")


@pytest.mark.parametrize("domain,brand", [
    ("paypa1.com", "paypal.com"),
    ("rnicrosoft.com", "microsoft.com"),
    ("paypal-secure-login.com", "paypal.com"),
    ("paypal.net", "paypal.com"),
    ("hamk.com", "hamk.fi"),
    ("xn--pypal-4ve.com", "paypal.com"),
])
def test_lookalike_detected(domain, brand):
    r = t.check_lookalike_domain(domain)
    assert r.status == "warning" and r.details["matched_brand"] == brand and r.is_offline


@pytest.mark.parametrize("domain", [
    "paypal.com", "https://login.hamk.fi/x", "example-shop.org", "ebay.com",
    "shop.fi", "stop.com", "ab.com", "tool.org", "b2b-shop.com",
])
def test_lookalike_clean(domain):
    assert t.check_lookalike_domain(domain).status == "success"


@pytest.mark.parametrize("bad", ["", "not a domain", "localhost", "http://", "a" * 400 + ".com"])
def test_lookalike_invalid_input(bad):
    assert t.check_lookalike_domain(bad).status == "error"


@pytest.mark.parametrize("domain,brand", [
    ("paypa1.example", "paypal.com"),
    ("hamk-student-login.example", "hamk.fi"),
    ("paypa1-secure.com", "paypal.com"),
])
def test_lookalike_evaluation_and_combined_cases(domain, brand):
    r = t.check_lookalike_domain(domain)
    assert r.status == "warning" and r.details["matched_brand"] == brand


def test_reserved_test_tld_accepted():
    assert t.normalize_domain("secure-login-hamk.example") == "secure-login-hamk.example"


def _resp(status, json=None):
    return httpx.Response(status, json=json, request=httpx.Request("GET", "https://rdap.org/x"))


def test_domain_age_new_domain(monkeypatch):
    from datetime import datetime, timedelta, timezone
    reg = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _resp(200, {"events": [
        {"eventAction": "registration", "eventDate": reg}]}))
    r = t.check_domain_age("evil-example.com")
    assert r.status == "warning" and r.risk_score >= 75 and r.details["age_days"] == 3


def test_domain_age_old_domain(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _resp(200, {"events": [
        {"eventAction": "registration", "eventDate": "2001-01-01T00:00:00Z"}]}))
    assert t.check_domain_age("google.com").risk_score == 0


def test_domain_age_failures_are_unavailable(monkeypatch):
    def timeout(*a, **k):
        raise httpx.TimeoutException("t")
    monkeypatch.setattr(httpx, "get", timeout)
    assert t.check_domain_age("example.com").status == "unavailable"
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _resp(429))
    assert t.check_domain_age("example.com").status == "unavailable"
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _resp(200, {"events": []}))
    assert t.check_domain_age("example.com").status == "unavailable"


def test_domain_age_invalid_domain_makes_no_request(monkeypatch):
    def fail(*a, **k):
        raise AssertionError("no request expected")
    monkeypatch.setattr(httpx, "get", fail)
    assert t.check_domain_age("../etc/passwd").status == "error"


def test_reputation_without_key(monkeypatch):
    monkeypatch.setattr(config, "safe_browsing_api_key", "")
    assert t.check_url_reputation("http://x.example/a").status == "unavailable"


def test_reputation_match_and_clean(monkeypatch):
    monkeypatch.setattr(config, "safe_browsing_api_key", "dummy")
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _resp(200, {"matches": [{"threatType": "SOCIAL_ENGINEERING"}]}))
    r = t.check_url_reputation("http://evil.example/login")
    assert r.status == "warning" and r.risk_score >= 90
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _resp(200, {}))
    assert t.check_url_reputation("http://fine.example/").risk_score == 0


def test_reputation_error_does_not_leak_key(monkeypatch):
    monkeypatch.setattr(config, "safe_browsing_api_key", "SECRET123")
    def boom(*a, **k):
        raise RuntimeError("https://x?key=SECRET123")
    monkeypatch.setattr(httpx, "post", boom)
    r = t.check_url_reputation("http://x.example/")
    assert r.status == "unavailable" and "SECRET123" not in r.model_dump_json()


def test_reputation_rejects_non_http():
    assert t.check_url_reputation("javascript:alert(1)").status == "error"


def test_history_db_down(monkeypatch):
    monkeypatch.setattr(config, "database_url", "postgresql://x:x@127.0.0.1:1/x")
    r = t.lookup_case_history("evil.com", ["evil.com"], "Urgent")
    assert r.status == "unavailable"


def test_lookup_raises_specific_exceptions(monkeypatch):
    def timeout(*a, **k):
        raise httpx.TimeoutException("t")
    monkeypatch.setattr(httpx, "get", timeout)
    with pytest.raises(ServiceTimeoutError):
        t._lookup_domain_age("example.com")
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _resp(429))
    with pytest.raises(RateLimitError):
        t._lookup_domain_age("example.com")
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _resp(500))
    with pytest.raises(ServiceResponseError):
        t._lookup_domain_age("example.com")
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _resp(200, {"events": []}))
    with pytest.raises(ServiceResponseError):
        t._lookup_domain_age("example.com")


def test_missing_key_raises(monkeypatch):
    monkeypatch.setattr(config, "safe_browsing_api_key", "")
    with pytest.raises(MissingApiKeyError):
        t._lookup_reputation("http://x.example/")


def test_require_domain_raises():
    with pytest.raises(InvalidDomainError):
        t.require_domain("not a domain")


def test_unexpected_bug_is_contained(monkeypatch):
    monkeypatch.setattr(t, "_lookup_domain_age", lambda d: 1 / 0)
    r = t.check_domain_age("example.com")
    assert r.status == "unavailable" and "ZeroDivisionError" in r.finding

def test_domain_age_404_is_only_a_weak_signal(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _resp(404))
    r = t.check_domain_age("no-record.example")
    assert r.status == "success" and r.risk_score == 25


def test_http_errors_are_not_reported_as_timeouts(monkeypatch):
    def too_many(*a, **k):
        raise httpx.TooManyRedirects("x", request=httpx.Request("GET", "https://rdap.org/x"))
    monkeypatch.setattr(httpx, "get", too_many)
    r = t.check_domain_age("loop.example")
    assert r.status == "unavailable" and "timed out" not in r.finding