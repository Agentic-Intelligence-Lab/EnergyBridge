import json

import experiments.benchmark.family_runner as fr
from experiments.benchmark.family_runner import (
    _FamilyLoop,
    _adaptability_diagnostics,
    _agent_memory_is_cost_grid_oriented,
    _agent_memory_is_protective,
    _agent_onboarding_questions,
    _agent_preference_memory_prompt_text,
    _count_action_service_changes,
    _eb_acceptance_learning_adjustment,
    _evaluate_vpp_plan_acceptance_gate,
    _fallback_agent_onboarding_questionnaire,
    _fixed_services_modified,
    _ensure_price_sensitive_reason_estimate,
    _init_agent_preference_memory,
    _learned_efficiency_floor_c,
    _preserve_fixed_routine_actions,
    _requested_skip_devices,
    _roleplay_middle_acceptance_floor,
    _vpp_plan_intrusion_metrics,
)


def test_requested_skip_devices_returns_only_explicit_true_flags() -> None:
    actions = {
        "washer_skip": True,
        "dishwasher_skip": False,
        "dryer_skip": None,
    }
    assert _requested_skip_devices(actions) == ["washer"]


def test_agent_onboarding_questionnaire_is_short_and_does_not_leak_hidden_prompt(monkeypatch, tmp_path) -> None:
    persona = {
        "id": "leak_check",
        "tags": {"control": "confirm_required", "schedule": "irregular", "comfort": "normal_comfort"},
        "preferences": {"scoring_weights": {"comfort": 0.4, "energy": 0.3, "vpp": 0.3}},
        "schedule": {"schedule_variability_h": 3.0, "returns_home_h": 19.0},
        "appliances": {
            "ac": {"setpoint_preferred_min_c": 24.0, "setpoint_preferred_max_c": 26.0, "temp_tolerance_c": 1.0},
            "washer": {"present": True, "dr_adjustable": False},
        },
        "llm_prompts": {"system_prompt": "SECRET_RAW_PERSONA_PROMPT"},
    }
    questionnaire = _fallback_agent_onboarding_questionnaire(persona)

    assert len(_agent_onboarding_questions()) == 4
    assert questionnaire["question_count"] == 4
    assert len(questionnaire["answers"]) == 4
    serialized_questionnaire = json.dumps(questionnaire, ensure_ascii=False)
    assert "SECRET_RAW_PERSONA_PROMPT" not in serialized_questionnaire
    assert "confirm_required" not in serialized_questionnaire

    monkeypatch.setattr(fr, "_run_agent_onboarding_questionnaire", lambda _persona: questionnaire)
    loop = _FamilyLoop()
    _init_agent_preference_memory(loop, tmp_path, method="agent", persona_config=persona)
    prompt = _agent_preference_memory_prompt_text(loop)

    assert "onboarding_questionnaire" in prompt
    assert "SECRET_RAW_PERSONA_PROMPT" not in prompt
    assert "confirm_required" not in prompt


def test_eb_acceptance_learning_grows_after_good_accepted_feedback() -> None:
    no_history = _eb_acceptance_learning_adjustment(method="agent", past_events=[])
    good_history = [
        {
            "score": 4,
            "comfort_score": 4,
            "target_achieved": True,
            "vpp_acceptance_gate": {"accepted": True},
        }
        for _ in range(5)
    ]
    learned = _eb_acceptance_learning_adjustment(method="agent", past_events=good_history)

    assert no_history["adjustment"] == 0.0
    assert learned["positive_streak"] == 5
    assert learned["adjustment"] >= 0.35


def test_agent_memory_classifies_roleplay_llm_natural_strategy_bias() -> None:
    loop = _FamilyLoop()
    loop.agent_preference_memory = {
        "onboarding_questionnaire": {
            "inferred_profile": {
                "strategy_bias": "save money first when comfort is preserved, especially during weekday evening peaks",
                "cost_grid_priority": "high",
                "comfort_priority": "medium",
                "automation_preference": "suggestion_first",
            }
        }
    }
    assert _agent_memory_is_cost_grid_oriented(loop)
    assert not _agent_memory_is_protective(loop)

    loop.agent_preference_memory["onboarding_questionnaire"]["inferred_profile"] = {
        "strategy_bias": "irregular_calendar_confirm_before_vpp",
        "cost_grid_priority": "medium",
        "comfort_priority": "medium",
        "calendar_routine_sensitivity": "high",
        "automation_preference": "ask_before_vpp_specific_changes",
    }
    assert _agent_memory_is_protective(loop)


