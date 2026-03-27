from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field

ROOT_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT_DIR / ".env"

if ENV_FILE.exists():
    load_dotenv(ENV_FILE)


def _env_flag(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default

    return raw_value.strip().lower() not in {"0", "false", "no", "off"}


class Settings(BaseModel):
    app_name: str = "企业 Agent 意图路由控制台"
    app_version: str = "0.1.0"
    api_host: str = "127.0.0.1"
    api_port: int = 8010
    log_level: str = "INFO"
    request_timeout_seconds: int = 25
    enable_llm_classifier: bool = Field(
        default_factory=lambda: _env_flag("ENABLE_LLM_CLASSIFIER", True)
    )
    llm_api_key: str = Field(
        default_factory=lambda: os.getenv("LLM_API_KEY", "").strip()
    )
    llm_base_url: str = Field(
        default_factory=lambda: os.getenv("LLM_BASE_URL", "").strip()
    )
    llm_model_id: str = Field(
        default_factory=lambda: os.getenv("LLM_MODEL_ID", "gpt-5-mini").strip()
    )
    root_dir: Path = ROOT_DIR
    data_dir: Path = ROOT_DIR / "data"
    static_dir: Path = ROOT_DIR / "app" / "static"

    api_key: str = Field(
        default_factory=lambda: os.getenv("API_KEY", "").strip()
    )
    require_auth: bool = Field(
        default_factory=lambda: _env_flag("REQUIRE_AUTH", False)
    )
    server_allow_action_execution: bool = Field(
        default_factory=lambda: _env_flag("SERVER_ALLOW_ACTION_EXECUTION", False)
    )

    circuit_breaker_failure_threshold: int = 3
    circuit_breaker_recovery_timeout: int = 60

    rule_high_confidence_threshold: float = 0.82

    cache_max_size: int = 256
    cache_ttl_seconds: int = 300

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_api_key and self.llm_base_url and self.llm_model_id)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
