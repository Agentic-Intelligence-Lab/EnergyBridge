"""LLM-backed role-play user simulator for evaluation runs."""

from __future__ import annotations

import json
from typing import Any

from energybridge.llm.client import LLMClient


def _extract_json_payload(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = [line for line in stripped.splitlines() if not line.strip().startswith("```")]
        stripped = "\n".join(lines).strip()

    object_start = stripped.find("{")
    object_end = stripped.rfind("}")
    if object_start != -1 and object_end != -1 and object_end > object_start:
        return stripped[object_start : object_end + 1]

    raise ValueError("Role-play LLM response did not contain a JSON object.")


class RoleplayUserSimulator:
    def __init__(self) -> None:
        self.client = LLMClient(
            config_prefix="ROLEPLAY_LLM",
            use_key="ROLEPLAY_USE_LLM",
            fallback_prefix="LLM",
        )

    def _call_json(self, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        result = self.client.chat_with_metrics(system_prompt, user_prompt)
        payload = _extract_json_payload(result["text"])
        data = json.loads(payload)
        if not isinstance(data, dict):
            raise ValueError("Role-play LLM JSON payload must be an object.")
        return {
            "data": data,
            "raw_response": result["text"],
            "metrics": result["metrics"],
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
        }

    def create_persona(self) -> dict[str, Any]:
        system_prompt = (
            "You are generating a hidden evaluation persona for a residential energy user. "
            "Return only valid JSON and do not include markdown fences."
        )
        user_prompt = (
            "Create one random but realistic home energy user persona. "
            "The JSON object must contain: persona_id, display_name, summary, speaking_language, "
            "stable_preferences, speaking_style, decision_style. "
            "stable_preferences must contain comfort_priority, cost_priority, grid_priority, "
            "preferred_temp_min, preferred_temp_max, allow_pre_cooling, allow_temp_drift. "
            "Use float values for priorities that sum to about 1.0. "
            "Temperature values must be in Celsius only, and must stay within realistic home cooling bounds: "
            "preferred_temp_min between 23.0 and 25.5, preferred_temp_max between 24.5 and 27.0."
        )
        return self._call_json(system_prompt, user_prompt)

    def generate_user_input(
        self,
        persona: dict[str, Any],
        turn_index: int,
        scenario: dict[str, Any],
        memory_snapshot: dict[str, Any],
        history_summary: list[dict[str, Any]],
    ) -> dict[str, Any]:
        system_prompt = (
            "You are role-playing a single residential user consistently across multiple turns. "
            "Stay faithful to the hidden persona. Return only valid JSON."
        )
        user_prompt = (
            "Role-play this user persona:\n"
            f"{json.dumps(persona, ensure_ascii=False)}\n\n"
            f"Turn index: {turn_index}\n"
            "Current VPP/event scenario:\n"
            f"{json.dumps(scenario, ensure_ascii=False)}\n\n"
            "System memory snapshot:\n"
            f"{json.dumps(memory_snapshot, ensure_ascii=False)}\n\n"
            "Past turn summary:\n"
            f"{json.dumps(history_summary, ensure_ascii=False)}\n\n"
            "Return a JSON object with fields: user_input, hidden_goal, reveal_focus. "
            "The user_input should be 1 to 2 natural English sentences. "
            "Do not reveal every stable preference every turn. Reveal only one or two aspects naturally. "
            "Prefer revealing an aspect the system memory does not seem to have learned yet. "
            "When talking about comfort, use words such as comfort or comfortable. "
            "When talking about cost, use words such as save, savings, or cheap. "
            "When talking about grid support, use words such as grid, peak shaving, or demand response. "
            "When talking about pre-cooling, use pre-cooling. "
            "When talking about temperature drift, use drift or float."
        )
        return self._call_json(system_prompt, user_prompt)

    def choose_strategy(
        self,
        persona: dict[str, Any],
        turn_index: int,
        scenario: dict[str, Any],
        strategy_options: list[dict[str, Any]],
    ) -> dict[str, Any]:
        system_prompt = (
            "You are role-playing the same residential user, not the grid operator and not the controller. "
            "Choose the VPP response strategy that this user would realistically approve before the event. "
            "Stay faithful to the hidden persona, including comfort tolerance, cost sensitivity, grid cooperation, "
            "appliance service rules, household routines, and any past feedback. Return only valid JSON."
        )
        user_prompt = (
            "Persona:\n"
            f"{json.dumps(persona, ensure_ascii=False)}\n\n"
            f"Turn index: {turn_index}\n"
            "Scenario:\n"
            f"{json.dumps(scenario, ensure_ascii=False)}\n\n"
            "Available strategy options:\n"
            f"{json.dumps(strategy_options, ensure_ascii=False)}\n\n"
            "Decision instructions:\n"
            "- Select exactly one option that best matches the user's own preference.\n"
            "- Consider thermal comfort, electricity cost, willingness to support VPP peak shaving, and whether appliance tasks still finish.\n"
            "- Do not always choose the most energy-saving option; choose what this persona would actually accept.\n"
            "- If a strategy skips or delays required appliance tasks too aggressively, penalize it unless the persona clearly allows that.\n"
            "- If past events show dissatisfaction, adapt the choice accordingly.\n\n"
            "Return a JSON object with fields: selected_index, approved, reason. "
            "selected_index is 1-based and must refer to one of the provided options. "
            "reason must be concise and written as the user's rationale."
        )
        return self._call_json(system_prompt, user_prompt)

    def generate_feedback(
        self,
        persona: dict[str, Any],
        turn_index: int,
        selected_strategy: dict[str, Any],
        projected_control_plan: dict[str, Any],
        projected_safety_report: dict[str, Any],
        zone_group_context: dict | None = None,
    ) -> dict[str, Any]:
        system_prompt = (
            "You are role-playing the same residential/office user. Judge satisfaction realistically. "
            "Return only valid JSON."
        )
        zone_section = ""
        if zone_group_context:
            zone_section = (
                "\nZone group thermal context (office building):\n"
                + json.dumps(zone_group_context, ensure_ascii=False)
                + "\nScore zone_comfort_scores per group (Core/Bottom/Middle/Top).\n"
            )
        user_prompt = (
            "Persona:\n"
            f"{json.dumps(persona, ensure_ascii=False)}\n\n"
            f"Turn index: {turn_index}\n"
            "Selected strategy:\n"
            f"{json.dumps(selected_strategy, ensure_ascii=False)}\n\n"
            "Projected control plan:\n"
            f"{json.dumps(projected_control_plan, ensure_ascii=False)}\n\n"
            "Projected safety report:\n"
            f"{json.dumps(projected_safety_report, ensure_ascii=False)}\n"
            f"{zone_section}\n"
            "Scoring guidance:\n"
            "- Stay faithful to the persona's scoring_weights and tags, not the grid operator's goals.\n"
            "- For low_incentive or price_indifferent users, satisfaction should be dominated by comfort, routine smoothness, and whether the controller stayed low-pressure. Do not invent a financial pitch if it is absent.\n"
            "- For low_incentive or price_indifferent users, weak energy/VPP impact may lower energy_score or vpp_score, but should not lower overall satisfaction by itself when comfort, consent, and routine were preserved.\n"
            "- Event-level VPP success in this benchmark means no present non-AC appliance was scheduled or run inside the VPP window. Do not judge success by shed/cap targets, ratio, actual_shed, or daily energy alone.\n"
            "- If event-level VPP achieved is true, do not say the VPP event was missed in the comment. You may still mention limited savings, but call the appliance-avoidance criterion successful.\n"
            "- If the control plan says an appliance is fixed/non-DR-adjustable, do not treat that fixed operation as an agent scheduling violation.\n"
            "- For low-disruption or confirmation-required users, if comfort/consent/routine were preserved and only fixed non-DR appliances limited VPP, keep overall satisfaction separate from the VPP subscore.\n"
            "- If a required controllable task was skipped or an approved comfort boundary was exceeded, score harshly.\n\n"
            "Return a JSON object with EXACTLY these fields:\n"
            "  satisfaction_score (int 1-5): overall satisfaction\n"
            "  comfort_score (int 1-5): thermal comfort satisfaction\n"
            "  energy_score (int 1-5): satisfaction with energy usage / cost\n"
            "  vpp_score (int 1-5): satisfaction with VPP demand-response handling\n"
            "  satisfaction_label: one of very_satisfied/satisfied/neutral/dissatisfied/very_dissatisfied\n"
            "  comment (str <=80 chars): brief reason\n"
            + ("  zone_comfort_scores: {Core: X, Bottom: X, Middle: X, Top: X}\n" if zone_group_context else "")
            + "All score fields must be integers 1-5."
        )
        return self._call_json(system_prompt, user_prompt)
