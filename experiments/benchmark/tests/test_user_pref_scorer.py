import json
from copy import deepcopy

from experiments.benchmark.user_pref_scorer import (
    StrategyPreference,
    _calibrate_roleplay_score,
    _method_blind_observable_text,
    _observable_acceptance_judgement,
    build_vpp_preference_memory_notes,
    get_user_preference_input,
    normalize_persona,
    score_user_preference,
)


def _install_pre_event_roleplay_llm(monkeypatch, responder):
    import energybridge.llm.client as llm_client

    calls = []

    class FakeLLMClient:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

        def chat_with_metrics(self, system_prompt, user_prompt, **kwargs):
            calls.append((system_prompt, user_prompt, dict(kwargs)))
            text = responder(system_prompt, user_prompt)
            return {
                "text": text,
                "metrics": {
                    "provider": "SECRET_PROVIDER_SENTINEL",
                    "model": "SECRET_MODEL_SENTINEL",
                },
            }

    monkeypatch.setenv("USE_LLM", "true")
    monkeypatch.setenv("ROLEPLAY_USE_LLM", "true")
    monkeypatch.setattr(llm_client, "LLMClient", FakeLLMClient)
    return calls


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
    assert audit["post_event_evidence"]["offer_judgement_phase"] == "pre_event_proposal"
    assert audit["post_event_evidence"]["satisfaction_phase"] == "post_event_experience"


def test_v2_higher_post_event_rating_records_new_outcome_basis_without_remap(
    monkeypatch,
) -> None:
    monkeypatch.setenv("ENERGYBRIDGE_HARNESS_PROFILE", "adaptive_v2")
    result = _calibrated_score(
        0.66,
        authored_score=5,
        authored_comment=(
            "Comfort held, the household services completed, and the appliance event was avoided."
        ),
        accepted=True,
        achieved=True,
        mean_temp_c=25.0,
        pmv_ok_fraction=0.95,
    )

    assert result["score"] == 5
    assert result["comment"].startswith("Comfort held")
    audit = result["score_consistency_audit"]
    assert audit["acceptance_probability"] == 0.66
    assert audit["normalized_authored_rating"] == 1.0
    assert audit["signed_rating_minus_acceptance"] == 0.34
    assert audit["score_was_posthoc_remapped"] is False
    assert audit["phase_interpretation"] == (
        "higher_post_event_rating_has_new_positive_outcome_evidence"
    )
    evidence = audit["post_event_evidence"]
    assert evidence["realised_plan_basis"] == "accepted_offered_plan"
    assert evidence["positive_outcome_evidence"] == [
        "observed_comfort_preserved",
        "observed_vpp_service_achieved",
    ]
    assert evidence["negative_outcome_evidence"] == []


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
    judgement = state["pre_event_offer_judgement"]
    assert "acceptance_probability" not in judgement
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
    assert state["observed_action_evidence"]["missing_services"] == ["washer"]
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


def test_v2_hidden_low_disruption_tag_does_not_add_scoring_audit(monkeypatch) -> None:
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
    assert "non_safety_factual_audits" not in result
    assert result["score_consistency_audit"]["non_safety_factual_checks"] == []


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


