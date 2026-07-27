"""
ApiKeyPool — rotate through multiple API keys with per-key cooldown.

Usage:
    from api_pool import ApiKeyPool

    pool = ApiKeyPool.from_file("api_keys.txt",
                                base_url="https://www.dmxapi.cn/v1",
                                model="claude-sonnet-4-6")
    text = pool.chat(system_prompt, user_prompt)

Key file format (api_keys.txt):
    # lines starting with # are ignored
    <YOUR_API_KEY_1>
    <YOUR_API_KEY_2>
    <YOUR_API_KEY_3>
"""

from __future__ import annotations

import os
import time
import threading
from pathlib import Path
from typing import List, Optional


class ApiKeyPool:
    """
    Rotate through multiple API keys to avoid per-key rate limits.

    Strategy: always pick the key that has been idle the longest.
    If the most-idle key still hasn't cooled down, sleep for the
    remaining cooldown period (printed to stdout so the user can see).
    """

    def __init__(
        self,
        keys: List[str],
        base_url: str,
        model: str,
        *,
        min_gap_s: float = 12.0,
        temperature: float = 0.2,
        max_tokens: int = 1024,
    ) -> None:
        if not keys:
            raise ValueError("ApiKeyPool needs at least one key")
        self._keys = list(keys)
        self._base_url = base_url
        self._model = model
        self._min_gap = min_gap_s
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._last_used: List[float] = [0.0] * len(keys)
        self._call_count: List[int] = [0] * len(keys)
        self._lock = threading.Lock()
        print(f"  [ApiPool] Initialized with {len(keys)} key(s), "
              f"min_gap={min_gap_s}s, model={model}")

    # ── factory methods ────────────────────────────────────────────────────

    @classmethod
    def from_file(
        cls,
        filepath: str | Path,
        base_url: str,
        model: str,
        **kwargs,
    ) -> "ApiKeyPool":
        """Load one key per line from a text file; lines starting with # ignored."""
        p = Path(filepath)
        if not p.exists():
            raise FileNotFoundError(
                f"API key file not found: {p}\n"
                f"Create it with one key per line (see api_keys.txt.example)"
            )
        keys = [
            line.strip()
            for line in p.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        if not keys:
            raise ValueError(f"No keys found in {p}")
        return cls(keys, base_url, model, **kwargs)

    @classmethod
    def from_env(
        cls,
        base_url: str,
        model: str,
        env_var: str = "LLM_API_KEYS",
        fallback_var: str = "LLM_API_KEY",
        **kwargs,
    ) -> "ApiKeyPool":
        """
        Load keys from environment variable.
        LLM_API_KEYS = comma-separated list  (preferred)
        LLM_API_KEY  = single key            (fallback)
        """
        raw = os.environ.get(env_var, "")
        if raw:
            keys = [k.strip() for k in raw.split(",") if k.strip()]
        else:
            single = os.environ.get(fallback_var, "")
            keys = [single] if single else []
        if not keys:
            raise ValueError(
                f"No keys found in ${env_var} or ${fallback_var}. "
                "Set one of these environment variables."
            )
        return cls(keys, base_url, model, **kwargs)

    @classmethod
    def from_dotenv(
        cls,
        dotenv_path: str | Path,
        base_url: str,
        model: str,
        key_file: Optional[str | Path] = None,
        **kwargs,
    ) -> "ApiKeyPool":
        """
        Load keys from either:
          1. key_file (text file, one key per line) — preferred
          2. LLM_API_KEYS= in the .env file
          3. LLM_API_KEY= in the .env file (single key)
        """
        # Option 1: explicit key file
        if key_file is not None:
            return cls.from_file(key_file, base_url, model, **kwargs)

        # Option 2/3: parse .env manually (avoid dotenv dependency)
        env = {}
        p = Path(dotenv_path)
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip().strip('"').strip("'")

        raw = env.get("LLM_API_KEYS", "")
        if raw:
            keys = [k.strip() for k in raw.split(",") if k.strip()]
        else:
            single = env.get("LLM_API_KEY", "")
            keys = [single] if single else []
        if not keys:
            raise ValueError(
                f"No keys found in {dotenv_path}. "
                "Add LLM_API_KEYS=key1,key2,... or provide a key_file."
            )
        return cls(keys, base_url, model, **kwargs)

    # ── internal key selection ─────────────────────────────────────────────

    def _acquire_key(self) -> tuple[int, str]:
        """
        Thread-safe: return (idx, key) for the most-idle key.
        Sleeps if even the best key needs more cooldown.
        """
        with self._lock:
            now = time.time()
            idle = [now - t for t in self._last_used]
            best = max(range(len(self._keys)), key=lambda i: idle[i])
            remaining = self._min_gap - idle[best]

        if remaining > 0:
            print(f"  [ApiPool] all {len(self._keys)} keys cooling down, "
                  f"wait {remaining:.1f}s …")
            time.sleep(remaining)

        with self._lock:
            self._last_used[best] = time.time()
            self._call_count[best] += 1
            return best, self._keys[best]

    # ── public chat API ────────────────────────────────────────────────────

    def chat(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        max_retries: int = 2,
    ) -> str:
        """
        Send a chat request, rotating keys.
        On failure, penalizes the failed key and retries with a different one.
        Raises the last exception if all retries fail.
        """
        import openai  # lazy import

        temp = temperature if temperature is not None else self._temperature
        mtok = max_tokens if max_tokens is not None else self._max_tokens

        last_exc: Exception | None = None
        for attempt in range(max_retries + 1):
            idx, key = self._acquire_key()
            try:
                client = openai.OpenAI(api_key=key, base_url=self._base_url)
                resp = client.chat.completions.create(
                    model=self._model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user",   "content": user_prompt},
                    ],
                    temperature=temp,
                    max_tokens=mtok,
                )
                text = (resp.choices[0].message.content or "").strip()
                if not text:
                    raise ValueError(f"Key #{idx + 1} returned empty response")
                return text

            except Exception as exc:
                last_exc = exc
                print(f"  [ApiPool] key #{idx + 1} failed "
                      f"(attempt {attempt + 1}/{max_retries + 1}): {exc}")
                # Penalize: mark this key as used _just now + min_gap so it
                # cools for an extra cycle before being selected again.
                with self._lock:
                    self._last_used[idx] = time.time() + self._min_gap

        raise last_exc  # type: ignore[misc]

    # ── diagnostics ───────────────────────────────────────────────────────

    def status(self) -> str:
        now = time.time()
        parts = []
        for i, (t, c) in enumerate(zip(self._last_used, self._call_count)):
            idle = now - t
            parts.append(f"key#{i + 1}: {c} calls, idle {idle:.0f}s")
        return " | ".join(parts)

    @property
    def pool_size(self) -> int:
        return len(self._keys)
