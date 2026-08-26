import json

from experiments.benchmark.user_pref_scorer import (
    _calibrate_roleplay_score,
    _method_blind_observable_text,
    _observable_acceptance_judgement,
    build_vpp_preference_memory_notes,
    score_user_preference,
)


def _calibrated_score(
    probability: float,
    *,
    method: str = "controller_a",
    authored_score: float = 4.0,
    authored_comment: str = "Independent role-play feedback.",
    achieved: bool = True,
    mean_temp_c: float = 25.0,
    pmv_ok_fraction: float = 0.95,
    energy_kwh_per_day: float = 25.0,
    explained: bool = True,
    accepted: bool = True,
    live_judgement: bool = True,
) -> dict:
    gate = {
        "acceptance_probability": probability,
        "roleplay_source": "roleplay_llm" if live_judgement else "roleplay_model_unavailable_fail_closed",
        "fallback_source": None if live_judgement else "roleplay_model_unavailable_fail_closed",
        "accepted": accepted,
        "intrusion": {
            "has_user_facing_explanation": explained,
            "comfort_excess_c": 0.0,
            "hvac_off": False,
            "weak_action_coverage": False,
            "raw_policy_only": False,
        },
        "strategy_quality": {"strategy_quality_score": 0.65},
        "adaptability_diagnostics": {
            "calendar_fit": {"calendar_fit_score": 0.70},
            "roleplay_preference_alignment": {"alignment_score": 0.60},
        },
    }
    return _calibrate_roleplay_score(
        {
            "score": authored_score,
            "comfort_score": 4,
            "energy_score": 4,
            "vpp_score": 4,
            "label": "satisfied",
            "comment": authored_comment,
            "source": "roleplay_llm",
        },
        persona={
            "id": "price_cooperative_household",
            "tags": {"cost": "price_sensitive"},
            "scoring_weights": {"comfort": 0.4, "energy": 0.3, "vpp": 0.3},
        },
        method=method,
        mean_temp_c=mean_temp_c,
        pmv_ok_fraction=pmv_ok_fraction,
        energy_kwh_per_day=energy_kwh_per_day,
        pref_min=24.0,
        pref_max=26.0,
        pref_tol=1.0,
        explanation_is_user_facing=explained,
        vpp_result_context={"achieved": achieved},
        policy_control_context={
            "method": method,
            "objective_source": f"{method}_implementation",
            "vpp_acceptance_gate": gate,
        },
        severe_service_issue=False,
    )


def _score_with_authored_feedback(
    monkeypatch,
    feedback: dict,
    *,
    profile: str = "adaptive_v2",
    **overrides,
) -> tuple[dict, dict]:
    import energybridge.llm.roleplay_user as roleplay_user

    captured: dict = {}

    class FakeRoleplayUserSimulator:
        def generate_feedback(self, **kwargs):
            captured.update(kwargs)
            return {"data": dict(feedback)}

    monkeypatch.setenv("USE_LLM", "true")
    monkeypatch.setenv("ENERGYBRIDGE_HARNESS_PROFILE", profile)
    monkeypatch.setattr(roleplay_user, "RoleplayUserSimulator", FakeRoleplayUserSimulator)
    kwargs = {
        "building": "family",
        "method": "controller_a",
        "mean_temp_c": 25.0,
        "pmv_ok_fraction": 0.95,
        "energy_kwh_per_day": 24.0,
        "agent_setpoint_c": 25.0,
        "event_index": 1,
        "persona": {
            "id": "audit_household",
            "scoring_weights": {"comfort": 0.5, "energy": 0.25, "vpp": 0.25},
            "appliances": {
                "ac": {
                    "present": True,
                    "setpoint_preferred_min_c": 24.0,
                    "setpoint_preferred_max_c": 26.0,
                    "temp_tolerance_c": 1.0,
                },
            },
        },
        "agent_reason": (
            "The household-facing plan keeps the requested temperature and routine visible."
        ),
    }
    kwargs.update(overrides)
    return score_user_preference(**kwargs), captured


def _authored_tuple(result: dict) -> tuple:
    return (
        result["score"],
        result["comfort_score"],
        result["energy_score"],
        result["vpp_score"],
        result["label"],
        result["comment"],
    )