def test_adaptive_automated_pre_event_uses_one_household_statement(monkeypatch) -> None:
    import experiments.benchmark.user_pref_scorer as scorer
    from energybridge.harness.profile import build_household_resume
    from energybridge.harness.roleplay import sanitize_household_resume_for_roleplay

    calls = _install_pre_event_roleplay_llm(
        monkeypatch,
        lambda _system, _user: json.dumps({
            "statement": (
                "I can consider a brief adjustment if the washer still finishes before bedtime; "
                "otherwise, keep our normal routine."
            )
        }),
    )
    monkeypatch.setenv("ENERGYBRIDGE_HARNESS_PROFILE", "adaptive_v2")

    def unexpected_candidates(*_args, **_kwargs):
        raise AssertionError("adaptive automated role-play must not generate a strategy menu")

    monkeypatch.setattr(scorer, "generate_vpp_strategy_candidates", unexpected_candidates)
    persona = {
        "id": "hidden-household-id",
        "display_name": "Hidden Household Label",
        "description": "We need the evening washer load finished before bedtime.",
        "llm_prompts": {"system_prompt": "Speak plainly about our evening routine."},
        "preferences": {
            "scoring_weights": {"comfort": 0.7, "energy": 0.2, "vpp": 0.1},
            "vpp_override_prob": 0.91,
        },
        "appliances": {
            "washer": {
                "present": True,
                "shiftable": True,
                "earliest_h": 17.0,
                "latest_h": 22.0,
            },
        },
    }
    event = {"id": "vpp-1", "day": 1, "trigger_h": 18.0, "end_h": 19.0}

    result = get_user_preference_input(
        "family", 1, event, [], persona=persona, human_mode=False
    )

    assert isinstance(result, StrategyPreference)
    assert str(result).startswith("I can consider a brief adjustment")
    assert result.strategy_trace["source"] == "roleplay_llm"
    assert result.strategy_trace["candidates"] == []
    selected = result.strategy_trace["selected_strategy"]
    assert selected["id"] == "household_statement"
    assert selected["preference_text"] == str(result)
    system_prompt, user_prompt, _ = calls[0]
    lowered = system_prompt.lower()
    assert "exactly 3" not in lowered
    assert "a/b/c" not in lowered
    assert "candidate" not in lowered
    assert "comfort_priority" not in lowered
    payload = json.loads(user_prompt)
    assert set(payload) == {"schema_version", "household_resume", "event"}
    normalized = normalize_persona(persona)
    expected_resume = sanitize_household_resume_for_roleplay(
        build_household_resume(
            normalized,
            appliance_config=normalized["appliances"],
            past_events=[],
        )
    )
    assert payload["household_resume"] == expected_resume


def test_adaptive_pre_event_prompt_is_identical_when_only_hidden_fields_change(
    monkeypatch,
) -> None:
    calls = _install_pre_event_roleplay_llm(
        monkeypatch,
        lambda _system, _user: json.dumps({
            "statement": "Please keep the evening service deadline intact."
        }),
    )
    monkeypatch.setenv("ENERGYBRIDGE_HARNESS_PROFILE", "adaptive_v2")
    visible = {
        "description": "We return home at 18:00 and need hot water ready by 20:00.",
        "llm_prompts": {"system_prompt": "Use a concise first-person household voice."},
        "schedule": {"returns_home_h": 18.0},
        "appliances": {
            "water_heater": {
                "present": True,
                "dr_adjustable": True,
                "bath_required_h": 20.0,
            },
        },
    }
    left = deepcopy(visible)
    right = deepcopy(visible)
    left.update({
        "id": "HIDDEN_LEFT_ID",
        "display_name": "Hidden Left Label",
        "tags": {"latent_group": "left"},
        "preferences": {
            "scoring_weights": {"comfort": 0.99, "energy": 0.005, "vpp": 0.005},
            "vpp_override_prob": 0.01,
            "scoring_rubric": "HIDDEN_LEFT_RUBRIC",
        },
        "provider": "HIDDEN_LEFT_PROVIDER",
        "model": "HIDDEN_LEFT_MODEL",
        "api_key": "sk-HIDDENLEFT123",
        "base_url": "https://left-hidden.example/v1",
    })
    right.update({
        "id": "HIDDEN_RIGHT_ID",
        "display_name": "Hidden Right Label",
        "tags": {"latent_group": "right"},
        "preferences": {
            "scoring_weights": {"comfort": 0.01, "energy": 0.49, "vpp": 0.50},
            "vpp_override_prob": 0.99,
            "scoring_rubric": "HIDDEN_RIGHT_RUBRIC",
        },
        "provider": "HIDDEN_RIGHT_PROVIDER",
        "model": "HIDDEN_RIGHT_MODEL",
        "api_key": "sk-HIDDENRIGHT123",
        "base_url": "https://right-hidden.example/v1",
    })
    left_event = {
        "day": 1,
        "trigger_h": 18.0,
        "end_h": 19.0,
        "method": "HIDDEN_LEFT_METHOD",
        "provider": "HIDDEN_LEFT_EVENT_PROVIDER",
    }
    right_event = {
        "day": 1,
        "trigger_h": 18.0,
        "end_h": 19.0,
        "method": "HIDDEN_RIGHT_METHOD",
        "provider": "HIDDEN_RIGHT_EVENT_PROVIDER",
    }

    get_user_preference_input("family", 1, left_event, [], persona=left)
    get_user_preference_input("family", 1, right_event, [], persona=right)

    assert calls[0][0] == calls[1][0]
    assert calls[0][1] == calls[1][1]
    serialized = calls[0][1]
    for hidden in (
        "HIDDEN_LEFT_ID",
        "Hidden Left Label",
        "HIDDEN_LEFT_RUBRIC",
        "HIDDEN_LEFT_PROVIDER",
        "HIDDEN_LEFT_MODEL",
        "sk-HIDDENLEFT123",
        "left-hidden.example",
    ):
        assert hidden not in serialized


