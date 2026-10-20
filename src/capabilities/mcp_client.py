from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import os
import queue
import sys
import threading
from typing import Any
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pydantic import ValidationError

from src.capabilities.tool_boundary import error_to_result
from src.exceptions import (InvalidToolInputError, McpServerUnavailableError, ServiceResponseError, ToolError, UnknownToolError,)
from src.schemas.responses import ToolResult

log = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[2]

class DomainToolsClient:
    def __init__(
        self,
        call_timeout: float = 20,
        start_timeout: float = 20,
    ) -> None:
        self.call_timeout = call_timeout
        self.start_timeout = start_timeout

        self._ready = threading.Event()
        self._stop = threading.Event()
        self._queue: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._tools: dict[str, dict[str, Any]] = {}
        self._startup_error: BaseException | None = None

        self._thread = threading.Thread(
            target=self._thread_main,
            name="spotthephish-mcp-client",
            daemon=True,
        )
        self._thread.start()

        if not self._ready.wait(timeout=start_timeout):
            self.close()
            raise McpServerUnavailableError(
                "Timed out while starting the MCP server."
            )

        if self._startup_error is not None:
            error = self._startup_error
            self.close()
            raise McpServerUnavailableError(
                f"Could not start MCP server ({type(error).__name__})."
            ) from error

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._serve())
        except BaseException as exc:
            self._startup_error = exc
            log.exception("MCP client thread stopped unexpectedly.")
        finally:
            self._ready.set()

    async def _serve(self) -> None:
        command = os.getenv("MCP_SERVER_COMMAND") or sys.executable
        args_text = os.getenv("MCP_SERVER_ARGS") or "-m src.mcp_servers.domain_server"
        args = args_text.split()

        params = StdioServerParameters(
            command=command,
            args=args,
            env=dict(os.environ),
            cwd=str(PROJECT_ROOT),
        )

        async with stdio_client(params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()

                response = await session.list_tools()
                self._tools = {
                    tool.name: {
                        "name": tool.name,
                        "description": tool.description or "",
                        "inputSchema": tool.inputSchema,
                    }
                    for tool in response.tools
                }

                self._ready.set()

                while not self._stop.is_set():
                    try:
                        item = self._queue.get_nowait()
                    except queue.Empty:
                        await asyncio.sleep(0.05)
                        continue

                    if item is None:
                        break

                    tool_name, arguments, future = item

                    if future.cancelled():
                        continue

                    try:
                        result = await session.call_tool(
                            tool_name,
                            arguments=arguments,
                        )
                        future.set_result(result)
                    except Exception as exc:
                        future.set_exception(exc)

    def tool_definitions(self) -> list[dict[str, Any]]:
        definitions = []

        for name, tool in self._tools.items():
            definitions.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": tool["description"],
                    "parameters": tool["inputSchema"],
                },
            })

        return definitions

    @property
    def tool_names(self) -> set[str]:
        return set(self._tools)

    def call(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> ToolResult:
        try:
            return self._call(tool_name, arguments)
        except ToolError as exc:
            return error_to_result(tool_name, exc, offline=False)

    def _call(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> ToolResult:
        if not isinstance(arguments, dict):
            raise InvalidToolInputError("Tool arguments must be an object.")

        if not self._thread or not self._thread.is_alive():
            raise McpServerUnavailableError()

        if tool_name not in self._tools:
            raise UnknownToolError(f"Unknown MCP tool: {tool_name}")

        future: concurrent.futures.Future = concurrent.futures.Future()
        self._queue.put((tool_name, arguments, future))

        try:
            response = future.result(timeout=self.call_timeout)
        except concurrent.futures.TimeoutError as exc:
            future.cancel()
            raise McpServerUnavailableError(
                f"MCP tool call timed out: {tool_name}"
            ) from exc
        except Exception as exc:
            raise McpServerUnavailableError(
                f"MCP tool call failed: {tool_name}"
            ) from exc

        if getattr(response, "isError", False):
            raise ServiceResponseError(
                f"MCP tool returned an error: {tool_name}"
            )

        content = getattr(response, "content", None) or []
        text_parts = [
            item.text
            for item in content
            if getattr(item, "type", None) == "text"
        ]

        if not text_parts:
            raise ServiceResponseError(
                f"MCP tool returned no text result: {tool_name}"
            )

        try:
            payload = json.loads(text_parts[0])
            result = ToolResult.model_validate(payload)
        except (json.JSONDecodeError, ValidationError) as exc:
            raise ServiceResponseError(
                f"MCP tool returned an invalid result: {tool_name}"
            ) from exc

        return result

    def close(self) -> None:
        self._stop.set()
        self._queue.put(None)

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)

    def __enter__(self) -> "DomainToolsClient":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()