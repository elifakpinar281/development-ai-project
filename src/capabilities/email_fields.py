from __future__ import annotations

from src.schemas.responses import ParsedEmail


def real_subject(email: ParsedEmail) -> str:
    """The email's subject, or "" when the input had no headers.
    For text pasted without headers the parser fills in a placeholder subject. 
    """
    if not email.has_headers:
        return ""
    return email.subject or ""