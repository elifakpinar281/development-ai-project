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

log = logging.getLogger(__name__)
mcp = FastMCP("spotthephish-domain-checks")


try:
    memory.init_db()
    log.info("Database schema initialized.")
except Exception as exc:
    log.warning("Database initialization failed: %s", type(exc).__name__)


@mcp.tool()
def check_lookalike_domain(domain: str) -> str:
    return domain_tools.check_lookalike_domain(domain).model_dump_json()


@mcp.tool()
def check_domain_age(domain: str) -> str:
    return domain_tools.check_domain_age(domain).model_dump_json()


@mcp.tool()
def check_url_reputation(url: str) -> str:
    return domain_tools.check_url_reputation(url).model_dump_json()


@mcp.tool()
def lookup_case_history(
    sender_domain: str = "",
    link_domains: list[str] | None = None,
    subject: str = "",
) -> str:
    return domain_tools.lookup_case_history(
        sender_domain=sender_domain,
        link_domains=link_domains,
        subject=subject,
    ).model_dump_json()


if __name__ == "__main__":
    mcp.run(transport="stdio")