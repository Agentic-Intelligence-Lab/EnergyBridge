"""Provider-agnostic LLM client with OpenAI-compatible backend."""

from __future__ import annotations

import logging
import time

from openai import OpenAI

from energybridge.utils.config import LLMConfig, load_llm_config

logger = logging.getLogger(__name__)


class LLMClient:
    def __init__(
        self,
        config: LLMConfig | None = None,
        *,
        config_prefix: str = "LLM",
        use_key: str = "USE_LLM",
        fallback_prefix: str | None = None,
    ) -> None:
        self.config = config or load_llm_config(
            prefix=config_prefix,
            use_key=use_key,
            fallback_prefix=fallback_prefix,
        )
        if self.config.provider != "openai_compatible":
            raise ValueError(
                "Unsupported LLM_PROVIDER for now. Use 'openai_compatible'."
            )
        if not self.config.api_key:
            raise ValueError(
                "LLM_API_KEY is missing. Set it in .env before using LLMClient."
            )

        self._client = OpenAI(api_key=self.config.api_key, base_url=self.config.base_url)

    def chat(self, system_prompt: str, user_prompt: str,
             max_retries: int = 3, retry_base_delay: float = 5.0) -> str:
        return self.chat_with_metrics(
            system_prompt, user_prompt,
            max_retries=max_retries, retry_base_delay=retry_base_delay,
        )["text"]

    def chat_with_metrics(self, system_prompt: str, user_prompt: str,
                          max_retries: int = 3, retry_base_delay: float = 5.0) -> dict:
        """Call the LLM with exponential-backoff retry on empty / error responses.

        On each failure the call sleeps retry_base_delay * 2**attempt seconds
        before the next try (5s, 10s, 20s by default).
        """
        last_exc: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
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

                text = response.choices[0].message.content or ""
                if not text.strip():
                    raise ValueError("LLM returned empty response")

                usage = getattr(response, "usage", None)
                token_usage = {
                    "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
                    "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
                    "total_tokens": getattr(usage, "total_tokens", 0) or 0,
                }
                return {
                    "text": text,
                    "metrics": {
                        "used": True,
                        "provider": self.config.provider,
                        "model": self.config.model,
                        "latency_seconds": round(latency_seconds, 3),
                        "token_usage": token_usage,
                        "retries": attempt,
                    },
                }
            except Exception as exc:
                last_exc = exc
                if attempt < max_retries:
                    delay = retry_base_delay * (2 ** attempt)
                    logger.warning(
                        "LLM call failed (attempt %d/%d): %s — retrying in %.0fs",
                        attempt + 1, max_retries + 1, exc, delay,
                    )
                    print(f"  [LLMClient] retry {attempt+1}/{max_retries} in {delay:.0f}s — {exc}")
                    time.sleep(delay)
        raise last_exc  # type: ignore[misc]
