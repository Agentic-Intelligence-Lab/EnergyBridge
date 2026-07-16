import json

import experiments.benchmark.family_runner as fr
from experiments.benchmark.family_runner import (
    _FamilyLoop,
    _adaptability_diagnostics,
    _accepted_effective_vpp_penalty,
    _agent_memory_is_cost_grid_oriented,
    _agent_memory_is_protective,
    _agent_onboarding_questions,
    _agent_preference_memory_prompt_text,
    _agent_repair_ev_service_actions,
    _count_action_service_changes,
    _eb_acceptance_learning_adjustment,
    _evaluate_vpp_plan_acceptance_gate,
    _fallback_agent_onboarding_questionnaire,
    _fallback_plan_after_vpp_rejection,
    _fixed_services_modified,
    _household_member_min_preferred_max_c,
    _ensure_price_sensitive_reason_estimate,
    _init_agent_preference_memory,
    _learned_efficiency_floor_c,
    _manual_no_vpp_user_plan,
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


def test_agent_ev_service_repair_fills_non_vpp_window() -> None:
    appliances = {
        "ev": {
            "present": True,
            "charger_kw": 7.4,
            "efficiency": 0.92,
            "daily_drive_kwh": 18.0,
            "arrival_h": 18.0,
            "departure_h": 7.5,
        }
    }
    event = {"id": "vpp1", "trigger_h": 18.0, "end_h": 19.0}

    repaired, changed = _agent_repair_ev_service_actions(
        {},
        appliance_config=appliances,
        event=event,
        hod=16.5,
    )

    assert changed
    assert repaired["ev_mode"] == "smart"
    assert repaired["ev_charge_start_h"] >= 19.0
    assert fr._ev_service_window_errors(repaired, appliances, vpp_event=event) == []
    assert not any(
        str(item).startswith("ev:")
        for item in fr._vpp_appliance_conflicts(repaired, appliances, event)
    )


def test_household_member_min_preferred_max_uses_strictest_member() -> None:
    household_persona = {
        "meta": {"persona_type": "multi_user_household"},
        "acceptance_profiles": [
            {"appliances": {"ac": {"setpoint_preferred_max_c": 26.0}}},
            {"appliances": {"ac": {"setpoint_preferred_max_c": 25.0}}},
            {"appliances": {"ac": {"setpoint_preferred_max_c": 25.5}}},
        ],
    }

    assert _household_member_min_preferred_max_c(household_persona) == 25.0


def test_vpp_rejection_fallback_restores_ordinary_routine_not_default_vpp_avoidance(monkeypatch) -> None:
    monkeypatch.setenv("ENERGYBRIDGE_ROLEPLAY_MANUAL_OVERRIDE", "0")
    event = {"id": "vpp1", "trigger_h": 18.0, "end_h": 19.0, "day": 1}
    appliances = {
        "ac": {"setpoint_preferred_min_c": 24.0, "setpoint_preferred_max_c": 26.0},
        "washer": {
            "present": True,
            "preferred_h": 18.0,
            "earliest_h": 8.0,
            "latest_h": 22.0,
            "duration_h": 1.0,
        },
        "water_heater": {
            "present": True,
            "normal_start_h": 18.0,
            "normal_end_h": 20.0,
            "normal_temp_c": 60.0,
        },
        "ev": {"present": True, "arrival_h": 18.0, "departure_h": 7.5},
    }
    default_plan = {
        "setpoint": 27.5,
        "appliance_actions": {
            "washer_start_h": 20.0,
            "water_heater_preheat": True,
            "water_heater_preheat_start_h": 16.0,
            "water_heater_preheat_end_h": 17.0,
            "ev_mode": "smart",
            "ev_charge_start_h": 19.0,
            "ev_charge_end_h": 7.5,
        },
    }

    fallback = _fallback_plan_after_vpp_rejection(
        default_plan=default_plan,
        current_setpoint=28.0,
        event=event,
        persona_config={"tags": {"comfort": "normal_comfort"}},
        appliance_config=appliances,
        current_hod=18.0,
    )
    actions = fallback["appliance_actions"]

    assert fallback["fallback_after_vpp_rejection"] is True
    assert fallback["fallback_is_vpp_aware"] is False
    assert fallback["fallback_manual_rebound"] is True
    assert fallback["objective_source"] == "vpp_acceptance_gate_user_rejected_ordinary_routine"
    assert fallback["setpoint"] == 24.0
    assert actions["washer_start_h"] == 18.0
    assert actions["water_heater_preheat_start_h"] == 18.0
    assert actions["water_heater_preheat_temp_c"] >= 62.0
    assert actions["ev_mode"] == "normal"
    assert actions["ev_charge_start_h"] == 18.0


def test_vpp_rejection_can_use_roleplay_manual_override(monkeypatch) -> None:
    captured = {}

    def fake_manual_override_llm(system_prompt, user_prompt):
        captured["system_prompt"] = system_prompt
        captured["user_prompt"] = user_prompt
        return (
            {
                "manual_override": {
                    "setpoint": 24.0,
                    "appliance_actions": {
                        "water_heater_preheat": True,
                        "water_heater_preheat_start_h": 18.0,
                        "water_heater_preheat_end_h": 20.0,
                        "water_heater_preheat_temp_c": 60.0,
                        "ev_mode": "normal",
                        "ev_charge_start_h": 18.5,
                        "ev_charge_end_h": 7.5,
                    },
                    "reason": "I rejected the event and restored comfort, hot water, and normal EV charging.",
                },
                "override_type": "mixed",
                "user_comment": "I want the house cool, hot water ready, and the car charged as usual.",
            },
            {"latency_seconds": 0.1, "token_usage": {"prompt_tokens": 10, "completion_tokens": 5}},
        )

    monkeypatch.setattr(fr, "_call_roleplay_manual_override_llm", fake_manual_override_llm)
    event = {"id": "vpp1", "trigger_h": 18.0, "end_h": 19.0, "day": 1}
    appliances = {
        "ac": {"setpoint_preferred_min_c": 24.0, "setpoint_preferred_max_c": 26.0},
        "water_heater": {"present": True, "normal_start_h": 18.0, "normal_end_h": 20.0},
        "ev": {"present": True, "arrival_h": 18.5, "departure_h": 7.5},
    }
    fallback = _fallback_plan_after_vpp_rejection(
        default_plan={
            "setpoint": 28.0,
            "appliance_actions": {"ev_mode": "smart", "ev_charge_start_h": 21.0, "ev_charge_end_h": 7.5},
            "objective_source": "rule_milp",
        },
        current_setpoint=28.0,
        event=event,
        persona_config={"id": "manual_user", "tags": {"comfort": "temp_sensitive"}},
        appliance_config=appliances,
        current_hod=18.0,
    )

    assert fallback["fallback_mode"] == "roleplay_manual_override"
    assert fallback["fallback_is_vpp_aware"] is False
    assert fallback["fallback_manual_rebound"] is True
    assert fallback["setpoint"] == 24.0
    assert fallback["appliance_actions"]["water_heater_preheat_start_h"] == 18.0
    assert fallback["appliance_actions"]["ev_mode"] == "normal"
    assert fallback["manual_override_source"] == "roleplay_llm"
    assert "rule_milp" not in captured["user_prompt"]


def test_manual_rejection_override_uses_strict_household_comfort_for_caregiving() -> None:
    persona = {
        "meta": {"persona_type": "multi_user_household"},
        "tags": {"comfort": "temp_sensitive", "role": "caregiver"},
        "acceptance_profiles": [
            {"appliances": {"ac": {"setpoint_preferred_max_c": 26.0}}},
            {"appliances": {"ac": {"setpoint_preferred_max_c": 25.0}}},
        ],
    }
    appliances = {
        "ac": {"setpoint_preferred_min_c": 24.0, "setpoint_preferred_max_c": 27.0},
        "water_heater": {"present": True, "normal_start_h": 18.0, "normal_end_h": 20.0},
    }

    plan = _manual_no_vpp_user_plan(
        persona_config=persona,
        appliance_config=appliances,
        current_setpoint=29.0,
        vpp_rejection_event={"id": "vpp1", "trigger_h": 18.0, "end_h": 19.0, "day": 1},
        current_hod=18.0,
    )

    assert plan["setpoint"] == 24.0
    assert plan["appliance_actions"]["water_heater_preheat_start_h"] == 18.0


def test_accepted_effective_vpp_penalty_counts_service_misses() -> None:
    appliances = {
        "washer": {"present": True, "power_kw": 1.5, "duration_h": 1.0},
        "water_heater": {"present": True, "rated_kw": 2.0, "normal_start_h": 18.0, "normal_end_h": 20.0},
        "ev": {"present": True, "daily_drive_kwh": 8.0},
    }
    event_log = [
        {
            "day": 1,
            "appliance_summary": {
                "washer": {"present": True, "completed": False, "skipped": False},
                "water_heater": {"present": True, "ready_at_bath": False},
                "ev": {"present": True, "target_reached": True},
            },
        }
    ]

    penalty = _accepted_effective_vpp_penalty(
        appliance_config=appliances,
        event_log=event_log,
        sim_days=1,
    )

    assert penalty["miss_count"] == 2
    assert penalty["penalty_kwh"] == 5.5


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


def test_vpp_acceptance_gate_caps_unexplained_strategy_without_method_bias() -> None:
    event, appliances, default_plan, rule_plan = _gate_fixture()
    persona = {
        "id": "unexplained_check",
        "tags": {"price": "price_sensitive", "control": "high_trust_auto", "grid_value": "high_flex"},
        "preferences": {"scoring_weights": {"comfort": 0.25, "energy": 0.40, "vpp": 0.35}},
    }
    unexplained_plan = {
        "setpoint": 26.0,
        "appliance_actions": dict(rule_plan["appliance_actions"]),
        "reason": "objective=0.42 solver dispatch",
        "objective_source": "technical_optimizer",
    }

    gates = [
        _evaluate_vpp_plan_acceptance_gate(
            method=method,
            persona_config=persona,
            appliance_config=appliances,
            event=event,
            proposed_plan=unexplained_plan,
            default_plan=default_plan,
            rule_milp_plan=rule_plan,
            user_preference_text="I can cooperate if the plan is clear and still comfortable.",
        )
        for method in ("rule_milp", "mpc_dynamic", "EnergyBridge")
    ]

    assert {gate["acceptance_probability"] for gate in gates} == {0.25}
    assert all(
        "no_user_facing_explanation_acceptance_cap<=0.250" in gate["factors"]
        for gate in gates
    )


def test_household_vpp_acceptance_uses_veto_aware_member_aggregation() -> None:
    event, appliances, default_plan, rule_plan = _gate_fixture()
    household = {
        "id": "household_gate_check",
        "tags": {"price": "mixed_price_sensitive", "control": "household_consensus"},
        "preferences": {"scoring_weights": {"comfort": 0.45, "energy": 0.30, "vpp": 0.25}},
        "meta": {"persona_type": "multi_user_household_independent_roleplay"},
        "members": [{"member_id": "bill_payer"}, {"member_id": "elder"}],
        "acceptance_profiles": [
            {
                "member_id": "bill_payer",
                "persona_id": "price_member",
                "household_role": "bill payer",
                "decision_weight": 1.0,
                "tags": {"price": "price_sensitive", "control": "high_trust_auto", "grid_value": "high_flex"},
                "preferences": {"scoring_weights": {"comfort": 0.25, "energy": 0.45, "vpp": 0.30}},
                "schedule": {"occupancy_pattern": "commuter"},
                "calendar": {"days": [{"day": 1, "events": [], "constraints": {}}]},
                "appliances": {"ac": {"setpoint_preferred_max_c": 27.5, "temp_tolerance_c": 1.0}},
            },
            {
                "member_id": "elder",
                "persona_id": "comfort_member",
                "household_role": "comfort-sensitive elder at home",
                "decision_weight": 1.3,
                "tags": {"comfort": "temp_sensitive", "control": "confirm_required", "price": "low_incentive"},
                "preferences": {"scoring_weights": {"comfort": 0.70, "energy": 0.15, "vpp": 0.15}},
                "schedule": {"vulnerable_members": ["elder"]},
                "calendar": {
                    "days": [
                        {
                            "day": 1,
                            "events": [{"title": "Dinner at home", "start_h": 18.0, "end_h": 19.0, "location": "home"}],
                            "constraints": {"vulnerable_member_home": True, "bath_shower_h": 20.5},
                        }
                    ]
                },
                "appliances": {"ac": {"setpoint_preferred_max_c": 25.5, "temp_tolerance_c": 0.5}},
            },
        ],
    }
    warm_plan = {
        "setpoint": 27.2,
        "appliance_actions": dict(rule_plan["appliance_actions"]),
        "reason": "Balanced VPP plan: shift washer and water heater, keep comfort reasonable, and restore after event.",
        "strategy_explanation": {"natural_language": "Explains comfort, washer, water heater, and VPP tradeoff."},
    }

    gate = _evaluate_vpp_plan_acceptance_gate(
        method="agent",
        persona_config=household,
        appliance_config=appliances,
        event=event,
        proposed_plan=warm_plan,
        default_plan=default_plan,
        rule_milp_plan=rule_plan,
        user_preference_text="The household wants savings, but elder comfort has veto priority.",
    )

    consent = gate["household_consent"]
    elder = next(item for item in consent["members"] if item["member_id"] == "elder")
    payer = next(item for item in consent["members"] if item["member_id"] == "bill_payer")

    assert gate["version"] == "household_vpp_plan_acceptance_gate_v1_veto_weighted"
    assert elder["impact"]["is_key_affected_member"] is True
    assert elder["acceptance_probability"] < payer["acceptance_probability"]
    assert gate["acceptance_probability"] <= consent["member_weighted_mean"]
    assert "household_veto_aware_weighted_consent" in gate["factors"]


def test_household_vpp_acceptance_remains_method_agnostic_for_same_strategy() -> None:
    event, appliances, default_plan, rule_plan = _gate_fixture()
    household = {
        "id": "household_method_blind",
        "tags": {"control": "household_consensus"},
        "preferences": {"scoring_weights": {"comfort": 0.4, "energy": 0.3, "vpp": 0.3}},
        "meta": {"persona_type": "multi_user_household"},
        "members": [{"member_id": "a"}, {"member_id": "b"}],
        "acceptance_profiles": [
            {
                "member_id": "a",
                "persona_id": "a",
                "decision_weight": 1.0,
                "tags": {"price": "price_sensitive", "control": "suggestion_first"},
                "preferences": {"scoring_weights": {"comfort": 0.35, "energy": 0.35, "vpp": 0.30}},
                "calendar": {"days": [{"day": 1, "events": [], "constraints": {}}]},
                "appliances": {"ac": {"setpoint_preferred_max_c": 26.0, "temp_tolerance_c": 1.0}},
            },
            {
                "member_id": "b",
                "persona_id": "b",
                "decision_weight": 1.0,
                "tags": {"comfort": "normal_comfort", "control": "suggestion_first"},
                "preferences": {"scoring_weights": {"comfort": 0.40, "energy": 0.30, "vpp": 0.30}},
                "calendar": {"days": [{"day": 1, "events": [], "constraints": {}}]},
                "appliances": {"ac": {"setpoint_preferred_max_c": 26.0, "temp_tolerance_c": 1.0}},
            },
        ],
    }
    plan = {
        "setpoint": 26.0,
        "appliance_actions": dict(rule_plan["appliance_actions"]),
        "reason": "Comfort-safe VPP plan: washer and water heater avoid the VPP window.",
        "strategy_explanation": {"natural_language": "Comfort, washer, water heater, and VPP impact are explained."},
    }

    gates = [
        _evaluate_vpp_plan_acceptance_gate(
            method=method,
            persona_config=household,
            appliance_config=appliances,
            event=event,
            proposed_plan=plan,
            default_plan=default_plan,
            rule_milp_plan=rule_plan,
            user_preference_text="Balanced household plan.",
        )
        for method in ("EnergyBridge", "mpc_dynamic", "rule_milp")
    ]

    assert len({gate["acceptance_probability"] for gate in gates}) == 1
    assert len({gate["stable_draw"] for gate in gates}) == 1


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

    assert gate["acceptance_probability"] >= 0.88
    assert "fixed_routine_consent_preserved_floor=0.880" in gate["factors"]
    assert "roleplay_residual_refusal_cap<=0.880" in gate["factors"]


def test_high_quality_explained_gate_still_uses_probability_draw() -> None:
    event, appliances, default_plan, rule_plan = _gate_fixture()
    persona = {
        "id": "draw_above_cap",
        "tags": {"price": "price_sensitive", "control": "high_trust_auto", "grid_value": "high_flex"},
        "preferences": {"scoring_weights": {"comfort": 0.25, "energy": 0.40, "vpp": 0.35}},
    }
    plan = {
        "setpoint": 25.8,
        "appliance_actions": dict(rule_plan["appliance_actions"]),
        "reason": (
            "Comfort-safe personalized VPP plan: washer and water heater avoid the VPP window, "
            "EV readiness and hot water are protected, and the household gets a clear price benefit."
        ),
        "strategy_explanation": {
            "natural_language": (
                "This keeps comfort protected, moves required services outside 18:00-19:00, "
                "preserves hot-water readiness, and explains the price benefit."
            )
        },
    }

    gate = _evaluate_vpp_plan_acceptance_gate(
        method="EnergyBridge",
        persona_config=persona,
        appliance_config=appliances,
        event=event,
        proposed_plan=plan,
        default_plan=default_plan,
        rule_milp_plan=rule_plan,
        user_preference_text="I usually cooperate when the plan is clear and service-safe.",
    )

    assert gate["acceptance_probability"] <= 0.88
    assert gate["high_confidence_accept"] is False
    assert gate["accepted"] == (gate["stable_draw"] <= gate["acceptance_probability"])


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