def test_missing_fixed_actions_mean_keep_daily_plan_for_vpp_gate() -> None:
    appliances = {
        "ac": {"setpoint_preferred_max_c": 25.5, "temp_tolerance_c": 1.0},
        "washer": {"present": True, "shiftable": False, "dr_adjustable": False},
        "water_heater": {"present": True, "dr_adjustable": False},
    }
    default_actions = {
        "washer_start_h": 9.0,
        "washer_skip": False,
        "water_heater_preheat": True,
        "water_heater_preheat_start_h": 5.0,
        "water_heater_preheat_end_h": 7.0,
        "water_heater_preheat_temp_c": 63.0,
    }

    assert _count_action_service_changes({}, default_actions) == (0, [])
    assert _fixed_services_modified({}, default_actions, appliances) == []
    assert _fixed_services_modified({"washer_start_h": 18.0}, default_actions, appliances) == ["washer"]

    intrusion = _vpp_plan_intrusion_metrics(
        proposed_plan={"setpoint": 25.0, "appliance_actions": {}, "reason": "comfort routine water VPP"},
        default_plan={"setpoint": 25.5, "appliance_actions": default_actions},
        persona_config={},
        appliance_config=appliances,
        event={"id": "vpp1", "trigger_h": 18.0, "end_h": 19.0},
    )
    assert intrusion["changed_service_count"] == 0
    assert intrusion["fixed_services_modified"] == []
    assert intrusion["vpp_conflicts"] == []

    conflict_actions = dict(default_actions)
    conflict_actions["water_heater_preheat_start_h"] = 18.0
    conflict_actions["water_heater_preheat_end_h"] = 20.0
    fixed_overlap = _vpp_plan_intrusion_metrics(
        proposed_plan={"setpoint": 25.0, "appliance_actions": {}, "reason": "comfort routine water VPP"},
        default_plan={"setpoint": 25.5, "appliance_actions": conflict_actions},
        persona_config={},
        appliance_config=appliances,
        event={"id": "vpp1", "trigger_h": 18.0, "end_h": 19.0},
    )
    assert fixed_overlap["vpp_conflicts"] == []

    flexible_appliances = dict(appliances)
    flexible_appliances["water_heater"] = {"present": True, "dr_adjustable": True}
    flexible_conflict = _vpp_plan_intrusion_metrics(
        proposed_plan={"setpoint": 25.0, "appliance_actions": conflict_actions, "reason": "shiftable water VPP"},
        default_plan={"setpoint": 25.5, "appliance_actions": default_actions},
        persona_config={},
        appliance_config=flexible_appliances,
        event={"id": "vpp1", "trigger_h": 18.0, "end_h": 19.0},
    )
    assert flexible_conflict["vpp_conflicts"]


def test_preserve_fixed_routine_actions_restores_only_non_dr_services() -> None:
    appliances = {
        "washer": {"present": True, "shiftable": False, "dr_adjustable": False},
        "dishwasher": {"present": True, "shiftable": True, "dr_adjustable": True},
        "water_heater": {"present": True, "dr_adjustable": False},
    }
    default = {
        "washer_start_h": 19.0,
        "washer_skip": False,
        "dishwasher_start_h": 21.0,
        "dishwasher_skip": False,
        "water_heater_preheat": True,
        "water_heater_preheat_start_h": 18.0,
        "water_heater_preheat_end_h": 20.0,
        "water_heater_preheat_temp_c": 60.0,
    }
    proposed = {
        "washer_start_h": 14.0,
        "washer_skip": False,
        "dishwasher_start_h": 16.0,
        "dishwasher_skip": False,
        "water_heater_preheat": True,
        "water_heater_preheat_start_h": 16.5,
        "water_heater_preheat_end_h": 18.0,
        "water_heater_preheat_temp_c": 63.0,
    }

    repaired, preserved = _preserve_fixed_routine_actions(proposed, default, appliances)

    assert preserved == ["washer", "water_heater"]
    assert repaired["washer_start_h"] == 19.0
    assert repaired["water_heater_preheat_start_h"] == 18.0
    assert repaired["water_heater_preheat_end_h"] == 20.0
    assert repaired["dishwasher_start_h"] == 16.0


