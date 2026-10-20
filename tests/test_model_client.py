import socket

import httpx
import ollama
import pytest

from src.exceptions import (
    ModelError,
    ModelNotFoundError,
    ModelTimeoutError,
    ModelUnavailableError,
)
from src.models.model_client import OllamaModelClient
from src.schemas.agent import ChatMessage

MESSAGES = [ChatMessage(role="user", content="hello")]


class FakeOllama:
    def __init__(self, reply=None, error=None):
        self.reply = reply
        self.error = error
        self.kwargs = None

    def chat(self, **kwargs):
        self.kwargs = kwargs
        if self.error is not None:
            raise self.error
        return self.reply


def client_with(fake):
    return OllamaModelClient(
        base_url="http://localhost:11434", model_name="qwen3:8b", timeout_seconds=5,
        temperature=0.0, context_tokens=4096, think=False, ollama_client=fake,
    )


def test_returns_reply_and_sends_settings():
    fake = FakeOllama(reply={"message": {"role": "assistant", "content": '{"ok": true}'}})
    schema = {"type": "object"}
    text = client_with(fake).chat(MESSAGES, response_schema=schema)

    assert text == '{"ok": true}'
    assert fake.kwargs["model"] == "qwen3:8b"
    assert fake.kwargs["messages"] == [{"role": "user", "content": "hello"}]
    assert fake.kwargs["format"] == schema
    assert fake.kwargs["think"] is False
    assert fake.kwargs["options"] == {"temperature": 0.0, "num_ctx": 4096}


def test_reads_ollama_chat_response_objects():
    reply = ollama.ChatResponse(message=ollama.Message(role="assistant", content="hi"))
    assert client_with(FakeOllama(reply=reply)).chat(MESSAGES) == "hi"


def test_empty_reply_becomes_empty_string():
    assert client_with(FakeOllama(reply={"message": {"content": None}})).chat(MESSAGES) == ""


@pytest.mark.parametrize("error,expected", [
    (ollama.ResponseError("model 'qwen3:8b' not found", 404), ModelNotFoundError),
    (ollama.ResponseError("out of memory", 500), ModelUnavailableError),
    (ConnectionError("Failed to connect to Ollama"), ModelUnavailableError),
    (httpx.ReadTimeout("timed out"), ModelTimeoutError),
    (httpx.RemoteProtocolError("server disconnected"), ModelUnavailableError),
])
def test_errors_are_translated(error, expected):
    with pytest.raises(expected) as caught:
        client_with(FakeOllama(error=error)).chat(MESSAGES)
    assert isinstance(caught.value, ModelError)
    assert caught.value.__cause__ is error


def test_other_exceptions_are_not_hidden():
    with pytest.raises(TypeError):
        client_with(FakeOllama(error=TypeError("bug"))).chat(MESSAGES)


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_real_ollama_client_with_no_server_running():
    client = OllamaModelClient(
        base_url=f"http://127.0.0.1:{_free_port()}", model_name="qwen3:8b", timeout_seconds=3,
    )
    with pytest.raises(ModelUnavailableError) as caught:
        client.chat(MESSAGES)
    assert "Could not connect to Ollama" in caught.value.reason
    assert "Ollama running" in caught.value.user_message


def test_model_without_thinking_mode_is_retried_without_the_setting():
    class NoThinkingOllama(FakeOllama):
        def __init__(self):
            super().__init__(reply={"message": {"content": "ok"}})
            self.calls = []

        def chat(self, **kwargs):
            self.calls.append(kwargs)
            if "think" in kwargs:
                raise ollama.ResponseError('"llama3.2" does not support thinking', 400)
            return self.reply

    fake = NoThinkingOllama()
    client = client_with(fake)
    assert client.chat(MESSAGES) == "ok"
    assert client.chat(MESSAGES) == "ok"
    assert ["think" in call for call in fake.calls] == [True, False, False]


def test_other_bad_requests_are_not_retried():
    fake = FakeOllama(error=ollama.ResponseError("invalid format schema", 400))
    with pytest.raises(ModelUnavailableError):
        client_with(fake).chat(MESSAGES)
