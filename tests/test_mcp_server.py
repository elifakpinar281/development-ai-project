import asyncio
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from src.schemas.responses import ToolResult

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_server_lists_tools_and_answers():
    async def run():
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "src.mcp_servers.domain_server"],
            env=dict(os.environ),
            cwd=str(PROJECT_ROOT),
        )
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                names = {t.name for t in (await s.list_tools()).tools}
                res = await s.call_tool("check_lookalike_domain", {"domain": "paypa1.com"})
                return names, res

    names, res = asyncio.run(run())
    assert names == {"check_lookalike_domain", "check_domain_age",
                     "check_url_reputation", "lookup_case_history"}
    assert "save_verdict" not in names
    assert ToolResult.model_validate_json(res.content[0].text).status == "warning"


def test_sync_client():
    from src.capabilities.mcp_client import DomainToolsClient
    with DomainToolsClient() as c:
        assert "check_lookalike_domain" in c.tool_names
        assert c.tool_definitions()[0]["function"]["parameters"]["type"] == "object"
        assert c.call("check_lookalike_domain", {"domain": "paypa1.com"}).status == "warning"
        assert c.call("delete_everything", {}).status == "error"