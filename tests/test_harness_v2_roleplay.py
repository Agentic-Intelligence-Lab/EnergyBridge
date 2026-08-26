from __future__ import annotations

import json
from pathlib import Path

import pytest

from energybridge.harness.profile import RESUME_SCHEMA_VERSION, build_household_resume
from energybridge.harness.roleplay import (
    ACCEPTANCE_SCHEMA_VERSION,
    PROBABILITY_ROUNDING_TOLERANCE,
    RoleplayResponseError,
    build_roleplay_acceptance_prompts,
    normalize_roleplay_acceptance_response,
)
from energybridge.llm.roleplay_user import (
    RoleplayUserSimulator,
    infer_observable_profile_from_answers,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PERSONA_DIR = PROJECT_ROOT / "energybridge" / "roleplay" / "personas"


def _load_persona(name: str) -> dict:
    return json.loads((PERSONA_DIR / f"{name}.json").read_text(encoding="utf-8"))


def _valid_response(**updates) -> dict:
    response = {
        "decision": "counteroffer",
        "baseline_acceptance_probability": 0.37,
        "adjustments": [
            {
                "dimension": "return-home comfort",
                "delta": -0.08,
                "evidence": "E1",
                "reason": "I do not want to arrive to a warmer home.",
            },
            {
                "dimension": "appliance service",
                "delta": 0.03,
                "evidence": "E2",
                "reason": "The schedule preserves the chore I need today.",
            },
        ],
        "final_acceptance_probability": 0.32,
        "confidence": 0.74,
        "evidence": [
            {
                "id": "E1",
                "source": "resume",
                "fact": "Comfort on arrival is a recurring preference.",
                "effect": "supports_rejection",
            },
            {
                "id": "E2",
                "source": "plan",
                "fact": "The washer remains scheduled outside the event.",
                "effect": "supports_acceptance",
            },
        ],
        "counterfactual": {
            "changes": ["Restore the normal setpoint before arrival."],
            "decision_if_changed": "accept",
            "acceptance_probability_if_changed": 0.81,
            "reason": "That change would remove my main concern.",
        },
        "reason": "I would agree only if comfort is restored before I get home.",
        "user_feedback": "Please restore the normal temperature before my arrival.",
    }
    response.update(updates)
    return response


def test_all_fixed_family_personas_build_distinct_auditable_resumes() -> None:
    paths = sorted(PERSONA_DIR.glob("basic_role_*.json"))
    assert len(paths) >= 6

    resumes = [
        build_household_resume(json.loads(path.read_text(encoding="utf-8")))
        for path in paths
    ]
    fingerprints = [resume["audit"]["profile_fingerprint"] for resume in resumes]
    narratives = [resume["biography"]["first_person_roleplay_source"] for resume in resumes]

    assert len(set(fingerprints)) == len(resumes)
    assert len(set(narratives)) == len(resumes)
    assert all(resume["schema_version"] == RESUME_SCHEMA_VERSION for resume in resumes)
    assert all(resume["audit"]["field_provenance"]["decision_profile"] for resume in resumes)
    assert all(resume["voice"]["example_utterances"] for resume in resumes)

    # Building the same household again is deterministic and auditable.
    persona = json.loads(paths[0].read_text(encoding="utf-8"))
    first = build_household_resume(persona)
    second = build_household_resume(persona)
    assert first["resume_id"] == second["resume_id"]
    assert first["audit"] == second["audit"]


def test_resume_uses_physical_appliance_override_and_bounded_relationship_history() -> None:
    persona = _load_persona("basic_role_a_commuter_price_cooperative")
    physical = {
        "ac": {
            "present": True,
            "setpoint_preferred_min_c": 23.5,
            "setpoint_preferred_max_c": 25.5,
            "temp_tolerance_c": 0.5,
        },
        "ev": {"present": True, "target_soc": 0.9, "departure_h": 6.5},
    }
    history = [
        {
            "id": f"event-{index}",
            "score": index % 5 + 1,
            "comment": f"feedback {index}",
            "method": "MUST_NOT_ENTER_RESUME_HISTORY",
        }
        for index in range(12)
    ]
    resume = build_household_resume(persona, appliance_config=physical, past_events=history)

    commitments = {
        item["device"]: item
        for item in resume["comfort_and_service"]["appliance_commitments"]
    }
    assert commitments["ev"]["target_soc"] == 0.9
    assert commitments["ac"]["setpoint_preferred_max_c"] == 25.5
    assert resume["audit"]["appliance_source"] == "appliance_config_argument"
    assert len(resume["relationship_history"]) == 8
    assert resume["relationship_history"][0]["event_id"] == "event-4"
    assert "MUST_NOT_ENTER_RESUME_HISTORY" not in json.dumps(resume, ensure_ascii=False)


def test_acceptance_prompt_is_model_blind_and_has_one_compact_response_shape() -> None:
    persona = _load_persona("basic_role_a_commuter_price_cooperative")
    system, user, payload = build_roleplay_acceptance_prompts(
        persona_config=persona,
        appliance_config=persona["appliances"],
        event={"id": "vpp1", "day": 1, "trigger_h": 18.0, "end_h": 19.0},
        proposed_plan={
            "setpoint": 26.0,
            "reason": (
                "Keep comfort stable and move the washer after the event; "
                "generated by SECRET_AGENT_MODEL with mpc_pdf_v15."
            ),
            "appliance_actions": {
                "washer_start_h": 19.5,
                "washer_skip": False,
                "washer": {
                    "start_h": 19.5,
                    "status": "selected by SECRET_NESTED_MODEL_SENTINEL",
                    "model": "ppo_policy SECRET_NESTED_MODEL_SENTINEL",
                },
                "metadata": {
                    "controller": "SECRET_CONTROLLER_ID",
                    "objective_source": "SECRET_OBJECTIVE_SOURCE",
                },
            },
            "strategy_explanation": {
                "natural_language": "EnergyBridge recommends the later washer time.",
                "recommended_actions": [
                    {
                        "device": "washer",
                        "action": "start at 19:30",
                        "provider": "SECRET_PROVIDER_SENTINEL",
                    }
                ],
            },
            "method": "SECRET_CONTROLLER_ID",
            "model": "SECRET_AGENT_MODEL",
            "objective_source": "SECRET_OBJECTIVE_SOURCE",
        },
        default_plan={
            "setpoint": 25.0,
            "reason": "controller=SECRET_DEFAULT_CONTROLLER",
            "appliance_actions": {"washer_start_h": 19.0},
        },
        past_events=[{
            "id": "prior",
            "comment": "The MPC model made this uncomfortable: SECRET_HISTORY_MODEL_SENTINEL.",
            "appliance_summary": {
                "washer": {
                    "completion_h": 20.0,
                    "source": "SECRET_HISTORY_CONTROLLER_SENTINEL",
                }
            },
        }],
        user_preference_text="I need the home comfortable when I return.",
        baseline_acceptance_probability=0.27,
    )
    serialized = json.dumps(payload, ensure_ascii=False)

    assert payload["schema_version"] == ACCEPTANCE_SCHEMA_VERSION
    assert payload["persona_baseline_acceptance_probability"] == 0.27
    assert payload["persona_baseline_audit"]["source"] == "caller.baseline_acceptance_probability"
    assert set(payload["household_resume"]["audit"]) == {
        "profile_fingerprint",
        "resume_fingerprint",
        "roleplay_projection",
    }
    assert "controller_context_source" not in serialized
    assert "the the plan" not in serialized.lower()
    assert "objective_source" not in serialized
    assert "policy_source" not in serialized
    assert "selected_skill" not in serialized
    assert "response_contract" not in payload
    assert (system + user).count('"decision": "accept|reject|counteroffer"') == 1
    assert "2-4" in system
    assert "at most two short sentences" in user
    assert "exactly one short first-person sentence" in user
    assert payload["offered_vpp_plan"]["appliance_actions"]["washer_start_h"] == 19.5
    assert payload["offered_vpp_plan"]["appliance_actions"]["washer"]["start_h"] == 19.5
    assert "SECRET_CONTROLLER_ID" not in serialized
    assert "SECRET_AGENT_MODEL" not in serialized
    assert "SECRET_OBJECTIVE_SOURCE" not in serialized
    assert "SECRET_NESTED_MODEL_SENTINEL" not in serialized
    assert "SECRET_PROVIDER_SENTINEL" not in serialized
    assert "SECRET_DEFAULT_CONTROLLER" not in serialized
    assert "SECRET_HISTORY_MODEL_SENTINEL" not in serialized
    assert "SECRET_HISTORY_CONTROLLER_SENTINEL" not in serialized
    assert "mpc_dynamic" not in serialized.lower()
    assert "mpc_pdf_v15" not in serialized.lower()
    assert "ppo_policy" not in serialized.lower()
    assert "EnergyBridge recommends" not in serialized
    assert "synthetic_energybridge" not in serialized.lower()
    assert "fixed probability band" not in (system + user).lower()
    assert "probability floor" not in (system + user).lower()
    assert "probability cap" not in (system + user).lower()


def test_acceptance_prompt_penalizes_generic_explanations_without_method_targets() -> None:
    persona = _load_persona("basic_role_a_commuter_price_cooperative")
    system, _, payload = build_roleplay_acceptance_prompts(
        persona_config=persona,
        appliance_config=persona["appliances"],
        event={"id": "vpp1", "trigger_h": 18.0, "end_h": 19.0},
        proposed_plan={
            "setpoint": 27.5,
            "reason": "Save energy, help the grid, and keep routines unchanged.",
            "appliance_actions": {"washer_start_h": 19.0},
            "method": "SECRET_METHOD_SENTINEL",
            "model": "SECRET_MODEL_SENTINEL",
        },
        default_plan={
            "setpoint": 26.0,
            "appliance_actions": {"washer_start_h": 19.0},
        },
    )
    instruction = system.lower()
    serialized = json.dumps(payload, ensure_ascii=False)

    assert "even when the household is generally cooperative" in instruction
    assert "compare offered_vpp_plan with ordinary_household_plan" in instruction
    assert "benefit or tradeoff" in instruction
    assert "let the explanation materially raise willingness" in instruction
    assert "communication value separately from any correctable physical drawback" in instruction
    assert "merely preserving ordinary comfort, safety, or service earns no extra credit" in instruction
    assert "inherited ordinary actions are context, not new benefits" in instruction
    assert "generic claims" in instruction
    assert "should lower willingness" in instruction
    assert "incomplete request for consent" in instruction
    assert "not positive evidence while that condition is still unmet" in instruction
    assert "an empty conflict list" in instruction
    assert "context_only unless this offer improves them" in instruction
    assert "in 100 comparable situations" in instruction
    assert "prior is a starting point, not a floor" in instruction
    assert "do not apply hidden clipping or canned deltas" in instruction
    assert "explanation specificity" not in instruction
    assert "one explicit negative" not in instruction
    assert "largest-magnitude" not in instruction
    assert "target acceptance rate" not in instruction
    assert "SECRET_METHOD_SENTINEL" not in serialized
    assert "SECRET_MODEL_SENTINEL" not in serialized


def test_acceptance_prompt_treats_conditions_and_unverified_plan_claims_as_negative() -> None:
    persona = _load_persona("basic_role_a_commuter_price_cooperative")
    system, _, payload = build_roleplay_acceptance_prompts(
        persona_config=persona,
        appliance_config=persona["appliances"],
        event={"id": "vpp1", "trigger_h": 18.0, "end_h": 19.0},
        proposed_plan={
            "setpoint": 27.5,
            "reason": "The washer is moved outside the event and every routine is protected.",
            "appliance_actions": {"washer_start_h": 18.5},
        },
        default_plan={
            "setpoint": 26.0,
            "appliance_actions": {"washer_start_h": 17.0},
        },
        user_preference_text=(
            "I will participate only if the washer runs outside the event, and as long as it finishes on time."
        ),
        verified_plan_facts={
            "proposed_setpoint_c": 27.5,
            "vpp_conflicts": ["washer: 18:30 start overlaps VPP 18:00-19:00"],
            "present_services": ["washer"],
            "specified_services": ["washer"],
            "unspecified_services": [],
            "method": "SECRET_METHOD_SENTINEL",
            "model": "SECRET_MODEL_SENTINEL",
        },
    )
    instruction = system.lower()

    assert "unresolved 'if/only if/as long as' conditions" in instruction
    assert "credits it only in the counterfactual" in instruction
    assert "check times and claims against the event, both plans, and verified_offer_facts" in instruction
    assert "exclusive event end is outside the event" in instruction
    assert "do not invent savings, guarantees, outcomes, failures, or personal details" in instruction
    assert payload["event"]["trigger_h"] == 18.0
    assert payload["event"]["end_h"] == 19.0
    assert payload["event"]["trigger_hod"] == 18.0
    assert payload["event"]["end_hod"] == 19.0
    assert "[trigger_hod, end_hod)" in payload["event"]["window_semantics"]
    assert payload["ordinary_household_plan"]["appliance_actions"]["washer_start_h"] == 17.0
    assert payload["offered_vpp_plan"]["appliance_actions"]["washer_start_h"] == 18.5
    assert "only if" in payload["live_household_statement"]
    assert "as long as" in payload["live_household_statement"]
    assert payload["verified_offer_facts"] == {
        "proposed_setpoint_c": 27.5,
        "vpp_conflicts": ["washer: 18:30 start overlaps VPP 18:00-19:00"],
        "present_services": ["washer"],
        "specified_services": ["washer"],
        "unspecified_services": [],
        "event_overlap_note": (
            "vpp_conflicts is the checked overlap result under the event's half-open interval. An empty list means "
            "none of the supplied effective appliance intervals overlaps this event."
        ),
    }
    assert "SECRET_METHOD_SENTINEL" not in json.dumps(payload, ensure_ascii=False)
    assert "SECRET_MODEL_SENTINEL" not in json.dumps(payload, ensure_ascii=False)


def test_verified_missing_service_actions_are_explained_without_controller_identity() -> None:
    persona = _load_persona("basic_role_a_commuter_price_cooperative")
    _, _, payload = build_roleplay_acceptance_prompts(
        persona_config=persona,
        proposed_plan={"setpoint": 26.5, "appliance_actions": {}},
        verified_plan_facts={
            "present_services": ["washer", "water_heater"],
            "specified_services": [],
            "unspecified_services": ["washer", "water_heater"],
            "method": "SECRET_METHOD_SENTINEL",
        },
    )

    facts = payload["verified_offer_facts"]
    assert facts["present_services"] == ["washer", "water_heater"]
    assert facts["specified_services"] == []
    assert facts["unspecified_services"] == ["washer", "water_heater"]
    assert "does not explicitly cover these present household services" in facts["action_coverage_note"]
    assert "SECRET_METHOD_SENTINEL" not in json.dumps(payload, ensure_ascii=False)


def test_sparse_offer_inherits_ordinary_actions_without_format_penalty() -> None:
    persona = _load_persona("basic_role_a_commuter_price_cooperative")
    _, _, payload = build_roleplay_acceptance_prompts(
        persona_config=persona,
        default_plan={
            "setpoint": 25.5,
            "reason": "The ordinary-plan explanation must not become an offer explanation.",
            "appliance_actions": {
                "washer_start_h": 20.0,
                "washer_skip": False,
                "water_heater_preheat_start_h": 16.0,
                "water_heater_preheat_end_h": 18.0,
            },
        },
        proposed_plan={
            "setpoint": 26.0,
            "appliance_actions": {"washer_start_h": 21.0},
            "reason": "OpenAI o3 via DMXAPI produced this DeepSeek-V3 plan.",
        },
        verified_plan_facts={
            "proposed_setpoint_c": 26.0,
            "default_setpoint_c": 25.5,
            "preferred_min_c": 24.0,
            "preferred_max_c": 26.0,
            "preference_tolerance_c": 1.0,
            "comfort_excess_c": 99.0,
            "default_delta_c": 99.0,
            "hvac_off": True,
            "present_services": ["washer", "water_heater"],
            "specified_services": ["washer", "water_heater"],
            "unspecified_services": [],
            "vpp_conflicts": [],
        },
    )

    offered = payload["offered_vpp_plan"]
    effective = payload["effective_plan_if_accepted"]
    assert offered["setpoint_c"] == 26.0
    assert offered["appliance_actions"]["washer_start_h"] == 21.0
    assert "washer_skip" not in offered["appliance_actions"]
    assert "water_heater_preheat_start_h" not in offered["appliance_actions"]
    assert effective["setpoint_c"] == 26.0
    assert effective["appliance_actions"]["washer_start_h"] == 21.0
    assert effective["appliance_actions"]["washer_skip"] is False
    assert effective["appliance_actions"]["water_heater_preheat_start_h"] == 16.0
    assert "inherit the ordinary household plan" in effective["plan_field_semantics"]
    assert effective["reason_shown_to_household"] == offered["reason_shown_to_household"]
    assert "ordinary-plan explanation" not in effective["reason_shown_to_household"]
    facts = payload["verified_offer_facts"]
    assert facts["preferred_min_c"] == 24.0
    assert facts["preference_tolerance_c"] == 1.0
    assert "comfort_excess_c" not in facts
    assert "default_delta_c" not in facts
    assert "hvac_off" not in facts
    assert facts["vpp_conflicts"] == []
    assert "empty list means none" in facts["event_overlap_note"]
    assert facts["setpoint_change_c"] == 0.5
    serialized = json.dumps(payload, ensure_ascii=False).lower()
    for identity in ("openai", "o3", "dmxapi", "deepseek"):
        assert identity not in serialized

    _, _, no_explanation_payload = build_roleplay_acceptance_prompts(
        persona_config=persona,
        default_plan={"setpoint": 25.5, "reason": "Ordinary comfort explanation."},
        proposed_plan={"setpoint": 26.0},
    )
    assert "reason_shown_to_household" not in no_explanation_payload[
        "effective_plan_if_accepted"
    ]


def test_persona_prior_is_neutral_without_relabelling_other_persona_weights() -> None:
    a = _load_persona("basic_role_a_commuter_price_cooperative")
    e = _load_persona("basic_role_e_caregiver_low_dr")
    _, _, payload_a = build_roleplay_acceptance_prompts(persona_config=a)
    _, _, payload_e = build_roleplay_acceptance_prompts(persona_config=e)

    assert payload_a["persona_baseline_acceptance_probability"] == pytest.approx(0.5)
    assert payload_e["persona_baseline_acceptance_probability"] == pytest.approx(0.5)
    assert payload_a["persona_baseline_audit"]["formula"] == "0.5"
    assert payload_a["persona_baseline_audit"]["source"] == (
        "neutral_uninformed_consent_prior"
    )
    assert "not relabelled as acceptance probabilities" in payload_a[
        "persona_baseline_audit"
    ]["note"]
    assert payload_a["household_resume"]["audit"]["profile_fingerprint"] != (
        payload_e["household_resume"]["audit"]["profile_fingerprint"]
    )


def test_normalizer_preserves_arithmetic_model_probability_without_reshaping() -> None:
    normalized = normalize_roleplay_acceptance_response(
        _valid_response(),
        expected_baseline=0.37,
    )

    assert normalized["decision"] == "counteroffer"
    assert normalized["baseline_acceptance_probability"] == 0.37
    assert normalized["final_acceptance_probability"] == 0.32
    assert normalized["acceptance_probability"] == 0.32
    assert normalized["confidence"] == 0.74
    assert normalized["adjustments"][0]["delta"] == -0.08
    assert normalized["counterfactual"]["decision_if_changed"] == "accept"
    assert normalized["normalization"]["adjustment_sum"] == pytest.approx(-0.05)
    assert normalized["normalization"]["arithmetic_residual"] == pytest.approx(0.0)
    assert normalized["normalization"]["expected_baseline"] == 0.37
    assert normalized["hard_veto_applied"] is False


def test_explanation_quality_adjustment_remains_model_authored() -> None:
    response = _valid_response(
        decision="counteroffer",
        baseline_acceptance_probability=0.62,
        adjustments=[{
            "dimension": "proposal evidence quality",
            "delta": -0.29,
            "evidence": "E1",
            "reason": "The offer gives no household-specific benefit or schedule rationale.",
        }],
        final_acceptance_probability=0.33,
    )
    normalized = normalize_roleplay_acceptance_response(
        response,
        expected_baseline=0.62,
    )

    assert normalized["baseline_acceptance_probability"] == 0.62
    assert normalized["adjustments"] == response["adjustments"]
    assert normalized["normalization"]["adjustment_sum"] == pytest.approx(-0.29)
    assert normalized["final_acceptance_probability"] == 0.33
    assert normalized["acceptance_probability"] == 0.33


def test_normalizer_allows_no_change_but_rejects_mismatch_zero_item_and_unexplained_final() -> None:
    with pytest.raises(RoleplayResponseError, match="supplied persona baseline"):
        normalize_roleplay_acceptance_response(
            _valid_response(baseline_acceptance_probability=0.44, final_acceptance_probability=0.41),
            expected_baseline=0.37,
        )

    unchanged = normalize_roleplay_acceptance_response(
        _valid_response(adjustments=[], final_acceptance_probability=0.37),
        expected_baseline=0.37,
    )
    assert unchanged["adjustments"] == []
    assert unchanged["final_acceptance_probability"] == 0.37
    assert unchanged["normalization"]["adjustment_sum"] == 0.0

    with pytest.raises(RoleplayResponseError, match="non-zero signed adjustment"):
        normalize_roleplay_acceptance_response(
            _valid_response(
                adjustments=[{
                    "dimension": "no material change",
                    "delta": 0.0,
                    "evidence": "E1",
                    "reason": "Nothing changed.",
                }],
                final_acceptance_probability=0.37,
            ),
            expected_baseline=0.37,
        )

    with pytest.raises(RoleplayResponseError, match="baseline plus signed adjustments"):
        normalize_roleplay_acceptance_response(
            _valid_response(final_acceptance_probability=0.613),
            expected_baseline=0.37,
        )


def test_normalizer_rejects_adjustment_evidence_sign_contradictions() -> None:
    with pytest.raises(RoleplayResponseError, match="positive adjustments"):
        normalize_roleplay_acceptance_response(
            _valid_response(
                adjustments=[{
                    "dimension": "contradictory positive",
                    "delta": 0.08,
                    "evidence": "E1",
                    "reason": "This cannot cite rejection evidence as a benefit.",
                }],
                final_acceptance_probability=0.45,
            ),
            expected_baseline=0.37,
        )

    with pytest.raises(RoleplayResponseError, match="negative adjustments"):
        normalize_roleplay_acceptance_response(
            _valid_response(
                adjustments=[{
                    "dimension": "contradictory negative",
                    "delta": -0.06,
                    "evidence": "E2",
                    "reason": "This cannot cite acceptance evidence as a penalty.",
                }],
                final_acceptance_probability=0.31,
            ),
            expected_baseline=0.37,
        )


def test_normalizer_rejects_duplicate_evidence_ids() -> None:
    response = _valid_response()
    response["evidence"][1]["id"] = "e1"
    with pytest.raises(RoleplayResponseError, match="evidence ids must be unique"):
        normalize_roleplay_acceptance_response(
            response,
            expected_baseline=0.37,
        )


def test_multiday_event_exposes_local_hour_of_day_for_action_comparison() -> None:
    persona = _load_persona("basic_role_a_commuter_price_cooperative")
    _, _, payload = build_roleplay_acceptance_prompts(
        persona_config=persona,
        event={"id": "day2-vpp", "day": 2, "trigger_h": 42.0, "end_h": 43.0},
        proposed_plan={"appliance_actions": {"washer_start_h": 19.0}},
    )

    event = payload["event"]
    assert event["trigger_h"] == 42.0
    assert event["end_h"] == 43.0
    assert event["trigger_hod"] == 18.0
    assert event["end_hod"] == 19.0
    assert "absolute simulation hours" in event["window_semantics"]


def test_normalizer_allows_only_ordinary_decimal_rounding_residual() -> None:
    response = _valid_response(
        baseline_acceptance_probability=0.3137,
        adjustments=[{
            "dimension": "routine fit",
            "delta": 0.041,
            "evidence": "E2",
            "reason": "The later start preserves the routine.",
        }],
        final_acceptance_probability=0.355,
    )
    normalized = normalize_roleplay_acceptance_response(
        response,
        expected_baseline=0.314,
    )
    assert normalized["final_acceptance_probability"] == 0.355
    assert abs(normalized["normalization"]["arithmetic_residual"]) < (
        PROBABILITY_ROUNDING_TOLERANCE
    )


def test_normalizer_requires_structured_fields_and_uses_only_caller_fallback() -> None:
    with pytest.raises(RoleplayResponseError):
        normalize_roleplay_acceptance_response({"decision": "accept"})

    fallback = _valid_response(
        decision="reject",
        reason="The proposed temperature conflicts with my stated boundary.",
    )
    normalized = normalize_roleplay_acceptance_response(
        "not json",
        fallback=fallback,
        expected_baseline=0.37,
    )

    assert normalized["decision"] == "reject"
    assert normalized["final_acceptance_probability"] == 0.32
    assert normalized["normalization"]["source"] == "caller_fallback"
    assert "roleplay_error" in normalized["normalization"]


def test_hard_safety_veto_is_the_only_probability_override() -> None:
    normalized = normalize_roleplay_acceptance_response(
        _valid_response(
            decision="accept",
            adjustments=[{
                "dimension": "service reliability",
                "delta": 0.54,
                "evidence": "E2",
                "reason": "Every required service remains ready.",
            }],
            final_acceptance_probability=0.91,
        ),
        hard_veto_reasons=["EV cannot reach the required departure SOC."],
        expected_baseline=0.37,
    )

    assert normalized["decision"] == "reject"
    assert normalized["final_acceptance_probability"] == 0.0
    assert normalized["acceptance_probability"] == 0.0
    assert normalized["roleplay_decision_before_veto"] == "accept"
    assert normalized["roleplay_final_acceptance_probability_before_veto"] == 0.91
    assert normalized["hard_veto_applied"] is True
    assert normalized["hard_veto_reasons"] == ["EV cannot reach the required departure SOC."]
    assert normalized["evidence"][-1]["source"] == "hard_safety_veto"


def test_hard_veto_can_return_a_structured_rejection_when_model_output_is_invalid() -> None:
    normalized = normalize_roleplay_acceptance_response(
        "invalid response",
        hard_veto_reasons=["Unsafe thermostat command."],
    )

    assert normalized["decision"] == "reject"
    assert normalized["final_acceptance_probability"] == 0.0
    assert normalized["counterfactual"]["changes"]
    assert normalized["normalization"]["source"] == "hard_safety_veto"
    assert "roleplay_error" in normalized["normalization"]


def test_observable_onboarding_inference_uses_only_public_answer_selections() -> None:
    answers = [
        {
            "id": "vpp_priority",
            "selected_option_ids": ["bill_savings_first"],
            "answer": "Show me a concrete saving while keeping the home comfortable.",
        },
        {
            "id": "thermostat_flexibility",
            "selected_option_ids": ["small_1c_short"],
            "answer": "About one degree for a short event is fine.",
        },
        {
            "id": "appliance_shift_consent",
            "selected_option_ids": ["do_not_move_without_approval"],
            "answer": "Please ask before moving a task.",
        },
        {
            "id": "calendar_routine_constraints",
            "selected_option_ids": ["irregular_confirm_same_day"],
            "answer": "My plans change, so check the same day.",
        },
    ]
    inferred = infer_observable_profile_from_answers(answers)

    assert inferred["inferred_profile"]["cost_grid_priority"] == "high"
    assert inferred["inferred_profile"]["thermostat_flexibility_c"] == 1.0
    assert inferred["inferred_profile"]["automation_preference"] == (
        "ask_before_vpp_specific_changes"
    )
    assert inferred["inferred_profile"]["calendar_routine_sensitivity"] == "high"
    assert inferred["preference_rules"]
    assert inferred["inference_audit"]["hidden_resume_fields_used"] is False


def test_v2_onboarding_drops_hidden_prompt_and_llm_authored_profile(monkeypatch) -> None:
    monkeypatch.setenv("ENERGYBRIDGE_HARNESS_PROFILE", "adaptive_v2")
    captured: dict[str, str] = {}
    simulator = object.__new__(RoleplayUserSimulator)

    def fake_call(system_prompt: str, user_prompt: str) -> dict:
        captured["system"] = system_prompt
        captured["user"] = user_prompt
        return {
            "data": {
                "answers": [
                    {
                        "id": "vpp_priority",
                        "selected_option_ids": ["balanced_tradeoff", "high_trust_auto"],
                        "answer": "My scoring_weights are SECRET_HIDDEN_PROFILE_SENTINEL.",
                    },
                    {
                        "id": "appliance_shift_consent",
                        "selected_option_ids": ["shift_1_2h_deadline_protected"],
                        "answer": "A short shift is fine if the task finishes.",
                    },
                ],
                "inferred_profile": {"leak": "SECRET_LLM_INFERRED_PROFILE"},
                "preference_rules": ["SECRET_LLM_RULE"],
            },
            "raw_response": "SECRET_RAW_ROLEPLAY_RESPONSE",
            "system_prompt": "SECRET_RETURNED_SYSTEM_PROMPT",
            "user_prompt": "SECRET_RETURNED_USER_PROMPT",
            "metrics": {"used": True, "model": "roleplay-test"},
        }

    monkeypatch.setattr(simulator, "_call_json", fake_call)
    result = simulator.answer_onboarding_questions(
        persona={
            "id": "private-household",
            "description": "SECRET_PERSONA_DESCRIPTION",
            "preferences": {
                "scoring_weights": {"comfort": 0.9, "energy": 0.05, "vpp": 0.05},
                "vpp_override_prob": 0.9,
            },
        },
        questions=[
            {"id": "vpp_priority", "question": "What matters?", "options": []},
            {"id": "appliance_shift_consent", "question": "May tasks move?", "options": []},
        ],
    )
    serialized = json.dumps(result, ensure_ascii=False)

    assert "only `answers`" in captured["user"]
    assert "inferred_profile" in captured["user"]  # present only in the prohibition
    assert set(result) == {"data", "metrics", "privacy"}
    assert result["data"]["inference_audit"]["hidden_resume_fields_used"] is False
    assert result["data"]["inferred_profile"]["strategy_bias"] == "balanced_middle"
    assert result["data"]["inferred_profile"]["appliance_flexibility"] == (
        "shift_1_2h_if_deadlines_protected"
    )
    assert result["privacy"]["hidden_resume_returned"] is False
    assert "high_trust_auto" not in serialized
    assert "SECRET" not in serialized


def test_v2_feedback_prompt_sanitizes_resume_provenance_and_keeps_household_facts(
    monkeypatch,
) -> None:
    monkeypatch.setenv("ENERGYBRIDGE_HARNESS_PROFILE", "adaptive_v2")
    captured: dict[str, str] = {}
    simulator = object.__new__(RoleplayUserSimulator)

    def fake_call(system_prompt: str, user_prompt: str) -> dict:
        captured["system"] = system_prompt
        captured["user"] = user_prompt
        return {
            "data": {
                "satisfaction_score": 4,
                "comfort_score": 4,
                "energy_score": 3,
                "vpp_score": 4,
                "satisfaction_label": "satisfied",
                "comment": "The plan protected my evening shower.",
            },
            "metrics": {"used": True},
        }

    monkeypatch.setattr(simulator, "_call_json", fake_call)
    simulator.generate_feedback(
        persona={
            "id": "feedback-household",
            "description": (
                "I need hot water for my evening shower. "
                "OpenAI SECRET_FEEDBACK_MODEL_SENTINEL must not appear."
            ),
            "agent_context": "ideal DR candidate for EnergyBridge evaluation tests",
            "calendar": {
                "source": "synthetic_energybridge_private_calendar",
                "days": [],
            },
        },
        turn_index=1,
        selected_strategy={"pre_event_roleplay_acceptance": {"acceptance_probability": 0.63}},
        projected_control_plan={"setpoint": 25.0},
        projected_safety_report={"status": "approved"},
    )

    prompt = captured["system"] + captured["user"]
    lowered = prompt.lower()
    assert "evening shower" in lowered
    assert "openai" not in lowered
    assert "energybridge" not in lowered
    assert "synthetic_energybridge" not in lowered
    assert "secret_feedback_model_sentinel" not in lowered
    assert "ideal dr candidate" not in lowered
    assert "controller_context_source" not in lowered
