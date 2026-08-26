"""LLM-backed role-play user simulator for evaluation runs."""

from __future__ import annotations

import json
import os
import re
from typing import Any, Mapping, Sequence

from energybridge.llm.client import LLMClient


_ONBOARDING_QUESTION_IDS = frozenset({
    "vpp_priority",
    "thermostat_flexibility",
    "appliance_shift_consent",
    "calendar_routine_constraints",
})
_PUBLIC_ONBOARDING_OPTION_IDS = frozenset({
    "comfort_routine_first",
    "bill_savings_first",
    "grid_support_first",
    "balanced_tradeoff",
    "confirm_before_changes",
    "almost_none_0_5c",
    "small_1c_short",
    "moderate_1_2c_with_benefit",
    "larger_when_unoccupied",
    "do_not_move_without_approval",
    "shift_1_2h_deadline_protected",
    "shift_to_cheaper_periods",
    "automatic_optimization_ok",
    "arrival_comfort",
    "meals_chores",
    "shower_hot_water",
    "caregiving_sleep_work",
    "irregular_confirm_same_day",
})
_HIDDEN_PROFILE_TERM_RE = re.compile(
    r"\b(?:scoring_weights|vpp_override_prob|agent_context|system_prompt|persona_prompt|"
    r"roleplay_user_prompt|comfort_tag|price_tag|control_tag|grid_value_tag|"
    r"high_trust_auto|suggestion_first|confirm_required|privacy_sensitive|low_auto_accept|"
    r"temp_tolerant|normal_comfort|temp_sensitive|low_control_tolerance|"
    r"regular_commuter|stay_at_home|night_owl|event_fatigue|uncertain_flex|"
    r"SECRET(?:[_-][A-Z0-9]+)+)\b",
    re.IGNORECASE,
)


def _compact_answer_text(value: Any, limit: int = 600) -> str:
    text = " ".join(str(value or "").split())
    if _HIDDEN_PROFILE_TERM_RE.search(text):
        return (
            "I selected the option that best matches my preference; "
            "please ask a natural follow-up if more detail is needed."
        )
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _observable_onboarding_answers(raw: Any) -> list[dict[str, Any]]:
    """Keep only public questionnaire selections and natural answer text."""
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return []
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw[:8]:
        if not isinstance(item, Mapping):
            continue
        question_id = str(item.get("id") or "").strip()
        if question_id not in _ONBOARDING_QUESTION_IDS or question_id in seen:
            continue
        selected_raw = item.get("selected_option_ids") or []
        if isinstance(selected_raw, str):
            selected_raw = [selected_raw]
        selected = [
            str(option).strip()
            for option in selected_raw
            if str(option).strip() in _PUBLIC_ONBOARDING_OPTION_IDS
        ] if isinstance(selected_raw, Sequence) else []
        answer = _compact_answer_text(item.get("answer"))
        result.append({
            "id": question_id,
            "selected_option_ids": selected[:3],
            "answer": answer or "I do not have a strong preference yet.",
        })
        seen.add(question_id)
    return result