def test_v2_preserves_authored_score_comment_and_audits_acceptance(monkeypatch) -> None:
    monkeypatch.setenv("ENERGYBRIDGE_HARNESS_PROFILE", "adaptive_v2")
    result = _calibrated_score(
        0.37,
        authored_score=2,
        authored_comment="I still need the promised cost and hot-water details.",
    )

    assert result["score"] == 2
    assert result["label"] == "dissatisfied"
    assert result["comment"] == "I still need the promised cost and hot-water details."
    audit = result["score_consistency_audit"]
    assert audit["score_was_posthoc_remapped"] is False
    assert audit["live_acceptance_judgement"] is True
    assert audit["acceptance_probability"] == 0.37
    assert audit["normalized_authored_rating"] == 0.25
    assert audit["signed_rating_minus_acceptance"] == -0.12


def test_v2_satisfaction_audit_is_method_blind_for_identical_evidence(monkeypatch) -> None:
    monkeypatch.setenv("ENERGYBRIDGE_HARNESS_PROFILE", "adaptive_v2")
    scores = {
        method: _calibrated_score(0.42, method=method, authored_score=3)["score"]
        for method in ("mpc_dynamic", "rl_ppo_pref_v2", "hema_agent", "EnergyBridge")
    }

    assert len(set(scores.values())) == 1


def test_v2_outcomes_do_not_posthoc_rewrite_the_authored_tuple(monkeypatch) -> None:
    monkeypatch.setenv("ENERGYBRIDGE_HARNESS_PROFILE", "adaptive_v2")
    good = _calibrated_score(0.42, authored_score=3)
    poor = _calibrated_score(
        0.42,
        authored_score=3,
        achieved=False,
        mean_temp_c=29.0,
        pmv_ok_fraction=0.2,
        energy_kwh_per_day=60.0,
    )

    assert good["score"] == poor["score"] == 3
    assert good["comment"] == poor["comment"]
    assert good["score_consistency_audit"]["score_was_posthoc_remapped"] is False


def test_v2_sampling_accept_or_reject_does_not_create_rating_discontinuity(monkeypatch) -> None:
    monkeypatch.setenv("ENERGYBRIDGE_HARNESS_PROFILE", "adaptive_v2")
    accepted = _calibrated_score(0.42, accepted=True)
    rejected = _calibrated_score(0.42, accepted=False)

    assert accepted["score"] == rejected["score"]


def test_v2_fallback_probability_is_not_treated_as_a_live_judgement(monkeypatch) -> None:
    monkeypatch.setenv("ENERGYBRIDGE_HARNESS_PROFILE", "adaptive_v2")
    result = _calibrated_score(0.86, authored_score=2, live_judgement=False)

    audit = result["score_consistency_audit"]
    assert audit["live_acceptance_judgement"] is False
    assert audit["acceptance_probability"] is None
    assert audit["signed_rating_minus_acceptance"] is None


