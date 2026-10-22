from __future__ import annotations

import logging
import sys

from mcp.server.fastmcp import FastMCP

from src.capabilities import domain_tools
from src.capabilities import memory

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stderr,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
for noisy in ("httpx", "httpcore"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

log = logging.getLogger(__name__)
mcp = FastMCP("spotthephish-domain-checks")


try:
    memory.init_db()
    log.info("Database schema initialized.")
except Exception as exc:
    log.warning("Database initialization failed: %s", type(exc).__name__)

@mcp.tool()
def check_lookalike_domain(domain: str) -> str:
    """Checks whether a domain imitates a well-known brand or university domain (character swaps such as paypa1, a brand name inside the domain or subdomain, a different domain ending). Works offline. Use it for the sender domain and for every link domain you do not recognise."""
    return domain_tools.check_lookalike_domain(domain).model_dump_json()

@mcp.tool()
def check_domain_age(domain: str) -> str:
    """Looks up when a domain was registered. Very new domains are a strong phishing sign. Use it for the sender domain and for link domains that are not well known."""
    return domain_tools.check_domain_age(domain).model_dump_json()

@mcp.tool()
def check_url_reputation(url: str) -> str:
    """Asks Google Safe Browsing whether a link is a known malicious URL. Pass a full URL from the 'Links found' list. Use it when the email contains links."""
    return domain_tools.check_url_reputation(url).model_dump_json()

@mcp.tool()
def lookup_case_history(sender_domain: str = "", link_domains: list[str] | None = None, subject: str = "") -> str:
    """Looks for earlier cases with the same sender domain, link domains or subject pattern. A match points to a repeated campaign; no match proves nothing."""
    return domain_tools.lookup_case_history(
        sender_domain=sender_domain,
        link_domains=link_domains,
        subject=subject,
    ).model_dump_json()


if __name__ == "__main__":
    mcp.run(transport="stdio")