def test_adaptive_pre_event_visible_household_facts_change_natural_statement(
    monkeypatch,
) -> None:
    def respond(_system, user_prompt):
        if "night shift" in user_prompt.lower():
            statement = "Please keep the bedroom quiet while I sleep after my night shift."
        else:
            statement = "Please keep hot water ready for the children's evening bath."
        return json.dumps({"statement": statement})

    calls = _install_pre_event_roleplay_llm(monkeypatch, respond)
    monkeypatch.setenv("ENERGYBRIDGE_HARNESS_PROFILE", "adaptive_v2")
    night_worker = {
        "id": "hidden-a",
        "description": "I sleep during the day after my night shift.",
        "schedule": {"sleep_start_h": 8.0, "sleep_end_h": 15.0},
    }
    family_bath = {
        "id": "hidden-b",
        "description": "The children have an evening bath and need hot water ready.",
        "appliances": {
            "water_heater": {"present": True, "bath_required_h": 20.0},
        },
    }

    first = get_user_preference_input(
        "family", 1, {"trigger_h": 18.0, "end_h": 19.0}, [], persona=night_worker
    )
    second = get_user_preference_input(
        "family", 1, {"trigger_h": 18.0, "end_h": 19.0}, [], persona=family_bath
    )

    assert calls[0][1] != calls[1][1]
    assert "night shift" in str(first).lower()
    assert "evening bath" in str(second).lower()


def test_adaptive_model_statement_privacy_preserves_household_language(
    monkeypatch,
    tmp_path,
) -> None:
    authored = (
        "The utility provider portal utility.example/bill and key routine remain visible; "
        "provider OpenAI, model GPT-5, method=mpc_dynamic, evaluator=JudgeX, "
        "api_key=sk-SECRET123, endpoint=https://private.example/v1."
    )
    calls = _install_pre_event_roleplay_llm(
        monkeypatch,
        lambda _system, _user: json.dumps({"statement": authored}),
    )
    monkeypatch.setenv("ENERGYBRIDGE_HARNESS_PROFILE", "adaptive_v2")
    persona = {
        "id": "HIDDEN_LOG_PERSONA_ID",
        "description": (
            "We use the utility provider AcmeCloud portal utility.example/bill for our key routine; "
            "api_key=sk-HIDDENRESUME123."
        ),
        "calendar": {
            "source": "provider=HIDDEN_CALENDAR_PROVIDER",
            "days": [{
                "day": 1,
                "events": [{
                    "title": "My key routine at https://hidden-calendar.example/path",
                    "start_h": 18.0,
                    "end_h": 18.5,
                    "provider": "HIDDEN_EVENT_PROVIDER",
                    "api_key": "sk-HIDDENCALENDAR123",
                }],
            }],
        },
    }
    log_path = tmp_path / "adaptive_statement.jsonl"

    result = get_user_preference_input(
        "family",
        1,
        {"trigger_h": 18.0, "end_h": 19.0, "provider": "HIDDEN_CONTEXT_PROVIDER"},
        [],
        persona=persona,
        log_path=log_path,
    )

    statement = str(result)
    lowered = statement.lower()
    assert "utility service company portal [private endpoint]" in lowered
    assert "key routine remain visible" in lowered
    for forbidden in (
        "utility.example",
        "openai",
        "gpt-5",
        "mpc_dynamic",
        "judgex",
        "sk-secret123",
        "private.example",
        "provider",
        "model",
        "method",
        "evaluator",
    ):
        assert forbidden not in lowered

    prompt_text = calls[0][1]
    prompt_lowered = prompt_text.lower()
    assert "utility service company" in prompt_lowered
    assert "portal [private endpoint]" in prompt_lowered
    assert "key routine" in prompt_lowered
    for hidden in (
        "AcmeCloud",
        "utility.example",
        "sk-HIDDENRESUME123",
    ):
        assert hidden not in prompt_text

    trace_text = json.dumps(result.strategy_trace, ensure_ascii=False)
    log_text = log_path.read_text(encoding="utf-8")
    assert "key routine" in trace_text
    for hidden in (
        "HIDDEN_LOG_PERSONA_ID",
        "HIDDEN_CALENDAR_PROVIDER",
        "HIDDEN_EVENT_PROVIDER",
        "sk-HIDDENCALENDAR123",
        "hidden-calendar.example",
        "HIDDEN_CONTEXT_PROVIDER",
        "SECRET_PROVIDER_SENTINEL",
        "SECRET_MODEL_SENTINEL",
    ):
        assert hidden not in trace_text
        assert hidden not in log_text
    assert json.loads(log_text)["persona"] == "roleplay_household"


