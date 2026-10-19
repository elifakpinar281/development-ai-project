import os
from dataclasses import dataclass
from dotenv import load_dotenv

# Load environment variables from .env file if present
load_dotenv()


@dataclass
class Config:
    """Simple configuration object reading environment variables."""

    ollama_base_url: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    model_name: str = os.getenv("MODEL_NAME", "qwen3:8b")

    database_url: str = os.getenv("DATABASE_URL", "postgresql://spotthephish:spotthephish@localhost:5432/spotthephish",)

    test_database_url: str = os.getenv("TEST_DATABASE_URL", "postgresql://spotthephish:spotthephish@localhost:5432/spotthephish_test",)

    safe_browsing_api_key: str = os.getenv("SAFE_BROWSING_API_KEY", "")

    http_timeout: float = float(os.getenv("HTTP_TIMEOUT", "5"))

    eval_cache_mode: str = os.getenv("EVAL_CACHE_MODE", "off").lower()

    case_history_write: bool = (os.getenv("CASE_HISTORY_WRITE", "true").lower() == "true")


# Instantiate global configuration object
config = Config()
