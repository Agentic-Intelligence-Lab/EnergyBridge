"""Configuration helpers for EnergyBridge."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


def _to_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class LLMConfig:
    use_llm: bool
    provider: str
    base_url: str
    api_key: str
    model: str
    temperature: float
    max_tokens: int


def _env_with_fallback(primary_key: str, default: str, fallback_key: str | None = None) -> str:
    value = os.getenv(primary_key)
    if value is not None:
        return value
    if fallback_key:
        fallback_value = os.getenv(fallback_key)
        if fallback_value is not None:
            return fallback_value
    return default


def load_llm_config(
    prefix: str = "LLM",
    use_key: str = "USE_LLM",
    fallback_prefix: str | None = None,
) -> LLMConfig:
    load_dotenv()

    def get_key(name: str) -> str:
        return f"{prefix}_{name}"

    def get_fallback_key(name: str) -> str | None:
        if not fallback_prefix:
            return None
        return f"{fallback_prefix}_{name}"

    return LLMConfig(
        use_llm=_to_bool(os.getenv(use_key), default=False),
        provider=_env_with_fallback(
            get_key("PROVIDER"),
            "openai_compatible",
            get_fallback_key("PROVIDER"),
        ),
        base_url=_env_with_fallback(
            get_key("BASE_URL"),
            "https://api.openai.com/v1",
            get_fallback_key("BASE_URL"),
        ),
        api_key=_env_with_fallback(
            get_key("API_KEY"),
            "",
            get_fallback_key("API_KEY"),
        ),
        model=_env_with_fallback(
            get_key("MODEL"),
            "gpt-4o-mini",
            get_fallback_key("MODEL"),
        ),
        temperature=float(
            _env_with_fallback(
                get_key("TEMPERATURE"),
                "0.2",
                get_fallback_key("TEMPERATURE"),
            )
        ),
        max_tokens=int(
            _env_with_fallback(
                get_key("MAX_TOKENS"),
                "1024",
                get_fallback_key("MAX_TOKENS"),
            )
        ),
    )
