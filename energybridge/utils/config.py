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


def load_llm_config() -> LLMConfig:
    load_dotenv()
    return LLMConfig(
        use_llm=_to_bool(os.getenv("USE_LLM"), default=False),
        provider=os.getenv("LLM_PROVIDER", "openai_compatible"),
        base_url=os.getenv("LLM_BASE_URL", "https://api.openai.com/v1"),
        api_key=os.getenv("LLM_API_KEY", ""),
        model=os.getenv("LLM_MODEL", "gpt-4o-mini"),
        temperature=float(os.getenv("LLM_TEMPERATURE", "0.2")),
        max_tokens=int(os.getenv("LLM_MAX_TOKENS", "1024")),
    )