def test_feedback_llm_receives_allowlisted_method_blind_acceptance_context(monkeypatch) -> None:
    import energybridge.llm.roleplay_user as roleplay_user

    captured = {}

    class FakeRoleplayUserSimulator:
        def generate_feedback(self, **kwargs):
            captured.update(kwargs)
            return {
                "data": {
                    "satisfaction_score": 3,
                    "comfort_score": 4,
                    "energy_score": 3,
                    "vpp_score": 3,
                    "satisfaction_label": "neutral",
                    "comment": "The proposal is acceptable, with some reservations.",
                }
            }

    monkeypatch.setenv("USE_LLM", "true")
    monkeypatch.setenv("ENERGYBRIDGE_HARNESS_PROFILE", "adaptive_v2")
    monkeypatch.setattr(roleplay_user, "RoleplayUserSimulator", FakeRoleplayUserSimulator)
    gate = {
        "method": "mpc_dynamic",
        "acceptance_probability": 0.43,
        "roleplay_decision": "accept",
        "accepted": True,
        "roleplay_source": "roleplay_llm",
        "roleplay_acceptance_reasoning": "The MPC proposal is tolerable despite limited detail.",
        "roleplay_evidence": [
            {
                "id": "E1",
                "source": "MPC plan",
                "fact": "MPC raises the setpoint briefly.",
                "effect": "requires_change",
            }
        ],
        "roleplay_probability_adjustments": [
            {
                "dimension": "explanation",
                "delta": -0.12,
                "evidence": "E1",
                "reason": "GPT model gave no household-specific benefit.",
            }
        ],
        "prompt_audit": {"system_prompt": "must never be copied"},
        "prompt_gate_metrics": {"model": "gpt-secret-model"},
        "intrusion": {"has_user_facing_explanation": False},
    }

    result = score_user_preference(
        building="family",
        method="mpc_dynamic",
        mean_temp_c=25.0,
        pmv_ok_fraction=0.9,
        energy_kwh_per_day=25.0,
        agent_setpoint_c=26.0,
        event_index=1,
        persona={
            "id": "ordinary_household",
            "scoring_weights": {"comfort": 0.5, "energy": 0.25, "vpp": 0.25},
        },
        agent_reason=(
            "MPC will keep comfort at 26 C, protect shower readiness, and return to the normal "
            "routine after 19:00."
        ),
        vpp_result_context={"achieved": True},
        policy_control_context={
            "method": "mpc_dynamic",
            "objective_source": "mpc_candidate_scoring_pdf_v15",
            "vpp_acceptance_gate": gate,
        },
    )

    assert result["source"] == "roleplay_llm"
    state = captured["selected_strategy"]
    judgement = state["pre_event_roleplay_acceptance"]
    assert judgement["acceptance_probability"] == 0.43
    assert judgement["roleplay_decision"] == "accept"
    assert judgement["accepted"] is True
    assert judgement["probability_adjustments"][0]["delta"] == -0.12
    prompt_payload = json.dumps(
        {
            "selected_strategy": state,
            "projected_control_plan": captured["projected_control_plan"],
        }
    ).lower()
    assert "mpc" not in prompt_payload
    assert "gpt" not in prompt_payload
    assert "prompt_audit" not in prompt_payload
    assert "prompt_gate_metrics" not in prompt_payload


def test_short_method_alias_redaction_does_not_corrupt_ordinary_words() -> None:
    cleaned = _method_blind_observable_text(
        "RL reduced load while the household's world stayed comfortable.",
        identities=("rl",),
    )

    assert cleaned == "controller reduced load while the household's world stayed comfortable."


def test_model_provider_identity_redaction_covers_common_compatible_apis() -> None:
    cleaned = _method_blind_observable_text(
        "OpenAI o3, DeepSeek-V3, Llama-4, DMXAPI, Mistral, and Grok-3 made the plan."
    ).lower()

    for identity in ("openai", "o3", "deepseek", "llama", "dmxapi", "mistral", "grok"):
        assert identity not in cleaned


def test_unavailable_gate_does_not_expose_fallback_prior_as_household_willingness() -> None:
    summary = _observable_acceptance_judgement({
        "acceptance_probability": 0.86,
        "roleplay_decision": "reject",
        "accepted": False,
        "roleplay_source": "roleplay_model_unavailable_fail_closed",
        "fallback_source": "roleplay_model_unavailable_fail_closed",
        "roleplay_acceptance_reasoning": "OpenAI o3 was unavailable.",
    })

    assert summary is not None
    assert summary["judgement_status"] == "unavailable"
    assert summary["acceptance_probability"] is None
    assert summary["roleplay_decision"] is None
    assert summary["accepted"] is None
    assert summary["evidence"] == []
    assert summary["probability_adjustments"] == []


