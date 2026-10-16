from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Optional

import httpx
import ollama

from src.exceptions import (
    ModelNotFoundError,
    ModelTimeoutError,
    ModelUnavailableError,
)
from src.schemas.agent import ChatMessage

logger = logging.getLogger(__name__)


class ModelClient(ABC):
    model_name: str

    @abstractmethod
    def chat(self, messages: list[ChatMessage], response_schema: Optional[dict] = None) -> str:
        """Return the model's reply."""


class OllamaModelClient(ModelClient):

    def __init__(
        self,
        base_url: str,
        model_name: str,
        timeout_seconds: float,
        temperature: float = 0.0,
        context_tokens: int = 8192,
        think: bool = False,
        ollama_client: Optional[Any] = None,
    ):
        # ollama_client: only for tests. By default a real ollama.Client is created.
        self.base_url = base_url
        self.model_name = model_name
        self.timeout_seconds = timeout_seconds
        self.temperature = temperature
        self.context_tokens = context_tokens
        self.think = think
        self._send_think_setting = True
        if ollama_client is None:
            ollama_client = ollama.Client(host=base_url, timeout=timeout_seconds)
        self._client = ollama_client

    def chat(self, messages: list[ChatMessage], response_schema: Optional[dict] = None) -> str:
        payload = []
        for message in messages:
            payload.append({"role": message.role, "content": message.content})

        try:
            response = self._send(payload, response_schema)
        except ollama.ResponseError as error:
            if error.status_code == 404:
                raise ModelNotFoundError(
                    f"Model '{self.model_name}' is not installed in Ollama at {self.base_url}."
                ) from error
            raise ModelUnavailableError(
                f"Ollama returned HTTP {error.status_code}: {error.error}"
            ) from error
        except httpx.TimeoutException as error:
            raise ModelTimeoutError(
                f"Ollama did not answer within {self.timeout_seconds} seconds."
            ) from error
        except (ConnectionError, httpx.TransportError) as error:
            raise ModelUnavailableError(
                f"Could not connect to Ollama at {self.base_url}."
            ) from error

        return _reply_text(response)

    def _send(self, payload: list[dict], response_schema: Optional[dict]) -> Any:
        options = {"temperature": self.temperature, "num_ctx": self.context_tokens}

        if self._send_think_setting:
            try:
                return self._client.chat(
                    model=self.model_name,
                    messages=payload,
                    format=response_schema,
                    think=self.think,
                    options=options,
                )
            except ollama.ResponseError as error:
                if error.status_code != 400 or "think" not in str(error.error).lower():
                    raise
                logger.info("Model %s has no thinking mode. Sending requests without it.", self.model_name)
                self._send_think_setting = False

        return self._client.chat(
            model=self.model_name,
            messages=payload,
            format=response_schema,
            options=options,
        )


def _reply_text(response: Any) -> str:
    if isinstance(response, dict):
        message = response.get("message") or {}
        content = message.get("content") or ""
    else:
        message = getattr(response, "message", None)
        content = getattr(message, "content", None) or ""
    return str(content)
