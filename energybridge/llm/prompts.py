"""Prompt templates for optional LLM-backed modules."""

SYSTEM_PROMPT = (
    "You are an assistant for home-grid coordination. "
    "Provide concise, safe, and controllable recommendations."
)

USER_PROMPT_TEMPLATE = "Analyze this request and respond briefly: {text}"

STRATEGY_OPTIONS_SYSTEM_PROMPT = (
    "You design safe residential energy strategies. "
    "Return only valid JSON. Do not include markdown fences."
)


def build_strategy_options_user_prompt(context: dict, option_count: int = 3) -> str:
    return (
        "Generate exactly "
        f"{option_count} candidate home energy strategies as a JSON array. "
        "Each item must contain: title, mode, recommended_setpoint, pre_cooling, "
        "expected_user_impact, rationale. "
        "Use concise English text. "
        "Respect user comfort and keep setpoints within safe residential HVAC ranges. "
        f"Context: {context}"
    )