def test_v2_missing_policy_service_is_feedback_evidence_not_a_hardcoded_score(
    monkeypatch,
) -> None:
    import energybridge.llm.roleplay_user as roleplay_user

    captured = {}

    class FakeRoleplayUserSimulator:
        def generate_feedback(self, **kwargs):
            captured.update(kwargs)
            return {
                "data": {
                    "satisfaction_score": 4,
                    "comfort_score": 4,
                    "energy_score": 3,
                    "vpp_score": 3,
                    "satisfaction_label": "satisfied",
                    "comment": "The routine completed, but I still want an explicit washer action next time.",
                }
            }

    monkeypatch.setenv("USE_LLM", "true")
    monkeypatch.setenv("ENERGYBRIDGE_HARNESS_PROFILE", "adaptive_v2")
    monkeypatch.setattr(roleplay_user, "RoleplayUserSimulator", FakeRoleplayUserSimulator)

    result = score_user_preference(
        building="family",
        method="rl_ppo_pref_v2",
        mean_temp_c=25.0,
        pmv_ok_fraction=0.95,
        energy_kwh_per_day=24.0,
        agent_setpoint_c=26.0,
        event_index=1,
        persona={
            "id": "washer_household",
            "scoring_weights": {"comfort": 0.4, "energy": 0.3, "vpp": 0.3},
            "appliances": {
                "ac": {
                    "setpoint_preferred_min_c": 24.0,
                    "setpoint_preferred_max_c": 26.0,
                },
                "washer": {"present": True},
            },
        },
        agent_reason="The event setpoint remains comfortable, but no washer command was emitted.",
        appliance_summary={
            "washer": {
                "present": True,
                "completed": True,
                "skipped": False,
                "ran_during_vpp": False,
            }
        },
        vpp_result_context={"achieved": True},
        policy_control_context={
            "method": "rl_ppo_pref_v2",
            "objective_source": "rl_ppo_pref_v2_policy",
            "action_space_services": ["washer"],
            "emitted_services": [],
            "vpp_trigger_actions": {},
            "vpp_acceptance_gate": {
                "acceptance_probability": 0.31,
                "roleplay_decision": "accept",
                "accepted": True,
                "roleplay_source": "roleplay_llm",
                "fallback_source": None,
            },
        },
    )

    state = captured["selected_strategy"]
    assert state["policy_control_context"]["missing_policy_services"] == ["washer"]
    rationale = captured["projected_control_plan"]["rationale"].lower()
    assert "observable appliance-strategy gap" in rationale
    assert "automatically convert it into a missed service outcome" in rationale
    assert "normally 1/5" not in rationale

    assert result["score"] == 4
    assert result["comment"] == (
        "The routine completed, but I still want an explicit washer action next time."
    )
    assert result["policy_service_guard"] == {
        "missing_policy_services": ["washer"],
        "unsupported_policy_services": [],
        "emitted_policy_services": [],
        "present_required_services": ["washer"],
    }
    assert "rl_policy_service_guard" not in result
    assert result["score_consistency_audit"]["acceptance_probability"] == 0.31
    assert result["score_consistency_audit"]["score_was_posthoc_remapped"] is False


def test_v2_achieved_vpp_guard_is_audit_only(monkeypatch) -> None:
    comment = "The VPP event failed, although my comfort was fine."
    result, _ = _score_with_authored_feedback(
        monkeypatch,
        {
            "satisfaction_score": 2,
            "comfort_score": 4,
            "energy_score": 1,
            "vpp_score": 1,
            "satisfaction_label": "dissatisfied",
            "comment": comment,
        },
        vpp_result_context={"achieved": True},
    )

    assert _authored_tuple(result) == (2, 4, 1, 1, "dissatisfied", comment)
    assert "factual_consistency_guard" not in result
    audits = result["non_safety_factual_audits"]
    assert [item["check"] for item in audits] == [
        "achieved_vpp_authored_judgement_disagreement"
    ]
    assert audits[0]["facts"] == {
        "vpp_achieved": True,
        "comment_claimed_vpp_miss": True,
        "authored_vpp_score": 1,
    }
    assert audits[0]["score_was_posthoc_remapped_by_guard"] is False
    score_audit = result["score_consistency_audit"]
    assert score_audit["score_was_posthoc_remapped"] is False
    assert score_audit["non_safety_factual_audit_count"] == 1
    assert score_audit["non_safety_factual_checks"] == [
        "achieved_vpp_authored_judgement_disagreement"
    ]


