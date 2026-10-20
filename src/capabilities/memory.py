from __future__ import annotations

import hashlib
import re
from contextlib import contextmanager
from typing import Iterator

import psycopg
from psycopg.rows import dict_row

from src.config import config
from src.exceptions import DatabaseUnavailableError, InvalidDomainError
from src.schemas.responses import ParsedEmail, Verdict

from src.capabilities.known_domains import KNOWN_BRANDS



SCHEMA = """
CREATE TABLE IF NOT EXISTS case_history (
    id SERIAL PRIMARY KEY,
    sender_domain TEXT NOT NULL DEFAULT '',
    link_domains TEXT[] NOT NULL DEFAULT '{}',
    subject_hash TEXT NOT NULL DEFAULT '',
    verdict TEXT NOT NULL,
    risk_score REAL NOT NULL,
    judged_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_case_sender
    ON case_history (sender_domain);

CREATE INDEX IF NOT EXISTS idx_case_subject
    ON case_history (subject_hash);

CREATE INDEX IF NOT EXISTS idx_case_links
    ON case_history USING GIN (link_domains);

CREATE TABLE IF NOT EXISTS eval_cache (
    tool_name TEXT NOT NULL,
    cache_key TEXT NOT NULL,
    result JSONB NOT NULL,
    PRIMARY KEY (tool_name, cache_key)
);
"""


_schema_ready: set[str] = set()


@contextmanager
def db() -> Iterator[psycopg.Connection]:
    try:
        with psycopg.connect(
            config.database_url,
            row_factory=dict_row,
            connect_timeout=3,
        ) as conn:
            yield conn
    except psycopg.Error as err:
        raise DatabaseUnavailableError() from err


def init_db() -> None:
    with db() as conn:
        for statement in SCHEMA.split(";"):
            statement = statement.strip()
            if statement:
                conn.execute(statement)

    _schema_ready.add(config.database_url)


def ensure_schema() -> None:
    if config.database_url not in _schema_ready:
        init_db()


def subject_fingerprint(subject: str) -> str:
    if not isinstance(subject, str):
        return ""

    normalized = subject.strip().lower()

    normalized = re.sub(
        r"^(?:(?:re|fw|fwd|aw|wg)\s*:\s*)+",
        "",
        normalized,
    )
    normalized = re.sub(r"\d+", "#", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()

    if not normalized:
        return ""

    return hashlib.sha256(
        normalized.encode("utf-8")
    ).hexdigest()


def _registrable(value: str) -> str:
    from src.capabilities.domain_tools import normalize_domain #imported here because domain_tools imports this module

    try:
        return normalize_domain(value)
    except InvalidDomainError:
        return ""


def fingerprint(parsed: ParsedEmail) -> dict:
    links = {_registrable(link.domain or link.url) for link in parsed.links}
    links = sorted(domain for domain in links if domain and domain not in KNOWN_BRANDS)

    return {
        "sender_domain": _registrable(parsed.sender.domain or parsed.sender.address),
        "link_domains": links,
        "subject_hash": subject_fingerprint(parsed.subject or ""),
    }


def save_verdict(parsed: ParsedEmail, verdict: Verdict) -> bool:
    if not config.case_history_write or verdict.is_fallback:
        return False

    metadata = fingerprint(parsed)
    ensure_schema()

    with db() as conn:
        conn.execute(
            """
            INSERT INTO case_history (
                sender_domain,
                link_domains,
                subject_hash,
                verdict,
                risk_score
            )
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                metadata["sender_domain"],
                metadata["link_domains"],
                metadata["subject_hash"],
                verdict.verdict,
                verdict.risk_score,
            ),
        )

    return True


def find_similar(
    sender_domain: str,
    link_domains: list[str],
    subject_hash: str,
    limit: int = 10,
) -> list[dict]:
    if limit < 1:
        return []

    sender = (sender_domain or "").strip().lower()
    links = sorted({
        domain.strip().lower()
        for domain in link_domains
        if isinstance(domain, str) and domain.strip()
    })
    subject = subject_hash or ""

    ensure_schema()

    with db() as conn:
        rows = conn.execute(
            """
            SELECT
                verdict,
                risk_score,
                judged_at,
                (
                    sender_domain <> ''
                    AND sender_domain = %(sender)s
                ) AS same_sender,
                (
                    cardinality(%(links)s::text[]) > 0
                    AND link_domains && %(links)s::text[]
                ) AS same_link,
                (
                    %(subject)s <> ''
                    AND subject_hash <> ''
                    AND subject_hash = %(subject)s
                ) AS same_subject
            FROM case_history
            WHERE
                (
                    %(sender)s <> ''
                    AND sender_domain = %(sender)s
                )
                OR (
                    cardinality(%(links)s::text[]) > 0
                    AND link_domains && %(links)s::text[]
                )
                OR (
                    %(subject)s <> ''
                    AND subject_hash <> ''
                    AND subject_hash = %(subject)s
                )
            ORDER BY judged_at DESC
            LIMIT %(limit)s
            """,
            {
                "sender": sender,
                "links": links,
                "subject": subject,
                "limit": limit,
            },
        ).fetchall()

    return list(rows)


def reset_history() -> None:
    ensure_schema()

    with db() as conn:
        conn.execute("TRUNCATE TABLE case_history")