def test_price_sensitive_reason_requires_quantified_impact() -> None:
    persona = {"tags": {"price": "price_sensitive"}}
    appliances = {
        "washer": {"present": True, "power_kw": 1.5},
        "dishwasher": {"present": True, "power_kw": 1.2},
        "water_heater": {"present": True, "rated_kw": 2.0},
    }
    event = {"id": "vpp1", "trigger_h": 18.0, "end_h": 19.0}

    repaired = _ensure_price_sensitive_reason_estimate(
        "Shifted flexible loads away from the event.",
        persona,
        appliances,
        event,
        demand_kw=3.077,
    )
    assert "est. shifted ~4.7kW" in repaired

    already_quantified = _ensure_price_sensitive_reason_estimate(
        "Shifted about 3.0kW away from the event.",
        persona,
        appliances,
        event,
        demand_kw=3.077,
    )
    assert already_quantified == "Shifted about 3.0kW away from the event."


def test_learned_efficiency_floor_requires_positive_history() -> None:
    persona = {
        "tags": {"comfort": "normal_comfort", "price": "price_sensitive", "control": "suggestion_first"},
        "schedule": {},
    }
    events = [
        {"score": 4, "comfort_score": 4, "comment": "Comfort stayed within range."},
        {"score": 4, "comfort_score": 4, "comment": "Comfort stayed reasonable."},
    ]

    assert _learned_efficiency_floor_c(
        events,
        persona,
        default_sp_c=25.0,
        preferred_max_c=26.0,
        vpp_active=False,
    ) == 25.5
    assert _learned_efficiency_floor_c(
        events,
        persona,
        default_sp_c=25.0,
        preferred_max_c=26.0,
        vpp_active=True,
    ) == 26.0


def test_learned_efficiency_floor_disabled_for_confirmation_users() -> None:
    persona = {
        "tags": {"comfort": "normal_comfort", "price": "needs_explanation", "control": "confirm_required"},
        "schedule": {},
    }
    events = [
        {"score": 4, "comfort_score": 4, "comment": "Comfort stayed within range."},
        {"score": 4, "comfort_score": 4, "comment": "Comfort stayed reasonable."},
    ]

    assert _learned_efficiency_floor_c(
        events,
        persona,
        default_sp_c=25.0,
        preferred_max_c=26.0,
        vpp_active=True,
    ) is None


def _gate_fixture():
    event = {"id": "vpp1", "trigger_h": 18.0, "end_h": 19.0, "day": 1}
    appliances = {
        "ac": {
            "present": True,
            "setpoint_preferred_min_c": 24.0,
            "setpoint_preferred_max_c": 26.0,
            "temp_tolerance_c": 1.0,
        },
        "washer": {
            "present": True,
            "shiftable": True,
            "dr_adjustable": True,
        },
        "water_heater": {
            "present": True,
            "dr_adjustable": True,
        },
        "ev": {"present": False},
    }
    default_plan = {
        "setpoint": 25.5,
        "appliance_actions": {"washer_start_h": 18.0, "washer_skip": False},
        "reason": "normal no-VPP daily plan",
    }
    rule_plan = {
        "setpoint": 40.0,
        "appliance_actions": {
            "washer_start_h": 19.0,
            "washer_skip": False,
            "water_heater_preheat": True,
            "water_heater_preheat_start_h": 16.0,
            "water_heater_preheat_end_h": 17.5,
            "water_heater_preheat_temp_c": 63.0,
        },
        "reason": "rule_milp cost-min VPP plan",
    }
    return event, appliances, default_plan, rule_plan


