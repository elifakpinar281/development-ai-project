from __future__ import annotations

import functools
import logging
from typing import Callable

from src.exceptions import ToolError
from src.schemas.responses import ToolResult

log = logging.getLogger(__name__)


def error_to_result(tool_name: str, exc: ToolError, offline: bool = False) -> ToolResult:
    return ToolResult(tool_name=tool_name, status=exc.tool_status, finding=exc.finding(),
                      is_offline=offline)


def tool_boundary(tool_name: str, *, offline: bool) -> Callable:
    def deco(fn: Callable[..., ToolResult]) -> Callable[..., ToolResult]:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs) -> ToolResult:
            try:
                return fn(*args, **kwargs)
            except ToolError as exc:
                cause = type(exc.__cause__).__name__ if exc.__cause__ else "-"
                log.info("%s: %s (cause: %s)", tool_name, type(exc).__name__, cause)
                return error_to_result(tool_name, exc, offline)
            except Exception as exc:
                log.error("%s: unexpected %s", tool_name, type(exc).__name__)
                return ToolResult(tool_name=tool_name, status="unavailable", is_offline=offline, finding=f"Check could not be run: internal error ({type(exc).__name__}).")
        return wrapper
    return deco