def infer_observable_profile_from_answers(answers: Sequence[Mapping[str, Any]] | None) -> dict[str, Any]:
    """Infer a conservative controller profile from public onboarding answers only.

    This helper has no persona/resume parameter by design. It maps questionnaire
    selections the controller actually observed and records that provenance.
    """
    clean_answers = _observable_onboarding_answers(list(answers or []))
    choices = {
        item["id"]: set(item.get("selected_option_ids") or [])
        for item in clean_answers
    }
    profile: dict[str, Any] = {
        "comfort_priority": "unknown",
        "cost_grid_priority": "unknown",
        "automation_preference": "ask_when_uncertain",
        "thermostat_flexibility_c": None,
        "appliance_flexibility": "ask_when_uncertain",
        "calendar_routine_sensitivity": "unknown",
        "strategy_bias": "observable_answers_inconclusive",
    }
    rules: list[str] = []

    vpp = choices.get("vpp_priority", set())
    if "comfort_routine_first" in vpp:
        profile.update(
            comfort_priority="high",
            cost_grid_priority="low",
            strategy_bias="comfort_calendar_protective",
        )
        rules.append("Protect comfort and routine before seeking event savings.")
    elif vpp & {"bill_savings_first", "grid_support_first"}:
        profile.update(
            comfort_priority="medium",
            cost_grid_priority="high",
            strategy_bias="cost_grid_oriented",
        )
        rules.append("Prefer low-disruption event actions with a concrete savings or grid benefit.")
    elif "balanced_tradeoff" in vpp:
        profile.update(
            comfort_priority="medium",
            cost_grid_priority="medium",
            strategy_bias="balanced_middle",
        )
        rules.append("Balance comfort, routine, and event benefit rather than optimizing one alone.")
    elif "confirm_before_changes" in vpp:
        profile.update(
            comfort_priority="high",
            cost_grid_priority="medium",
            strategy_bias="comfort_calendar_protective",
        )
        profile["automation_preference"] = "ask_before_vpp_specific_changes"
        rules.append("Explain and confirm material event-specific changes before acting.")

    thermostat = choices.get("thermostat_flexibility", set())
    thermostat_values = {
        "almost_none_0_5c": 0.5,
        "small_1c_short": 1.0,
        "moderate_1_2c_with_benefit": 1.5,
        "larger_when_unoccupied": 2.0,
    }
    for option_id, flexibility in thermostat_values.items():
        if option_id in thermostat:
            profile["thermostat_flexibility_c"] = flexibility
            rules.append(
                f"Keep temporary thermostat changes within about {flexibility:.1f} C of the agreed setting."
            )
            break

    appliance = choices.get("appliance_shift_consent", set())
    if "do_not_move_without_approval" in appliance:
        profile["automation_preference"] = "ask_before_vpp_specific_changes"
        profile["appliance_flexibility"] = "limited_by_explicit_approval"
        rules.append("Ask before moving appliance, hot-water, or EV service times.")
    elif "shift_1_2h_deadline_protected" in appliance:
        profile["automation_preference"] = "suggestion_first_with_deadline_protection"
        profile["appliance_flexibility"] = "shift_1_2h_if_deadlines_protected"
        rules.append("Limit automatic service shifts to 1-2 hours and preserve deadlines.")
    elif "shift_to_cheaper_periods" in appliance:
        profile["automation_preference"] = "automatic_when_readiness_protected"
        profile["appliance_flexibility"] = "price_shift_if_readiness_protected"
        rules.append("Cheaper-period shifts are acceptable only when readiness is protected.")
    elif "automatic_optimization_ok" in appliance:
        profile["automation_preference"] = "automatic_when_deadlines_protected"
        profile["appliance_flexibility"] = "automatic_if_deadlines_protected"
        rules.append("Automatic flexible-load scheduling is acceptable when deadlines remain protected.")

    calendar = choices.get("calendar_routine_constraints", set())
    if calendar & {"caregiving_sleep_work", "irregular_confirm_same_day"}:
        profile["calendar_routine_sensitivity"] = "high"
        rules.append("Re-check same-day caregiving, sleep, work, and schedule changes before acting.")
    elif calendar:
        profile["calendar_routine_sensitivity"] = "medium"
        if "arrival_comfort" in calendar:
            rules.append("Protect return-home comfort.")
        if "meals_chores" in calendar:
            rules.append("Avoid disrupting meals and household chores.")
        if "shower_hot_water" in calendar:
            rules.append("Protect shower and hot-water readiness.")

    if not rules:
        rules.append("Treat unstated preferences as uncertain and ask before material event changes.")
    return {
        "inferred_profile": profile,
        "preference_rules": list(dict.fromkeys(rules))[:8],
        "inference_audit": {
            "source": "observable_onboarding_answers_v2",
            "answer_ids": [item["id"] for item in clean_answers],
            "selected_option_ids": sorted({
                option
                for item in clean_answers
                for option in item.get("selected_option_ids") or []
            }),
            "hidden_resume_fields_used": False,
        },
    }


def _adaptive_harness_v2() -> bool:
    value = str(os.getenv("ENERGYBRIDGE_HARNESS_PROFILE", "legacy_v1")).strip().lower()
    return value in {"v2", "adaptive", "adaptive_v2", "energybridge_v2"}