def test_adaptive_roleplay_off_uses_single_visible_fact_fallback(monkeypatch) -> None:
    import experiments.benchmark.user_pref_scorer as scorer
    import energybridge.llm.client as llm_client

    monkeypatch.setenv("ENERGYBRIDGE_HARNESS_PROFILE", "adaptive_v2")
    monkeypatch.setenv("USE_LLM", "true")
    monkeypatch.setenv("ROLEPLAY_USE_LLM", "false")

    class UnexpectedLLMClient:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("ROLEPLAY_USE_LLM=false must not construct a role-play client")

    monkeypatch.setattr(llm_client, "LLMClient", UnexpectedLLMClient)

    def unexpected_candidates(*_args, **_kwargs):
        raise AssertionError("adaptive fallback must not use fixed strategy candidates")

    monkeypatch.setattr(scorer, "generate_vpp_strategy_candidates", unexpected_candidates)
    result = get_user_preference_input(
        "family",
        1,
        {"trigger_h": 18.0, "end_h": 19.0},
        [],
        persona={
            "id": "fallback-household",
            "appliances": {"washer": {"present": True, "latest_h": 22.0}},
        },
    )

    assert isinstance(result, StrategyPreference)
    assert result.strategy_trace["source"] == "roleplay_visible_fact_fallback"
    assert result.strategy_trace["candidates"] == []
    lowered = str(result).lower()
    assert "only if" in lowered
    assert "washer" in lowered
    assert "balanced" not in lowered
    assert "25°c" not in lowered
    assert "26°c" not in lowered
    assert "27°c" not in lowered


def test_legacy_pre_event_menu_and_selected_text_remain_frozen(
    monkeypatch,
    capsys,
) -> None:
    import experiments.benchmark.user_pref_scorer as scorer

    monkeypatch.setenv("ENERGYBRIDGE_HARNESS_PROFILE", "legacy_v1")
    monkeypatch.setenv("USE_LLM", "false")
    monkeypatch.setenv("ROLEPLAY_USE_LLM", "false")
    monkeypatch.setattr("builtins.input", lambda _prompt: "B")

    def unexpected_adaptive(*_args, **_kwargs):
        raise AssertionError("legacy path must not invoke adaptive household role-play")

    monkeypatch.setattr(scorer, "_generate_adaptive_household_statement", unexpected_adaptive)
    result = get_user_preference_input(
        "family",
        1,
        {"trigger_h": 18.0, "end_h": 19.0},
        [],
        persona={
            "id": "legacy",
            "stable_preferences": {"comfort_priority": 0.5},
            "scoring_weights": {"comfort": 0.5, "energy": 0.2, "vpp": 0.3},
            "vpp_override_prob": 0.0,
        },
        human_mode=True,
    )

    assert str(result) == "Balanced: allow only a brief AC adjustment within 24.0-26.0°C."
    assert result.strategy_trace["source"] == "human"
    assert [item["id"] for item in result.strategy_trace["candidates"]] == ["A", "B", "C"]
    assert result.strategy_trace["selected_strategy"]["id"] == "B"
    output = capsys.readouterr().out
    assert "[Strategy Candidates | VPP event 1]" in output
    assert "Enter A / B / C" in output


