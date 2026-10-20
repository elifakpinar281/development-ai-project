from __future__ import annotations

import functools
import inspect
import json
import logging
from typing import Any, Callable, Optional

from psycopg.types.json import Jsonb

from src.capabilities import memory
from src.config import config
from src.exceptions import DatabaseUnavailableError, EvalCacheMissError
from src.schemas.responses import ToolResult


log = logging.getLogger(__name__)

VALID_MODES = {"off", "record", "replay"}


def _key(sig: inspect.Signature, args: tuple, kwargs: dict,) -> str:
    bound = sig.bind(*args, **kwargs)
    bound.apply_defaults()

    return json.dumps(
        dict(bound.arguments),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _read(tool_name: str, key: str,) -> Optional[ToolResult]:
    memory.ensure_schema()

    with memory.db() as conn:
        row = conn.execute(
            """
            SELECT result
            FROM eval_cache
            WHERE tool_name = %s AND cache_key = %s
            """,
            (tool_name, key),
        ).fetchone()

    if row is None:
        return None

    return ToolResult.model_validate(row["result"])


def _write(tool_name: str, key: str, result: ToolResult,) -> None:
    memory.ensure_schema()

    with memory.db() as conn:
        conn.execute(
            """
            INSERT INTO eval_cache (tool_name, cache_key, result)
            VALUES (%s, %s, %s)
            ON CONFLICT (tool_name, cache_key)
            DO UPDATE SET result = EXCLUDED.result
            """,
            (
                tool_name,
                key,
                Jsonb(result.model_dump(mode="json")),
            ),
        )


def seed(tool_name: str, arguments: dict[str, Any], result: ToolResult,) -> None:
    key = json.dumps(
        arguments,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    _write(tool_name, key, result)


def cached_online(tool_name: str) -> Callable:

    def deco(
        fn: Callable[..., ToolResult],
    ) -> Callable[..., ToolResult]:
        sig = inspect.signature(fn)

        @functools.wraps(fn)
        def wrapper(*args, **kwargs) -> ToolResult:
            mode = config.eval_cache_mode

            if mode not in VALID_MODES:
                raise ValueError(
                    "EVAL_CACHE_MODE must be one of: off, record, replay."
                )

            if mode == "off":
                return fn(*args, **kwargs)

            key = _key(sig, args, kwargs)

            if mode == "replay":
                cached = _read(tool_name, key)

                if cached is None:
                    raise EvalCacheMissError(
                        f"No cached result for {tool_name} and this input."
                    )

                return cached

            result = fn(*args, **kwargs)

            if result.status not in {"success", "warning"}:
                return result

            try:
                _write(tool_name, key, result)
            except DatabaseUnavailableError:
                log.warning(
                    "Could not write evaluation cache for %s",
                    tool_name,
                )

            return result

        return wrapper

    return deco
