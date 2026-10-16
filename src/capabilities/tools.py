from __future__ import annotations

from typing import Any

from src.capabilities.mcp_client import DomainToolsClient
from src.schemas.responses import ToolResult


class MCPToolRegistry:

    def __init__(
        self,
        client: DomainToolsClient | None = None,
    ) -> None:
        self.client = client or DomainToolsClient()
        self._owns_client = client is None

    def get_available_tools(self) -> list[dict[str, Any]]:
        return self.client.tool_definitions()

    def execute_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> ToolResult:
        return self.client.call(tool_name, arguments)

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def __enter__(self) -> "MCPToolRegistry":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()