def test_adaptive_human_menu_prompt_is_hidden_identity_invariant_and_output_safe(
    monkeypatch,
) -> None:
    import experiments.benchmark.user_pref_scorer as scorer

    authored = [
        {
            "id": option_id,
            "label": f"Option {option_id} from provider OpenAI",
            "description": "Protect the key routine and utility provider portal utility.example/bill.",
            "tradeoff": "endpoint=https://private.example/v1 evaluator=JudgeX",
            "user_pref": "Keep my key routine; api_key=sk-SECRET123 and model GPT-5 are irrelevant.",
        }
        for option_id in ("A", "B", "C")
    ]
    calls = _install_pre_event_roleplay_llm(
        monkeypatch, lambda _system, _user: json.dumps(authored)
    )
    monkeypatch.setenv("ENERGYBRIDGE_HARNESS_PROFILE", "adaptive_v2")
    visible = {
        "description": "We protect a key routine and use the utility provider portal utility.example/bill.",
        "calendar": {
            "days": [{"day": 1, "events": [{"title": "Key routine", "start_h": 18.0, "end_h": 19.0}]}]
        },
        "appliances": {"washer": {"present": True, "shiftable": True}},
    }
    left = deepcopy(visible)
    right = deepcopy(visible)
    left.update({
        "id": "LEFT_ID",
        "tags": {"control": "confirm_required"},
        "scoring_weights": {"comfort": 0.99, "energy": 0.01},
        "provider": "LEFT_PROVIDER",
        "model": "LEFT_MODEL",
        "api_key": "sk-LEFTSECRET",
    })
    right.update({
        "id": "RIGHT_ID",
        "tags": {"price": "price_driven"},
        "scoring_weights": {"comfort": 0.01, "energy": 0.99},
        "provider": "RIGHT_PROVIDER",
        "model": "RIGHT_MODEL",
        "api_key": "sk-RIGHTSECRET",
    })
    left_past = [{"id": "LEFT_EVENT", "score": 3, "comment": "Key routine; provider OpenAI."}]
    right_past = [{"id": "RIGHT_EVENT", "score": 3, "comment": "Key routine; provider Anthropic."}]

    left_candidates = scorer.generate_vpp_strategy_candidates(
        "family", 1, {"id": "LEFT_VPP", "day": 1, "trigger_h": 18.0, "end_h": 19.0},
        left_past, left,
    )
    right_candidates = scorer.generate_vpp_strategy_candidates(
        "family", 1, {"id": "RIGHT_VPP", "day": 1, "trigger_h": 18.0, "end_h": 19.0},
        right_past, right,
    )

    assert calls[0] == calls[1]
    assert left_candidates == right_candidates
    serialized = json.dumps(left_candidates, ensure_ascii=False).lower()
    assert "key routine" in serialized
    assert "utility service company portal [private endpoint]" in serialized
    for forbidden in ("openai", "gpt-5", "judgex", "sk-secret", "private.example"):
        assert forbidden not in serialized

    monkeypatch.setattr("builtins.input", lambda _prompt: "B")
    selected = get_user_preference_input(
        "family", 1, {"day": 1, "trigger_h": 18.0, "end_h": 19.0}, [],
        persona=left, human_mode=True,
    )
    assert selected.strategy_trace["candidates"]
    assert selected.strategy_trace["selected_strategy"]["id"] == "B"


