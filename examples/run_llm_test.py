"""Optional test for the LLM wrapper. Not required by the default demo."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from energybridge.llm.client import LLMClient
from energybridge.llm.prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE
from energybridge.utils.config import load_llm_config


def main() -> None:
    config = load_llm_config()
    if not config.use_llm:
        print("USE_LLM is false. Skip LLM test.")
        return

    try:
        client = LLMClient()
    except ValueError as exc:
        print(f"LLM setup error: {exc}")
        return

    user_prompt = USER_PROMPT_TEMPLATE.format(text="Plan a safe and concise demand-response action.")
    answer = client.chat(SYSTEM_PROMPT, user_prompt)
    print("=== LLM Response ===")
    print(answer)


if __name__ == "__main__":
    main()
