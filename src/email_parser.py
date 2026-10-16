import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    ollama_base_url: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    model_name: str = os.getenv("MODEL_NAME", "llama3.2")

    database_url: str = os.getenv(
        "DATABASE_URL", "postgresql://spotthephish:spotthephish@localhost:5432/spotthephish"
    )
    safe_browsing_api_key: str = os.getenv("SAFE_BROWSING_API_KEY", "")
    http_timeout: float = float(os.getenv("HTTP_TIMEOUT", "5"))
    eval_cache_mode: str = os.getenv("EVAL_CACHE_MODE", "off")  # off | record | replay
    case_history_write: bool = os.getenv("CASE_HISTORY_WRITE", "true").lower() == "true"


config = Config()