def test_adaptive_feedback_payload_is_hidden_identity_invariant_and_output_safe(
    monkeypatch,
) -> None:
    import energybridge.llm.roleplay_user as roleplay_user

    captured = []

    class FakeRoleplayUserSimulator:
        def generate_feedback(self, **kwargs):
            captured.append(deepcopy(kwargs))
            return {
                "data": {
                    "satisfaction_score": 4,
                    "comfort_score": 4,
                    "energy_score": 3,
                    "vpp_score": 4,
                    "satisfaction_label": "satisfied",
                    "comment": (
                        "The utility provider portal utility.example/bill and key routine worked; "
                        "provider OpenAI model GPT-5 evaluator JudgeX api_key=sk-SECRET123 "
                        "endpoint=https://private.example/v1."
                    ),
                    "zone_comfort_scores": {
                        "Core": "Key routine okay; provider OpenAI endpoint=https://zone.example/v1"
                    },
                }
            }

    monkeypatch.setenv("ENERGYBRIDGE_HARNESS_PROFILE", "adaptive_v2")
    monkeypatch.setenv("USE_LLM", "false")
    monkeypatch.setenv("ROLEPLAY_USE_LLM", "true")
    monkeypatch.setattr(roleplay_user, "RoleplayUserSimulator", FakeRoleplayUserSimulator)
    visible = {
        "description": "We use a utility provider portal utility.example/bill for our key routine.",
        "calendar": {
            "days": [{"day": 1, "events": [{"title": "Key routine", "start_h": 18.0, "end_h": 19.0}]}]
        },
        "appliances": {"ac": {"present": True, "setpoint_preferred_min_c": 24.0, "setpoint_preferred_max_c": 26.0}},
    }
    results = []
    for marker, tags, weights in (
        ("LEFT", {"control": "confirm_required"}, {"comfort": 0.99, "vpp": 0.01}),
        ("RIGHT", {"price": "price_driven"}, {"comfort": 0.01, "vpp": 0.99}),
    ):
        persona = deepcopy(visible)
        persona.update({
            "id": f"{marker}_ID",
            "tags": tags,
            "scoring_weights": weights,
            "provider": f"{marker}_PROVIDER",
            "model": f"{marker}_MODEL",
            "api_key": f"sk-{marker}SECRET",
            "base_url": f"https://{marker.lower()}.example/v1",
        })
        results.append(score_user_preference(
            building="family",
            method=f"{marker}_METHOD",
            mean_temp_c=25.0,
            pmv_ok_fraction=0.95,
            energy_kwh_per_day=24.0,
            agent_setpoint_c=25.5,
            event_index=1,
            persona=persona,
            user_preference_text=(
                f"Keep the key routine and utility provider portal utility.example/bill; "
                f"provider {marker}_PROVIDER endpoint=https://{marker.lower()}.example/v1."
            ),
            agent_reason=(
                f"Keep comfort and the key routine; method={marker}_METHOD model={marker}_MODEL "
                f"endpoint=https://{marker.lower()}.example/v1."
            ),
            vpp_context={"id": f"{marker}_EVENT", "day": 1, "trigger_h": 18.0, "end_h": 19.0},
            vpp_result_context={
                "achieved": True,
                "comment": f"Key routine completed; provider {marker}_PROVIDER.",
            },
            policy_control_context={
                "method": f"{marker}_METHOD",
                "objective_source": f"{marker}_MODEL",
            },
        ))

    assert captured[0] == captured[1]
    payload = json.dumps(captured[0], ensure_ascii=False).lower()
    assert "key routine" in payload
    assert "utility service company portal [private endpoint]" in payload
    for forbidden in ("left_", "right_", "sk-left", "sk-right", "left.example", "right.example"):
        assert forbidden not in payload
    for result in results:
        output = json.dumps(result, ensure_ascii=False).lower()
        assert "key routine" in output
        assert "utility service company portal [private endpoint]" in output
        for forbidden in ("openai", "gpt-5", "judgex", "sk-secret", "private.example", "zone.example"):
            assert forbidden not in output


def test_adaptive_feedback_uses_roleplay_enable_flag(monkeypatch) -> None:
    import energybridge.llm.roleplay_user as roleplay_user

    calls = []

    class FakeRoleplayUserSimulator:
        def generate_feedback(self, **kwargs):
            calls.append(kwargs)
            return {"data": {
                "satisfaction_score": 3, "comfort_score": 3, "energy_score": 3,
                "vpp_score": 3, "satisfaction_label": "neutral", "comment": "Okay."
            }}

    monkeypatch.setenv("ENERGYBRIDGE_HARNESS_PROFILE", "adaptive_v2")
    monkeypatch.setattr(roleplay_user, "RoleplayUserSimulator", FakeRoleplayUserSimulator)
    common = dict(
        building="family", method="agent", mean_temp_c=25.0,
        pmv_ok_fraction=0.9, energy_kwh_per_day=24.0,
        persona={"description": "An ordinary household."},
    )
    monkeypatch.setenv("USE_LLM", "false")
    monkeypatch.setenv("ROLEPLAY_USE_LLM", "true")
    assert score_user_preference(**common)["source"] == "roleplay_llm"
    assert len(calls) == 1

    monkeypatch.setenv("USE_LLM", "true")
    monkeypatch.setenv("ROLEPLAY_USE_LLM", "false")
    assert score_user_preference(**common)["source"] == "rule_based_fallback"
    assert len(calls) == 1