def test_v2_explicit_ev_guard_is_audit_only(monkeypatch) -> None:
    comment = "The EV schedule was missing, so I remain dissatisfied."
    persona = {
        "id": "ev_audit_household",
        "scoring_weights": {"comfort": 0.5, "energy": 0.25, "vpp": 0.25},
        "appliances": {
            "ac": {
                "present": True,
                "setpoint_preferred_min_c": 24.0,
                "setpoint_preferred_max_c": 26.0,
                "temp_tolerance_c": 1.0,
            },
            "ev": {
                "present": True,
                "arrival_h": 18.0,
                "departure_h": 7.5,
            },
        },
    }
    result, _ = _score_with_authored_feedback(
        monkeypatch,
        {
            "satisfaction_score": 2,
            "comfort_score": 4,
            "energy_score": 1,
            "vpp_score": 4,
            "satisfaction_label": "dissatisfied",
            "comment": comment,
        },
        persona=persona,
        appliance_summary={
            "ev": {"present": True, "target_reached": True, "ran_during_vpp": False},
        },
        vpp_result_context={"achieved": True},
        policy_control_context={
            "method": "controller_a",
            "action_space_services": ["ev"],
            "emitted_services": ["ev"],
            "vpp_trigger_actions": {
                "ev_mode": "scheduled",
                "ev_charge_start_h": 20.0,
                "ev_charge_end_h": 7.5,
            },
        },
    )

    assert _authored_tuple(result) == (2, 4, 1, 4, "dissatisfied", comment)
    assert "factual_consistency_guard" not in result
    audit = result["non_safety_factual_audits"][0]
    assert audit["check"] == "explicit_ev_action_authored_comment_disagreement"
    assert audit["facts"] == {
        "ev_policy_explicit": True,
        "ev_target_reached": True,
        "comment_claimed_ev_missing": True,
    }
    assert result["score_consistency_audit"]["non_safety_factual_checks"] == [
        "explicit_ev_action_authored_comment_disagreement"
    ]


def test_v2_fixed_load_low_disruption_guard_is_audit_only(monkeypatch) -> None:
    comment = "I am dissatisfied with how this event was handled."
    persona = {
        "id": "fixed_load_audit_household",
        "tags": {"price": "low_incentive"},
        "scoring_weights": {"comfort": 0.5, "energy": 0.25, "vpp": 0.25},
        "appliances": {
            "ac": {
                "present": True,
                "setpoint_preferred_min_c": 24.0,
                "setpoint_preferred_max_c": 26.0,
                "temp_tolerance_c": 1.0,
            },
            "water_heater": {"present": True, "dr_adjustable": False},
        },
    }
    result, _ = _score_with_authored_feedback(
        monkeypatch,
        {
            "satisfaction_score": 2,
            "comfort_score": 4,
            "energy_score": 2,
            "vpp_score": 1,
            "satisfaction_label": "dissatisfied",
            "comment": comment,
        },
        persona=persona,
        appliance_summary={
            "water_heater": {
                "present": True,
                "ran_during_vpp": True,
                "ready_at_bath": True,
            },
        },
        vpp_result_context={"achieved": False},
    )

    assert _authored_tuple(result) == (2, 4, 2, 1, "dissatisfied", comment)
    assert "fixed_constraint_satisfaction_guard" not in result
    audit = result["non_safety_factual_audits"][0]
    assert audit["check"] == "fixed_load_limited_vpp_authored_judgement"
    assert audit["facts"]["fixed_appliances"] == ["water_heater"]
    assert audit["score_was_posthoc_remapped_by_guard"] is False
    assert result["score_consistency_audit"]["non_safety_factual_checks"] == [
        "fixed_load_limited_vpp_authored_judgement"
    ]


def test_v2_real_unserved_service_hard_cap_still_applies(monkeypatch) -> None:
    persona = {
        "id": "unserved_ev_household",
        "scoring_weights": {"comfort": 0.5, "energy": 0.25, "vpp": 0.25},
        "appliances": {
            "ac": {
                "present": True,
                "setpoint_preferred_min_c": 24.0,
                "setpoint_preferred_max_c": 26.0,
            },
            "ev": {"present": True, "arrival_h": 18.0, "departure_h": 7.5},
        },
    }
    result, _ = _score_with_authored_feedback(
        monkeypatch,
        {
            "satisfaction_score": 5,
            "comfort_score": 5,
            "energy_score": 5,
            "vpp_score": 5,
            "satisfaction_label": "very_satisfied",
            "comment": "Everything seemed excellent.",
        },
        persona=persona,
        appliance_summary={
            "ev": {"present": True, "target_reached": False, "ran_during_vpp": False},
        },
        vpp_result_context={"achieved": True},
        policy_control_context={
            "method": "controller_a",
            "action_space_services": ["ev"],
            "emitted_services": ["ev"],
            "vpp_trigger_actions": {
                "ev_charge_start_h": 20.0,
                "ev_charge_end_h": 7.5,
            },
        },
    )

    assert result["score"] == 2
    assert result["energy_score"] == 2
    assert result["vpp_score"] == 2
    assert result["label"] == "dissatisfied"
    assert "required appliance service target(s) were not met (ev)" in result["comment"].lower()
    assert "score_consistency_audit" not in result


