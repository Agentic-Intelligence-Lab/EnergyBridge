"""Optional LLM-backed strategy option generation."""

from __future__ import annotations

import json

from energybridge.llm.client import LLMClient
from energybridge.llm.prompts import (
    STRATEGY_OPTIONS_SYSTEM_PROMPT,
    build_strategy_options_user_prompt,
)


def _to_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _extract_json_payload(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = [line for line in stripped.splitlines() if not line.strip().startswith("```")]
        stripped = "\n".join(lines).strip()

    array_start = stripped.find("[")
    array_end = stripped.rfind("]")
    if array_start != -1 and array_end != -1 and array_end > array_start:
        return stripped[array_start : array_end + 1]

    raise ValueError("LLM response did not contain a JSON array.")


def _normalize_option(option: dict, fallback_strategy: dict, index: int) -> dict:
    rationale = option.get("rationale", fallback_strategy.get("rationale", []))
    if isinstance(rationale, str):
        rationale = [rationale]
    if not isinstance(rationale, list):
        rationale = list(fallback_strategy.get("rationale", []))

    recommended_setpoint = option.get(
        "recommended_setpoint", fallback_strategy.get("recommended_setpoint", 25.0)
    )
    recommended_setpoint = max(18.0, min(30.0, float(recommended_setpoint)))

    return {
        "title": str(option.get("title", f"Strategy {index}")),
        "mode": str(option.get("mode", fallback_strategy.get("mode", "balanced"))),
        "recommended_setpoint": round(recommended_setpoint, 2),
        "pre_cooling": _to_bool(option.get("pre_cooling", fallback_strategy.get("pre_cooling", False))),
        "expected_user_impact": str(
            option.get(
                "expected_user_impact",
                fallback_strategy.get("expected_user_impact", "minimal"),
            )
        ),
        "rationale": [str(item) for item in rationale if str(item).strip()],
        "source": "llm",
    }


def _enforce_load_reduction_guardrails(
    normalized_option: dict,
    fallback_strategy: dict,
    context: dict,
) -> dict:
    home_state = context.get("home_state", {})
    grid_demand = context.get("grid_demand", {})
    current_setpoint = float(home_state.get("hvac_setpoint", 25.0) or 25.0)
    control_intent = str(grid_demand.get("control_intent", "normal_operation"))

    if control_intent in {"reduce_load", "cost_saving"}:
        recommended_setpoint = float(normalized_option.get("recommended_setpoint", current_setpoint))
        if recommended_setpoint < current_setpoint:
            normalized_option["recommended_setpoint"] = round(
                max(current_setpoint, float(fallback_strategy.get("recommended_setpoint", current_setpoint))),
                2,
            )
            rationale = list(normalized_option.get("rationale", []))
            rationale.append(
                "Setpoint was adjusted upward to avoid increasing HVAC load during a load-reduction event."
            )
            normalized_option["rationale"] = rationale

    return normalized_option


def generate_strategy_options(
    context: dict,
    fallback_strategy: dict,
    option_count: int = 3,
) -> tuple[list[dict], dict]:
    client = LLMClient()
    prompt = build_strategy_options_user_prompt(context=context, option_count=option_count)
    llm_result = client.chat_with_metrics(STRATEGY_OPTIONS_SYSTEM_PROMPT, prompt)
    response_text = llm_result["text"]
    payload = _extract_json_payload(response_text)
    raw_options = json.loads(payload)

    if not isinstance(raw_options, list) or not raw_options:
        raise ValueError("LLM did not return a non-empty strategy option list.")

    normalized_options = [
        _enforce_load_reduction_guardrails(
            _normalize_option(option, fallback_strategy, index),
            fallback_strategy,
            context,
        )
        for index, option in enumerate(raw_options[:option_count], start=1)
        if isinstance(option, dict)
    ]

    if not normalized_options:
        raise ValueError("LLM strategy option list did not contain valid objects.")

    return normalized_options, llm_result["metrics"]