def test_vpp_acceptance_gate_uses_persona_json_preferences() -> None:
    event, appliances, default_plan, rule_plan = _gate_fixture()
    economic = {
        "id": "econ",
        "tags": {"price": "price_sensitive", "control": "high_trust_auto", "grid_value": "high_flex"},
        "preferences": {"scoring_weights": {"comfort": 0.25, "energy": 0.4, "vpp": 0.35}, "vpp_override_prob": 0.05},
    }
    comfort = {
        "id": "comfort",
        "tags": {"comfort": "temp_sensitive", "control": "confirm_required", "price": "low_incentive"},
        "preferences": {"scoring_weights": {"comfort": 0.7, "energy": 0.15, "vpp": 0.15}, "vpp_override_prob": 0.75},
        "calendar": {
            "days": [
                {
                    "day": 1,
                    "summary": "Home during the evening VPP event.",
                    "events": [{"title": "Dinner at home", "start_h": 18.0, "end_h": 19.0, "location": "home"}],
                    "constraints": {"home_arrival_h": 18.0},
                }
            ]
        },
    }
    energy_leaning_plan = {
        "setpoint": 27.2,
        "appliance_actions": dict(rule_plan["appliance_actions"]),
        "reason": (
            "Shift washer and water heater away from VPP, keep comfort reasonable, "
            "estimate savings, and restore after the event."
        ),
        "strategy_explanation": {
            "natural_language": (
                "Comfort stays reasonable; washer and water heater avoid VPP; restore after event."
            )
        },
    }

    econ_gate = _evaluate_vpp_plan_acceptance_gate(
        method="agent",
        persona_config=economic,
        appliance_config=appliances,
        event=event,
        proposed_plan=energy_leaning_plan,
        default_plan=default_plan,
        rule_milp_plan=rule_plan,
        user_preference_text="Energy-aware: use the warmest still-comfortable AC setting and shift washer.",
    )
    comfort_gate = _evaluate_vpp_plan_acceptance_gate(
        method="agent",
        persona_config=comfort,
        appliance_config=appliances,
        event=event,
        proposed_plan=energy_leaning_plan,
        default_plan=default_plan,
        rule_milp_plan=rule_plan,
        user_preference_text="Comfort first: keep AC at or below 26.0C and protect routine.",
    )

    assert econ_gate["acceptance_probability"] > comfort_gate["acceptance_probability"]
    assert econ_gate["adaptability_diagnostics"]["persona_mode"] == "economic_grid_oriented"
    assert comfort_gate["adaptability_diagnostics"]["persona_mode"] == "comfort_calendar_protective"


def test_vpp_acceptance_gate_is_method_agnostic_for_identical_plan() -> None:
    event, appliances, default_plan, rule_plan = _gate_fixture()
    persona = {
        "id": "method_agnostic",
        "tags": {"price": "price_sensitive", "control": "suggestion_first"},
        "preferences": {"scoring_weights": {"comfort": 0.35, "energy": 0.35, "vpp": 0.30}},
    }
    plan = {
        "setpoint": 26.2,
        "appliance_actions": dict(rule_plan["appliance_actions"]),
        "reason": "Balanced plan: shift flexible loads away from the VPP window and explain the tradeoff.",
        "strategy_explanation": {"natural_language": "Small VPP adjustment with comfort protected."},
    }

    probs = []
    draws = []
    for method in ("EnergyBridge", "mpc_dynamic", "rule_milp", "rl_ppo_pref_v2", "custom_label"):
        gate = _evaluate_vpp_plan_acceptance_gate(
            method=method,
            persona_config=persona,
            appliance_config=appliances,
            event=event,
            proposed_plan=plan,
            default_plan=default_plan,
            rule_milp_plan=rule_plan,
            user_preference_text="Balanced: save during the peak when comfort and tasks stay protected.",
        )
        probs.append(gate["acceptance_probability"])
        draws.append(gate["stable_draw"])

    assert len(set(probs)) == 1
    assert len(set(draws)) == 1


def test_vpp_acceptance_gate_caps_raw_policy_strategy_without_method_bias() -> None:
    event, appliances, default_plan, rule_plan = _gate_fixture()
    persona = {
        "id": "raw_policy_check",
        "tags": {"price": "price_sensitive", "control": "high_trust_auto", "grid_value": "high_flex"},
        "preferences": {"scoring_weights": {"comfort": 0.25, "energy": 0.40, "vpp": 0.35}},
    }
    raw_plan = {
        "setpoint": 27.2,
        "appliance_actions": dict(rule_plan["appliance_actions"]),
        "reason": "raw policy action vector with no human-readable fallback appliance commands",
        "objective_source": "policy_optimizer",
        "raw_policy_only": True,
    }

    gates = [
        _evaluate_vpp_plan_acceptance_gate(
            method=method,
            persona_config=persona,
            appliance_config=appliances,
            event=event,
            proposed_plan=raw_plan,
            default_plan=default_plan,
            rule_milp_plan=rule_plan,
            user_preference_text="I can cooperate if the plan is clear and still comfortable.",
        )
        for method in ("rl_ppo_pref_v2", "EnergyBridge", "mpc_dynamic")
    ]

    assert {gate["acceptance_probability"] for gate in gates} == {0.004}
    assert all(gate["intrusion"]["raw_policy_only"] for gate in gates)


