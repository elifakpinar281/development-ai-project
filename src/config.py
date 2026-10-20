import os
from dataclasses import dataclass
from dotenv import load_dotenv

# Load environment variables from .env file if present
load_dotenv()

def _env_bool(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes"}


@dataclass
class Config:
    ollama_base_url: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    model_name: str = os.getenv("MODEL_NAME", "qwen2.5:3b")

    model_timeout: float = float(os.getenv("MODEL_TIMEOUT", "120"))

    model_temperature: float = float(os.getenv("MODEL_TEMPERATURE", "0"))

    model_context_tokens: int = int(os.getenv("MODEL_CONTEXT_TOKENS", "8192"))

    model_think: bool = _env_bool("MODEL_THINK", "false")

    agent_max_tool_calls: int = int(os.getenv("AGENT_MAX_TOOL_CALLS", "6"))

    max_email_chars: int = int(os.getenv("MAX_EMAIL_CHARS", "200000"))

    prompt_max_body_chars: int = int(os.getenv("PROMPT_MAX_BODY_CHARS", "6000"))

    database_url: str = os.getenv("DATABASE_URL", "postgresql://spotthephish:spotthephish@localhost:5432/spotthephish",)

    test_database_url: str = os.getenv("TEST_DATABASE_URL", "postgresql://spotthephish:spotthephish@localhost:5432/spotthephish_test",)

    safe_browsing_api_key: str = os.getenv("SAFE_BROWSING_API_KEY", "")

    http_timeout: float = float(os.getenv("HTTP_TIMEOUT", "5"))

    eval_cache_mode: str = os.getenv("EVAL_CACHE_MODE", "off").lower()

    case_history_write: bool = (os.getenv("CASE_HISTORY_WRITE", "true").lower() == "true")


# Instantiate global configuration object
config = Config()