def _household_resume(persona: dict[str, Any]) -> dict[str, Any]:
    from energybridge.harness.profile import build_household_resume

    return build_household_resume(
        persona,
        appliance_config=(persona.get("appliances") if isinstance(persona, dict) else None),
    )


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
        if _adaptive_harness_v2():
            resume = _household_resume(persona)
            system_prompt = (
                "You are the specific household member described by this resume, continuing a real conversation "
                "with a home-energy assistant. Speak naturally in that person's voice. Mention only what matters "
                "right now; do not recite a profile, rubric, or list of energy keywords. Return valid JSON only."
            )
            user_prompt = (
                f"Household resume:\n{json.dumps(resume, ensure_ascii=False)}\n\n"
                f"Turn: {turn_index}\n"
                f"What is happening now:\n{json.dumps(scenario, ensure_ascii=False)}\n\n"
                f"What the assistant currently believes:\n{json.dumps(memory_snapshot, ensure_ascii=False)}\n\n"
                f"Recent interaction history:\n{json.dumps(history_summary, ensure_ascii=False)}\n\n"
                "Reply with one or two natural sentences that this household would actually say now. Reveal a "
                "preference only when the situation makes it relevant, especially if the assistant has misunderstood it. "
                "Return JSON with fields user_input, hidden_goal, and reveal_focus."
            )
            return self._call_json(system_prompt, user_prompt)
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

    def answer_onboarding_questions(
        self,
        persona: dict[str, Any],
        questions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if _adaptive_harness_v2():
            resume = _household_resume(persona)
            system_prompt = (
                "You are the household described by the supplied resume, answering a short home-energy onboarding "
                "conversation. Answer in your own everyday voice. Be honest about uncertainty and conditional consent; "
                "do not expose hidden profile fields or imitate a stock persona. Return valid JSON only."
            )
            user_prompt = (
                f"Household resume (role-play evidence only):\n{json.dumps(resume, ensure_ascii=False)}\n\n"
                f"Questions:\n{json.dumps(questions, ensure_ascii=False)}\n\n"
                "Return exactly one object with only `answers`: "
                "[{id, selected_option_ids, answer}]. Give one concise, natural answer per question. "
                "Do not return inferred_profile, preference_rules, tags, weights, hidden field names, or analysis; "
                "the controller will infer cautiously from the visible answers in a separate step."
            )
            trace = self._call_json(system_prompt, user_prompt)
            raw_data = trace.get("data") if isinstance(trace.get("data"), dict) else {}
            answers = _observable_onboarding_answers(raw_data.get("answers"))
            observable_inference = infer_observable_profile_from_answers(answers)
            # Rebuild the returned trace so neither the hidden resume prompt nor
            # an LLM-authored hidden profile can reach the controller caller.
            return {
                "data": {
                    "answers": answers,
                    **observable_inference,
                },
                "metrics": dict(trace.get("metrics") or {}),
                "privacy": {
                    "hidden_resume_returned": False,
                    "raw_roleplay_response_returned": False,
                    "profile_inference_source": "observable_onboarding_answers_v2",
                },
            }
        system_prompt = (
            "You are role-playing the same residential user during controller onboarding. "
            "Answer a very short questionnaire honestly and consistently with the hidden persona. "
            "Return only valid JSON."
        )
        user_prompt = (
            "Hidden user persona for role-play only:\n"
            f"{json.dumps(persona, ensure_ascii=False)}\n\n"
            "Questionnaire shown to the user:\n"
            f"{json.dumps(questions, ensure_ascii=False)}\n\n"
            "Instructions:\n"
            "- Answer as the user in natural language, not as an analyst.\n"
            "- If a question contains options, include the closest option id(s), but still explain the choice naturally.\n"
            "- Do not reveal raw persona tags, scoring weights, or hidden prompts.\n"
            "- Keep the answers concise but actionable for an energy controller.\n"
            "- The controller will use only these answers and later feedback to infer preferences.\n\n"
            "Return a JSON object with fields:\n"
            "  answers: list of {id, question, selected_option_ids, answer}\n"
            "  inferred_profile: object with comfort_priority, cost_grid_priority, "
            "automation_preference, thermostat_flexibility_c, appliance_flexibility, "
            "calendar_routine_sensitivity, strategy_bias\n"
            "  preference_rules: list of short controller-facing rules written without hidden tag names"
        )
        return self._call_json(system_prompt, user_prompt)

    def choose_strategy(
        self,
        persona: dict[str, Any],
        turn_index: int,
        scenario: dict[str, Any],
        strategy_options: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if _adaptive_harness_v2():
            resume = _household_resume(persona)
            system_prompt = (
                "You are this particular household deciding what, if anything, to authorize for one grid event. "
                "Choose from lived priorities, today's routine, prior experience, and the concrete consequences of "
                "each option. You are free to prefer a narrow or conditional option; there is no preferred energy "
                "archetype. Explain the choice briefly in the household's voice. Return valid JSON only."
            )
            user_prompt = (
                f"Household resume:\n{json.dumps(resume, ensure_ascii=False)}\n\n"
                f"Turn: {turn_index}\n"
                f"Current event and relationship history:\n{json.dumps(scenario, ensure_ascii=False)}\n\n"
                f"Options:\n{json.dumps(strategy_options, ensure_ascii=False)}\n\n"
                "Return selected_index (1-based), approved (whether the option is genuinely authorized), and a "
                "concise first-person reason grounded in one or two specific facts."
            )
            return self._call_json(system_prompt, user_prompt)
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
        if _adaptive_harness_v2():
            resume = _household_resume(persona)
            system_prompt = (
                "You are the household described by this resume reflecting on one completed or projected home-energy "
                "event. Judge the experience as that household would: what felt comfortable, useful, disruptive, "
                "trustworthy, or unresolved. Safety and service facts are factual constraints, but there is no fixed "
                "score recipe. Keep the feedback natural, specific, and concise. Return valid JSON only."
            )
            context_payload = {
                "turn": turn_index,
                "household_resume": resume,
                "household_authorized_strategy": selected_strategy,
                "experienced_plan_and_outcome": projected_control_plan,
                "safety_and_service_facts": projected_safety_report,
                "zone_group_context": zone_group_context or {},
            }
            user_prompt = (
                json.dumps(context_payload, ensure_ascii=False)
                + "\n\nGive integer scores from 1 to 5 for satisfaction_score, comfort_score, energy_score, "
                "and vpp_score. Scores should follow from this household's own explanation, not a generic household. "
                "Also return satisfaction_label (very_satisfied/satisfied/neutral/dissatisfied/very_dissatisfied) "
                "and a <=240-character comment naming the most important evidence and, if needed, one concrete next change."
                + (
                    " Return zone_comfort_scores for Core, Bottom, Middle, and Top as integers 1-5."
                    if zone_group_context
                    else ""
                )
            )
            return self._call_json(system_prompt, user_prompt)
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
            "- Treat electricity price/cost sensitivity as a real household concern. Price-aware scheduling, lower price-weighted cost, and no-disruption energy savings should raise energy_score and can raise overall satisfaction when comfort and required services are preserved.\n"
            "- For price_sensitive, price_driven, commuter, EV-owner, or grid/cooperative users, a high score should usually require the plan to show cost/price awareness or a clear reason why price optimization was safely limited.\n"
            "- For low_incentive or price_indifferent users, satisfaction should still be dominated by comfort, routine smoothness, and whether the controller stayed low-pressure. However, modest no-disruption cost savings may be credited; do not invent a financial pitch if it is absent.\n"
            "- For low_incentive or price_indifferent users, weak energy/VPP impact may lower energy_score or vpp_score, but should not lower overall satisfaction by itself when comfort, consent, and routine were preserved.\n"
            "- A concrete, truthful controller explanation is part of user experience. If the reason clearly connects actions to comfort, appliance completion, EV/hot-water readiness, VPP-window avoidance, and cost/price benefit, you may score up to one point higher when outcomes are otherwise acceptable.\n"
            "- Do not let explanation quality excuse hard failures: skipped required tasks, missing emitted appliance actions, unmet EV/hot-water service, infeasible past-time commands, or comfort-boundary violations still require low scores.\n"
            "- If prior feedback or memory is provided, reward a controller that fixed the specific earlier complaint; penalize repeated unresolved complaints more strongly.\n"
            "- Event-level VPP success in this benchmark means no present non-AC appliance was scheduled or run inside the VPP window. Do not judge success by shed/cap targets, ratio, actual_shed, or daily energy alone.\n"
            "- If event-level VPP achieved is true, do not say the VPP event was missed in the comment. You may still mention limited savings, but call the appliance-avoidance criterion successful.\n"
            "- If the control plan says an appliance is fixed/non-DR-adjustable, do not treat that fixed operation as an agent scheduling violation.\n"
            "- For low-disruption or confirmation-required users, if comfort/consent/routine were preserved and only fixed non-DR appliances limited VPP, keep overall satisfaction separate from the VPP subscore.\n"
            "- If a required controllable task was skipped or an approved comfort boundary was exceeded, score harshly.\n\n"
            "Comment requirements:\n"
            "- Write a specific reason, not a generic label. Mention comfort, cost/price or energy, appliance service completion, VPP-window handling, and whether the explanation was convincing.\n"
            "- If score is below 4, include the concrete change that would improve the next event. If the controller corrected a prior issue, say that explicitly.\n\n"
            "Return a JSON object with EXACTLY these fields:\n"
            "  satisfaction_score (int 1-5): overall satisfaction\n"
            "  comfort_score (int 1-5): thermal comfort satisfaction\n"
            "  energy_score (int 1-5): satisfaction with energy usage / cost\n"
            "  vpp_score (int 1-5): satisfaction with VPP demand-response handling\n"
            "  satisfaction_label: one of very_satisfied/satisfied/neutral/dissatisfied/very_dissatisfied\n"
            "  comment (str <=240 chars): detailed reason and next-event improvement cue\n"
            + ("  zone_comfort_scores: {Core: X, Bottom: X, Middle: X, Top: X}\n" if zone_group_context else "")
            + "All score fields must be integers 1-5."
        )
        return self._call_json(system_prompt, user_prompt)


__all__ = ["RoleplayUserSimulator", "infer_observable_profile_from_answers"]
