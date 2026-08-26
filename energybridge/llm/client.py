"""Provider-agnostic LLM client with OpenAI-compatible backend."""

from __future__ import annotations

import logging
import random
import threading
import time
from typing import Any

from openai import OpenAI

from energybridge.utils.config import LLMConfig, load_llm_config

logger = logging.getLogger(__name__)
STRUCTURED_OUTPUT_TRANSPORT_VERSION = "openai_compatible_json_object_with_validation_v1"


class LLMCallError(RuntimeError):
    """Exhausted LLM request with credential-free protocol telemetry."""

    def __init__(self, *, failure_type: str, metrics: dict[str, Any]) -> None:
        super().__init__(f"LLM request exhausted after {metrics.get('attempts', 0)} attempts ({failure_type})")
        self.failure_type = failure_type
        self.metrics = metrics


def _response_format_unsupported(exc: Exception) -> bool:
    """Identify an OpenAI-compatible backend that lacks JSON response mode."""
    message = str(exc).lower()
    return any(
        marker in message
        for marker in (
            "response_format",
            "response format",
            "json mode",
            "json_object",
        )
    ) and any(
        marker in message
        for marker in (
            "unsupported",
            "not support",
            "unknown",
            "unrecognized",
            "invalid parameter",
            "extra inputs are not permitted",
        )
    )


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
        self._client = OpenAI(
            api_key=self.config.api_key,
            base_url=self.config.base_url,
            timeout=self.config.timeout_seconds,
        )

    def _get_client_for_key(self, key: str) -> "OpenAI":
        """Return a client for the given key, reusing self._client if key unchanged."""
        if key != self._active_key:
            self._active_key = key
            self._client = OpenAI(
                api_key=key,
                base_url=self.config.base_url,
                timeout=self.config.timeout_seconds,
            )
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
             validate_fn=None,
             response_format: dict[str, Any] | None = None) -> str:
        """Call LLM with key-pool rotation on failure.

        validate_fn(text) -> str: optional caller-provided validator.
        If it raises, the response is treated as invalid and retried with
        the next key in the pool.  It may also return a cleaned/transformed text.
        """
        return self.chat_with_metrics(
            system_prompt, user_prompt,
            max_retries=max_retries, retry_base_delay=retry_base_delay,
            validate_fn=validate_fn,
            response_format=response_format,
        )["text"]

    def chat_with_metrics(self, system_prompt: str, user_prompt: str,
                          max_retries: int = 3, retry_base_delay: float = 2.0,
                          validate_fn=None,
                          response_format: dict[str, Any] | None = None) -> dict:
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
        requested_response_format = dict(response_format or {}) or None
        active_response_format = requested_response_format
        response_format_fallback_type: str | None = None
        provider_failures = 0
        validation_failures = 0
        empty_response_failures = 0
        length_truncation_failures = 0
        finish_reasons: list[str] = []
        cumulative_latency_seconds = 0.0
        cumulative_prompt_tokens = 0
        cumulative_completion_tokens = 0
        cumulative_total_tokens = 0

        for attempt in range(max_retries):
            pool_idx = (start_idx + attempt) % n_pool
            key = pool[pool_idx]
            # A pool index is enough to diagnose rotation. Never expose any
            # part of an API credential in logs, even a suffix.
            key_hint = f"pool[{pool_idx}]"
            client = self._get_client_for_key(key)
            try:
                start_time = time.perf_counter()
                request_kwargs: dict[str, Any] = {
                    "model": self.config.model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": self.config.temperature,
                    "max_tokens": self.config.max_tokens,
                }
                if active_response_format is not None:
                    request_kwargs["response_format"] = active_response_format
                try:
                    response = client.chat.completions.create(**request_kwargs)
                except Exception as exc:
                    if active_response_format is None or not _response_format_unsupported(exc):
                        provider_failures += 1
                        raise
                    # Compatibility downgrade is transport-only. It does not
                    # alter the prompt, validation contract, or model content.
                    response_format_fallback_type = type(exc).__name__
                    active_response_format = None
                    request_kwargs.pop("response_format", None)
                    try:
                        response = client.chat.completions.create(**request_kwargs)
                    except Exception:
                        provider_failures += 1
                        raise
                cumulative_latency_seconds += time.perf_counter() - start_time

                finish_reason = str(response.choices[0].finish_reason or "unknown").strip().lower()
                if finish_reason not in {"stop", "length", "content_filter", "tool_calls"}:
                    finish_reason = "other"
                finish_reasons.append(finish_reason)
                if finish_reason == "length":
                    length_truncation_failures += 1
                usage = getattr(response, "usage", None)
                prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
                completion_tokens = getattr(usage, "completion_tokens", 0) or 0
                total_tokens = getattr(usage, "total_tokens", 0) or 0
                cumulative_prompt_tokens += int(prompt_tokens)
                cumulative_completion_tokens += int(completion_tokens)
                cumulative_total_tokens += int(total_tokens or (prompt_tokens + completion_tokens))
                text = response.choices[0].message.content or ""
                if not text.strip():
                    empty_response_failures += 1
                    raise ValueError("LLM returned empty response")

                # Optional caller-provided validation (e.g. JSON parse check).
                # May raise on invalid; may return a cleaned/transformed text.
                if validate_fn is not None:
                    try:
                        text = validate_fn(text)
                    except Exception:
                        validation_failures += 1
                        raise

                token_usage = {
                    "prompt_tokens": cumulative_prompt_tokens,
                    "completion_tokens": cumulative_completion_tokens,
                    "total_tokens": cumulative_total_tokens,
                }
                if attempt > 0:
                    print(f"  [LLMClient] ✓ succeeded on attempt {attempt+1}/{max_retries} ({key_hint})")
                return {
                    "text": text,
                    "metrics": {
                        "used": True,
                        "provider": self.config.provider,
                        "model": self.config.model,
                        "latency_seconds": round(cumulative_latency_seconds, 3),
                        "token_usage": token_usage,
                        "retries": attempt,
                        "key_index": pool_idx,
                        "response_format_requested": (
                            str((requested_response_format or {}).get("type", "")) or None
                        ),
                        "response_format_active": active_response_format is not None,
                        "response_format_fallback": response_format_fallback_type is not None,
                        "response_format_fallback_type": response_format_fallback_type,
                        "provider_failures": provider_failures,
                        "validation_failures": validation_failures,
                        "empty_response_failures": empty_response_failures,
                        "length_truncation_failures": length_truncation_failures,
                        "finish_reason": finish_reason,
                        "attempt_finish_reasons": list(finish_reasons),
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
        failure_type = type(last_exc).__name__ if last_exc is not None else "UnknownError"
        raise LLMCallError(
            failure_type=failure_type,
            metrics={
                "used": False,
                "provider": self.config.provider,
                "model": self.config.model,
                "latency_seconds": round(cumulative_latency_seconds, 3),
                "token_usage": {
                    "prompt_tokens": cumulative_prompt_tokens,
                    "completion_tokens": cumulative_completion_tokens,
                    "total_tokens": cumulative_total_tokens,
                },
                "attempts": max_retries,
                "retries": max(0, max_retries - 1),
                "response_format_requested": (
                    str((requested_response_format or {}).get("type", "")) or None
                ),
                "response_format_active": active_response_format is not None,
                "response_format_fallback": response_format_fallback_type is not None,
                "response_format_fallback_type": response_format_fallback_type,
                "provider_failures": provider_failures,
                "validation_failures": validation_failures,
                "empty_response_failures": empty_response_failures,
                "length_truncation_failures": length_truncation_failures,
                "finish_reason": finish_reasons[-1] if finish_reasons else None,
                "attempt_finish_reasons": list(finish_reasons),
                "exhausted": True,
                "exhausted_calls": 1,
                "failure_type": failure_type,
            },
        ) from last_exc