def test_legacy_non_safety_guards_keep_rewriting_behavior(monkeypatch) -> None:
    achieved, _ = _score_with_authored_feedback(
        monkeypatch,
        {
            "satisfaction_score": 2,
            "comfort_score": 4,
            "energy_score": 1,
            "vpp_score": 1,
            "satisfaction_label": "dissatisfied",
            "comment": "The VPP event failed, although comfort was fine.",
        },
        profile="legacy_v1",
        vpp_result_context={"achieved": True},
    )
    assert achieved["factual_consistency_guard"] == "corrected_achieved_vpp_missed_label"
    assert achieved["comment"] == "VPP appliance criterion achieved; comfort/routine were preserved."

    ev_persona = {
        "id": "ev_legacy_household",
        "scoring_weights": {"comfort": 0.5, "energy": 0.25, "vpp": 0.25},
        "appliances": {
            "ac": {
                "present": True,
                "setpoint_preferred_min_c": 24.0,
                "setpoint_preferred_max_c": 26.0,
            },
            "ev": {"present": True, "arrival_h": 18.0, "departure_h": 7.5},
        },
    }
    ev, _ = _score_with_authored_feedback(
        monkeypatch,
        {
            "satisfaction_score": 2,
            "comfort_score": 4,
            "energy_score": 1,
            "vpp_score": 4,
            "satisfaction_label": "dissatisfied",
            "comment": "The EV schedule was missing.",
        },
        profile="legacy_v1",
        persona=ev_persona,
        appliance_summary={
            "ev": {"present": True, "target_reached": True, "ran_during_vpp": False},
        },
        vpp_result_context={"achieved": True},
        policy_control_context={
            "method": "controller_a",
            "action_space_services": ["ev"],
            "emitted_services": ["ev"],
            "vpp_trigger_actions": {
                "ev_charge_start_h": 20.0,
                "ev_charge_end_h": 7.5,
            },
        },
    )
    assert ev["factual_consistency_guard"] == "corrected_false_ev_missing_label"
    assert ev["comment"] == (
        "EV charging schedule was emitted and target SOC was reached; "
        "remaining concerns are comfort/routine only."
    )

    fixed_persona = {
        "id": "fixed_load_legacy_household",
        "tags": {"price": "low_incentive"},
        "scoring_weights": {"comfort": 0.5, "energy": 0.25, "vpp": 0.25},
        "appliances": {
            "ac": {
                "present": True,
                "setpoint_preferred_min_c": 24.0,
                "setpoint_preferred_max_c": 26.0,
                "temp_tolerance_c": 1.0,
            },
            "water_heater": {"present": True, "dr_adjustable": False},
        },
    }
    fixed, _ = _score_with_authored_feedback(
        monkeypatch,
        {
            "satisfaction_score": 2,
            "comfort_score": 4,
            "energy_score": 2,
            "vpp_score": 1,
            "satisfaction_label": "dissatisfied",
            "comment": "I disliked the event.",
        },
        profile="legacy_v1",
        persona=fixed_persona,
        appliance_summary={
            "water_heater": {
                "present": True,
                "ran_during_vpp": True,
                "ready_at_bath": True,
            },
        },
        vpp_result_context={"achieved": False},
    )
    assert fixed["fixed_constraint_satisfaction_guard"] == (
        "overall_user_satisfaction_not_penalized_for_fixed_non_dr_loads"
    )
    assert fixed["comment"] == "Comfort/consent preserved; fixed loads limited VPP."
    assert "non_safety_factual_audits" not in achieved
    assert "non_safety_factual_audits" not in ev
    assert "non_safety_factual_audits" not in fixed