def test_cautious_middle_floor_is_lower_than_price_cooperative_floor() -> None:
    cautious = {
        "tags": {"control": "confirm_required", "schedule": "irregular"},
        "preferences": {"scoring_weights": {"comfort": 0.45, "energy": 0.25, "vpp": 0.30}},
    }
    price = {
        "tags": {"price": "price_sensitive", "control": "suggestion_first"},
        "preferences": {"scoring_weights": {"comfort": 0.30, "energy": 0.40, "vpp": 0.30}},
    }

    assert _roleplay_middle_acceptance_floor(cautious) <= 0.10
    assert _roleplay_middle_acceptance_floor(price) >= 0.55


def test_cautious_gate_rewards_explicit_fixed_routine_preservation() -> None:
    event = {"id": "vpp1", "trigger_h": 18.0, "end_h": 19.0, "day": 1}
    appliances = {
        "ac": {"present": True, "setpoint_preferred_max_c": 26.0, "temp_tolerance_c": 1.0},
        "washer": {"present": True, "shiftable": False, "dr_adjustable": False},
        "water_heater": {"present": True, "dr_adjustable": False},
    }
    default_plan = {
        "setpoint": 26.0,
        "appliance_actions": {
            "washer_start_h": 19.0,
            "washer_skip": False,
            "water_heater_preheat": True,
            "water_heater_preheat_start_h": 18.0,
            "water_heater_preheat_end_h": 20.0,
            "water_heater_preheat_temp_c": 60.0,
        },
        "reason": "normal no-VPP fixed routine",
    }
    plan = {
        "setpoint": 26.0,
        "appliance_actions": dict(default_plan["appliance_actions"]),
        "reason": "Comfort-safe VPP support; preserved fixed routine(s): washer, water_heater.",
        "fixed_routine_preserved_for_consent": ["washer", "water_heater"],
    }
    persona = {
        "id": "cautious_fixed",
        "tags": {"control": "confirm_required", "schedule": "irregular"},
        "preferences": {"scoring_weights": {"comfort": 0.45, "energy": 0.25, "vpp": 0.30}},
    }

    gate = _evaluate_vpp_plan_acceptance_gate(
        method="any",
        persona_config=persona,
        appliance_config=appliances,
        event=event,
        proposed_plan=plan,
        default_plan=default_plan,
        rule_milp_plan={"setpoint": 40.0, "appliance_actions": {}},
        user_preference_text="Keep comfort and fixed routines protected; ask before changes.",
    )

    assert gate["acceptance_probability"] >= 0.93
    assert "fixed_routine_consent_preserved_floor=0.930" in gate["factors"]


def test_adaptability_diagnostics_reward_comfort_divergence_from_rule_milp() -> None:
    event, appliances, default_plan, rule_plan = _gate_fixture()
    comfort = {
        "id": "comfort",
        "tags": {"comfort": "temp_sensitive", "control": "confirm_required"},
        "preferences": {"scoring_weights": {"comfort": 0.7, "energy": 0.15, "vpp": 0.15}},
        "calendar": {
            "days": [
                {
                    "day": 1,
                    "summary": "Arrives home during VPP.",
                    "events": [{"title": "Arrive home", "start_h": 18.0, "end_h": 19.0, "location": "home"}],
                    "constraints": {"home_arrival_h": 18.0},
                }
            ]
        },
    }
    comfort_plan = {
        "setpoint": 26.0,
        "appliance_actions": {
            "washer_start_h": 19.0,
            "washer_skip": False,
            "water_heater_preheat": True,
            "water_heater_preheat_start_h": 16.0,
            "water_heater_preheat_end_h": 17.5,
            "water_heater_preheat_temp_c": 63.0,
        },
        "strategy_explanation": {"natural_language": "Comfort and routine stay protected; restore control is available."},
    }

    diag = _adaptability_diagnostics(
        method="agent",
        plan=comfort_plan,
        default_plan=default_plan,
        rule_milp_plan=rule_plan,
        persona_config=comfort,
        appliance_config=appliances,
        event=event,
        user_preference_text="Comfort first: keep AC at or below 26.0C and protect routine.",
    )

    assert diag["expected_adaptation"] == "calendar_fit_and_rule_milp_divergence"
    assert diag["calendar_fit"]["calendar_fit_score"] > 0.8
    assert diag["rule_milp_similarity"]["setpoint_delta_c"] >= 10.0
