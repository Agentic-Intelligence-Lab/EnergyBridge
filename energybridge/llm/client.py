"""Provider-agnostic LLM client with OpenAI-compatible backend."""

from __future__ import annotations

import logging
import random
import threading
import time

from openai import OpenAI

from energybridge.utils.config import LLMConfig, load_llm_config

logger = logging.getLogger(__name__)


class LLMClient:
    _pool_lock = threading.Lock()
    _pool_counter = random.randint(0, 1_000_000)

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

    @classmethod
    def _next_pool_start(cls, n_pool: int) -> int:
        if n_pool <= 1:
            return 0
        with cls._pool_lock:
            idx = cls._pool_counter % n_pool
            cls._pool_counter += 1
        return idx

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

        Rotation strategy: each request starts at a process-local round-robin
        key index, then retries advance through the pool. This keeps concurrent
        benchmark runs from all hammering pool[0] before any other key is used.
        Between retries the call sleeps retry_base_delay seconds (flat, not
        exponential) to keep the simulation responsive.
        """
        pool = self.config.api_key_pool or [self.config.api_key]
        n_pool = len(pool)
        start_idx = self._next_pool_start(n_pool)
        last_exc: Exception | None = None

        for attempt in range(max_retries):
            pool_idx = (start_idx + attempt) % n_pool
            key = pool[pool_idx]
            key_hint = f"pool[{pool_idx}]...{key[-6:]}" if len(key) > 6 else f"pool[{pool_idx}]"
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
                        "key_index": pool_idx,
                    },
                }
            except Exception as exc:
                last_exc = exc
                if attempt < max_retries - 1:
                    next_key = pool[(start_idx + attempt + 1) % n_pool]
                    rotated = next_key != key
                    exc_text = f"{type(exc).__name__}: {exc}"
                    logger.warning(
                        "LLM attempt %d/%d failed (%s)%s: %s",
                        attempt + 1, max_retries, key_hint,
                        " — rotating key" if rotated else "", exc_text,
                    )
                    print(f"  [LLMClient] ⚠ attempt {attempt+1}/{max_retries} {key_hint}"
                          f"{' → rotating key' if rotated else ''}: {exc_text}")
                    time.sleep(retry_base_delay)

        print(f"  [LLMClient] ✗ all {max_retries} attempts exhausted — raising for fallback")
        raise last_exc  # type: ignore[misc]
