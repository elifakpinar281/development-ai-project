"""Model picker for the web UI.

Only adds new behaviour on top of service_factory. It does not change how the
default service is built, so the rest of the application stays untouched.
"""
from __future__ import annotations

import logging
import threading
from typing import Optional

from src.config import config
from src.services import service_factory

logger = logging.getLogger(__name__)

_services: dict = {}
_lock = threading.Lock()


def available_models() -> list[str]:
    names: list[str] = [config.model_name]
    try:
        import ollama

        response = ollama.Client(host=config.ollama_base_url, timeout=3).list()
        if isinstance(response, dict):
            models = response.get("models", [])
        else:
            models = getattr(response, "models", [])
        for model in models:
            if isinstance(model, dict):
                name = model.get("model") or model.get("name")
            else:
                name = getattr(model, "model", None)
            if name and name not in names:
                names.append(name)
    except Exception as error:  # Ollama not running: only offer the configured model
        logger.info("Could not list Ollama models: %s", error)
    return names


def _new_model_client(model_name: str):
    from src.models.model_client import OllamaModelClient

    return OllamaModelClient(
        base_url=config.ollama_base_url,
        model_name=model_name,
        timeout_seconds=config.model_timeout,
        temperature=config.model_temperature,
        context_tokens=config.model_context_tokens,
        think=config.model_think,
    )


def get_service_for_model(model_name: Optional[str] = None):
    name = (model_name or "").strip() or config.model_name
    if name == config.model_name:
        return service_factory.get_default_service()
    with _lock:
        service = _services.get(name)
        if service is None:
            try:
                service = service_factory.build_analysis_service(model_client=_new_model_client(name))
            except Exception as error:
                logger.warning("Could not build a service for model %s (%s). Using the default model.", name, error)
                return service_factory.get_default_service()
            _services[name] = service
        return service


def get_model_client(model_name: Optional[str] = None):
    service = get_service_for_model(model_name)
    analyzer = getattr(service, "main_analyzer", None)
    client = getattr(analyzer, "model_client", None)
    if client is not None:
        return client
    return _new_model_client((model_name or "").strip() or config.model_name)