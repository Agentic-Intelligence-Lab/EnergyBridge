"""Human-readable skill registry for the current EnergyBridge agent loop."""

from __future__ import annotations

from energybridge.skills.explanation_generator import generate_explanation
from energybridge.skills.grid_signal_translator import translate_vpp_context_to_grid_demand
from energybridge.skills.preference_parser import parse_user_preference
from energybridge.skills.strategy_generator import generate_candidate_strategy
from energybridge.skills.vpp_participation_explainer import finalize_vpp_participation_explanation


SKILL_REGISTRY = {
    "parse_user_preference": {
        "function": parse_user_preference,
        "description": "Parse user preference text into structured comfort/cost/grid preferences.",
    },
    "translate_grid_signal": {
        "function": translate_vpp_context_to_grid_demand,
        "description": "Translate EnergyBridge grid signal into control intent and urgency.",
    },
    "generate_candidate_strategy": {
        "function": generate_candidate_strategy,
        "description": "Generate deterministic fallback strategy from preferences and grid context.",
    },
    "generate_explanation": {
        "function": generate_explanation,
        "description": "Generate concise user-facing explanation for selected control plan.",
    },
    "explain_vpp_participation": {
        "function": finalize_vpp_participation_explanation,
        "description": "Explain a VPP participation strategy from EnergyBridge to the household customer.",
    },
}


def list_skills() -> list[dict[str, str]]:
    return [
        {"name": name, "description": item["description"]}
        for name, item in SKILL_REGISTRY.items()
    ]