def test_legacy_missing_policy_service_keeps_direct_punitive_guard(monkeypatch) -> None:
    import energybridge.llm.roleplay_user as roleplay_user

    called = False

    class UnexpectedRoleplayUserSimulator:
        def generate_feedback(self, **kwargs):
            nonlocal called
            called = True
            raise AssertionError("legacy missing-service guard must return before feedback LLM")

    monkeypatch.setenv("ENERGYBRIDGE_HARNESS_PROFILE", "legacy_v1")
    monkeypatch.setattr(
        roleplay_user,
        "RoleplayUserSimulator",
        UnexpectedRoleplayUserSimulator,
    )

    result = score_user_preference(
        building="family",
        method="rl_ppo_pref_v2",
        mean_temp_c=25.0,
        pmv_ok_fraction=0.95,
        energy_kwh_per_day=24.0,
        agent_setpoint_c=26.0,
        event_index=1,
        persona={
            "id": "washer_household",
            "scoring_weights": {"comfort": 0.4, "energy": 0.3, "vpp": 0.3},
            "appliances": {"washer": {"present": True}},
        },
        appliance_summary={
            "washer": {
                "present": True,
                "completed": True,
                "skipped": False,
                "ran_during_vpp": False,
            }
        },
        policy_control_context={
            "method": "rl_ppo_pref_v2",
            "objective_source": "rl_ppo_pref_v2_policy",
            "action_space_services": ["washer"],
            "emitted_services": [],
            "vpp_trigger_actions": {},
        },
    )

    assert called is False
    assert result["score"] == 1
    assert result["comfort_score"] == 2
    assert result["energy_score"] == 1
    assert result["vpp_score"] == 1
    assert result["source"] == "roleplay_llm"
    assert result["policy_service_guard"]["missing_policy_services"] == ["washer"]
    assert result["rl_policy_service_guard"] == result["policy_service_guard"]
    assert "score_consistency_audit" not in result


def test_skipped_shiftable_task_forces_low_score() -> None:
    result = score_user_preference(
        building="family",
        method="agent",
        mean_temp_c=25.5,
        pmv_ok_fraction=0.9,
        energy_kwh_per_day=20.0,
        agent_setpoint_c=26.0,
        event_index=2,
        persona={"id": "basic_test", "scoring_weights": {"comfort": 0.5, "energy": 0.2, "vpp": 0.3}},
        appliance_summary={
            "washer": {"present": True, "skipped": True, "completed": False, "ran_during_vpp": False},
            "dishwasher": {"present": True, "skipped": False, "completed": True, "ran_during_vpp": False},
        },
    )

    assert result["score"] == 1
    assert result["vpp_score"] == 1
    assert "skipped" in result["comment"].lower()


def test_unserved_ev_target_caps_user_score() -> None:
    result = score_user_preference(
        building="family",
        method="rule_milp",
        mean_temp_c=25.5,
        pmv_ok_fraction=0.9,
        energy_kwh_per_day=20.0,
        agent_setpoint_c=26.0,
        event_index=1,
        persona={
            "id": "ev_commuter",
            "scoring_weights": {"comfort": 0.4, "energy": 0.2, "vpp": 0.4},
            "appliances": {
                "ac": {"setpoint_preferred_min_c": 24.0, "setpoint_preferred_max_c": 26.0},
                "ev": {"present": True},
            },
        },
        appliance_summary={
            "ev": {"present": True, "target_reached": False, "ran_during_vpp": False},
        },
        policy_control_context={
            "method": "rule_milp",
            "action_space_services": ["ev"],
            "emitted_services": ["ev"],
            "vpp_trigger_actions": {"ev_charge_start_h": 4.5, "ev_charge_end_h": 7.5},
        },
    )

    assert result["score"] <= 2
    assert "ev" in result["comment"].lower()
    assert "not met" in result["comment"].lower()


def test_repaired_ev_window_does_not_cap_when_target_reached() -> None:
    result = score_user_preference(
        building="family",
        method="EnergyBridge",
        mean_temp_c=25.0,
        pmv_ok_fraction=0.95,
        energy_kwh_per_day=20.0,
        agent_setpoint_c=25.0,
        event_index=1,
        persona={
            "id": "ev_commuter",
            "scoring_weights": {"comfort": 0.4, "energy": 0.2, "vpp": 0.4},
            "appliances": {
                "ac": {"setpoint_preferred_min_c": 24.0, "setpoint_preferred_max_c": 26.0},
                "ev": {"present": True, "arrival_h": 18.0, "departure_h": 7.5},
            },
        },
        appliance_summary={
            "ev": {"present": True, "target_reached": True, "ran_during_vpp": False},
        },
        policy_control_context={
            "method": "EnergyBridge",
            "action_space_services": ["ev"],
            "emitted_services": ["ev"],
            "vpp_trigger_actions": {"ev_charge_start_h": 4.5, "ev_charge_end_h": 7.5},
        },
    )

    assert result["score"] > 2
    assert "required appliance service target(s) were not met" not in result["comment"].lower()


