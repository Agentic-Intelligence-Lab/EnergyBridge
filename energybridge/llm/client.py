"""Provider-agnostic LLM client with OpenAI-compatible backend."""

from __future__ import annotations

import time

from openai import OpenAI

from energybridge.utils.config import load_llm_config


class LLMClient:
    def __init__(self) -> None:
        self.config = load_llm_config()
        if self.config.provider != "openai_compatible":
            raise ValueError(
                "Unsupported LLM_PROVIDER for now. Use 'openai_compatible'."
            )
        if not self.config.api_key:
            raise ValueError(
                "LLM_API_KEY is missing. Set it in .env before using LLMClient."
            )

        self._client = OpenAI(api_key=self.config.api_key, base_url=self.config.base_url)

    def chat(self, system_prompt: str, user_prompt: str) -> str:
        return self.chat_with_metrics(system_prompt, user_prompt)["text"]

    def chat_with_metrics(self, system_prompt: str, user_prompt: str) -> dict:
        start_time = time.perf_counter()
        response = self._client.chat.completions.create(
            model=self.config.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
        )
        latency_seconds = time.perf_counter() - start_time

        usage = getattr(response, "usage", None)
        token_usage = {
            "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
            "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
            "total_tokens": getattr(usage, "total_tokens", 0) or 0,
        }

        return {
            "text": response.choices[0].message.content or "",
            "metrics": {
                "used": True,
                "provider": self.config.provider,
                "model": self.config.model,
                "latency_seconds": round(latency_seconds, 3),
                "token_usage": token_usage,
            },
        }
