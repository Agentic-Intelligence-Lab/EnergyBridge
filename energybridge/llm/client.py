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

        self._active_key = self.config.api_key
        self._client = OpenAI(api_key=self.config.api_key, base_url=self.config.base_url)

    def _get_client_for_key(self, key: str) -> "OpenAI":
        """Return a client for the given key, reusing self._client if key unchanged."""
        if key != self._active_key:
            self._active_key = key
            self._client = OpenAI(api_key=key, base_url=self.config.base_url)
        return self._client

    def chat(self, system_prompt: str, user_prompt: str,
             max_retries: int = 3, retry_base_delay: float = 2.0,
             validate_fn=None) -> str:
        """Call LLM with key-pool rotation on failure.

        validate_fn(text) -> str: optional caller-provided validator.
        If it raises, the response is treated as invalid and retried with
        the next key in the pool.  It may also return a cleaned/transformed text.
        """
        return self.chat_with_metrics(
            system_prompt, user_prompt,
            max_retries=max_retries, retry_base_delay=retry_base_delay,
            validate_fn=validate_fn,
        )["text"]

    def chat_with_metrics(self, system_prompt: str, user_prompt: str,
                          max_retries: int = 3, retry_base_delay: float = 2.0,
                          validate_fn=None) -> dict:
        """Call the LLM with key-pool rotation and retry on empty/invalid responses.

        Rotation strategy: attempt i uses pool[i % len(pool)], so with a
        3-key pool and max_retries=3 each key is tried once before giving up.
        Between retries the call sleeps retry_base_delay seconds (flat, not
        exponential) to keep the simulation responsive.
        """
        pool = self.config.api_key_pool or [self.config.api_key]
        n_pool = len(pool)
        last_exc: Exception | None = None

        for attempt in range(max_retries):
            key = pool[attempt % n_pool]
            key_hint = f"pool[{attempt % n_pool}]...{key[-6:]}" if len(key) > 6 else f"pool[{attempt % n_pool}]"
            client = self._get_client_for_key(key)
            try:
                start_time = time.perf_counter()
                response = client.chat.completions.create(
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

                # Optional caller-provided validation (e.g. JSON parse check).
                # May raise on invalid; may return a cleaned/transformed text.
                if validate_fn is not None:
                    text = validate_fn(text)

                usage = getattr(response, "usage", None)
                token_usage = {
                    "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
                    "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
                    "total_tokens": getattr(usage, "total_tokens", 0) or 0,
                }
                if attempt > 0:
                    print(f"  [LLMClient] ✓ succeeded on attempt {attempt+1}/{max_retries} ({key_hint})")
                return {
                    "text": text,
                    "metrics": {
                        "used": True,
                        "provider": self.config.provider,
                        "model": self.config.model,
                        "latency_seconds": round(latency_seconds, 3),
                        "token_usage": token_usage,
                        "retries": attempt,
                        "key_index": attempt % n_pool,
                    },
                }
            except Exception as exc:
                last_exc = exc
                if attempt < max_retries - 1:
                    next_key = pool[(attempt + 1) % n_pool]
                    rotated = next_key != key
                    logger.warning(
                        "LLM attempt %d/%d failed (%s)%s: %s",
                        attempt + 1, max_retries, key_hint,
                        " — rotating key" if rotated else "", exc,
                    )
                    print(f"  [LLMClient] ⚠ attempt {attempt+1}/{max_retries} {key_hint}"
                          f"{' → rotating key' if rotated else ''}: {exc}")
                    time.sleep(retry_base_delay)

        print(f"  [LLMClient] ✗ all {max_retries} attempts exhausted — raising for fallback")
        raise last_exc  # type: ignore[misc]