def test_unserved_water_heater_without_bath_check_flag_caps_user_score() -> None:
    result = score_user_preference(
        building="family",
        method="EnergyBridge",
        mean_temp_c=25.5,
        pmv_ok_fraction=0.9,
        energy_kwh_per_day=20.0,
        agent_setpoint_c=25.5,
        event_index=1,
        persona={
            "id": "bath_user",
            "scoring_weights": {"comfort": 0.4, "energy": 0.2, "vpp": 0.4},
            "appliances": {
                "ac": {"setpoint_preferred_min_c": 24.0, "setpoint_preferred_max_c": 26.0},
                "water_heater": {"present": True},
            },
        },
        appliance_summary={
            "water_heater": {"present": True, "ready_at_bath": False, "ran_during_vpp": False},
        },
        policy_control_context={
            "method": "EnergyBridge",
            "action_space_services": ["water_heater"],
            "emitted_services": ["water_heater"],
            "vpp_trigger_actions": {
                "water_heater_preheat": True,
                "water_heater_preheat_start_h": 19.0,
                "water_heater_preheat_end_h": 20.0,
            },
        },
    )

    assert result["score"] <= 2
    assert "water_heater" in result["comment"].lower()
    assert "not met" in result["comment"].lower()


def test_memory_notes_enable_cautious_energy_exploration_after_positive_feedback() -> None:
    persona = {
        "id": "generic_price_cooperative",
        "tags": {"comfort": "normal_comfort", "price": "price_sensitive", "control": "suggestion_first"},
        "appliances": {"ac": {"setpoint_preferred_min_c": 24.0, "setpoint_preferred_max_c": 26.0}},
    }
    events = [
        {"score": 4, "comfort_score": 4, "comment": "Comfort stayed within range."},
        {"score": 4, "comfort_score": 4, "comment": "Comfort stayed reasonable and VPP succeeded."},
    ]

    notes = build_vpp_preference_memory_notes(events, persona)

    joined = " ".join(notes).lower()
    assert "warm edge" in joined
    assert "avoid unnecessary cooling" in joined


def test_memory_notes_do_not_escalate_after_warmth_feedback() -> None:
    persona = {
        "id": "generic_price_cooperative",
        "tags": {"comfort": "normal_comfort", "price": "price_sensitive", "control": "suggestion_first"},
        "appliances": {"ac": {"setpoint_preferred_min_c": 24.0, "setpoint_preferred_max_c": 26.0}},
    }
    events = [
        {"score": 4, "comfort_score": 4, "comment": "Acceptable."},
        {"score": 4, "comfort_score": 4, "comment": "A bit too warm near 26.5."},
    ]

    notes = build_vpp_preference_memory_notes(events, persona)

    joined = " ".join(notes).lower()
    assert "warm edge" not in joined
    assert "do not escalate" in joined


def test_memory_notes_learn_fixed_vpp_overlap_as_constraint() -> None:
    persona = {
        "id": "fixed_water_heater_user",
        "tags": {"comfort": "normal_comfort", "price": "price_indifferent", "control": "low_auto_accept"},
        "appliances": {
            "water_heater": {"present": True, "dr_adjustable": False},
            "washer": {"present": True, "dr_adjustable": True},
        },
    }
    events = [
        {
            "score": 4,
            "comfort_score": 4,
            "comment": "Comfort and routine were preserved.",
            "appliance_summary": {
                "water_heater": {"present": True, "ran_during_vpp": True},
                "washer": {"present": True, "ran_during_vpp": False},
            },
        }
    ]

    notes = build_vpp_preference_memory_notes(events, persona)

    joined = " ".join(notes).lower()
    assert "fixed appliances overlap" in joined
    assert "controllable devices" in joined
