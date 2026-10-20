import os

import pytest

from src.capabilities import domain_tools as t
from src.capabilities import eval_cache, memory
from src.config import config
from src.exceptions import DatabaseUnavailableError
from src.schemas.responses import LinkInfo, ParsedEmail, EmailAddress, ToolResult, Verdict


@pytest.fixture
def db(monkeypatch):
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")
    monkeypatch.setattr(config, "database_url", url)
    monkeypatch.setattr(config, "case_history_write", True)
    try:
        memory.init_db()
    except DatabaseUnavailableError:
        pytest.skip("test database not reachable")
    memory.reset_history()
    with memory.db() as c:
        c.execute("TRUNCATE eval_cache")
    return url


def _email(subject="Urgent: verify invoice 4711", sender="evil-pay.com", links=("evil-pay.com",)):
    return ParsedEmail(subject=subject, sender=EmailAddress(address=f"a@{sender}", domain=sender),
                       body_plain="SECRET BODY TEXT",
                       links=[LinkInfo(url=f"http://{d}/x", domain=d) for d in links])


def _verdict(v="phishing", fallback=False):
    return Verdict(verdict=v, risk_score=90, explanation="x", is_fallback=fallback)


def test_campaign_match_by_subject_variant(db):
    assert memory.save_verdict(_email(), _verdict())
    r = t.lookup_case_history("other.org", ["other.org"], "RE: urgent: verify invoice 9999")
    assert r.status == "warning" and r.risk_score >= 70
    assert r.details["same_subject_phishing"] is True and r.details["matches"] == 1


def test_no_match_and_known_brand_links_ignored(db):
    memory.save_verdict(_email(links=("google.com",)), _verdict("likely safe"))
    r = t.lookup_case_history("unrelated.org", ["google.com"], "Hello")
    assert r.risk_score == 0 and "No similar" in r.finding


def test_fallback_verdicts_not_stored(db):
    assert memory.save_verdict(_email(), _verdict(fallback=True)) is False


def test_write_can_be_disabled(db, monkeypatch):
    monkeypatch.setattr(config, "case_history_write", False)
    assert memory.save_verdict(_email(), _verdict()) is False


def test_privacy_no_email_body_stored(db):
    memory.save_verdict(_email(), _verdict())
    with memory.db() as c:
        cols = {r["column_name"] for r in c.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name='case_history'")}
        dump = str(c.execute("SELECT * FROM case_history").fetchall())
    assert not cols & {"body", "body_plain", "text", "content", "raw_text"}
    assert "SECRET BODY TEXT" not in dump


def test_eval_cache_record_then_replay(db, monkeypatch):
    calls = []

    def fake(domain):
        calls.append(domain)
        return ToolResult(tool_name="check_domain_age", finding="registered 3 days ago",
                          risk_score=90, status="warning", is_offline=False)

    monkeypatch.setattr(t, "_lookup_domain_age", eval_cache.cached_online("check_domain_age")(fake))
    monkeypatch.setattr(config, "eval_cache_mode", "record")
    assert t.check_domain_age("Evil-Pay.com").risk_score == 90
    monkeypatch.setattr(config, "eval_cache_mode", "replay")
    assert t.check_domain_age("evil-pay.com").risk_score == 90 and len(calls) == 1
    assert t.check_domain_age("never-seen.com").status == "unavailable"  # cache miss


def test_save_verdict_raises_when_db_down(monkeypatch):
    monkeypatch.setattr(config, "database_url", "postgresql://x:x@127.0.0.1:1/x")
    monkeypatch.setattr(config, "case_history_write", True)
    with pytest.raises(DatabaseUnavailableError):
        memory.save_verdict(_email(), _verdict())


def test_eval_cache_seed_for_fictional_domain(db, monkeypatch):
    eval_cache.seed("check_domain_age", {"domain": "secure-login-hamk.example"},
                    ToolResult(tool_name="check_domain_age", status="warning", risk_score=90, finding="registered 2 days ago", is_offline=False))
    monkeypatch.setattr(config, "eval_cache_mode", "replay")
    assert t.check_domain_age("secure-login-hamk.example").risk_score == 90

def test_history_ignores_ip_links_and_bad_sender(db):
    memory.save_verdict(_email(), _verdict())
    r = t.lookup_case_history("not a domain", ["192.168.10.5", "evil-pay.com"], "other subject")
    assert r.status in ("success", "warning") and r.details["matches"] == 1

def test_campaign_match_with_subdomains(db):
    memory.save_verdict(
        _email(subject="Parcel 8841 not delivered", sender="mail.parcel-notice.example", links=("login.parcel-notice.example",)),
        _verdict())
    r = t.lookup_case_history("parcel-notice.example", ["parcel-notice.example"], "Something else")
    assert r.status == "warning" and r.details["matches"] == 1