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
        "baseline_acceptance_probability": 0.31,
        "adjustments": [
            {
                "dimension": "return-home comfort",
                "delta": -0.07,
                "evidence": "The event overlaps the usual return-home period.",
                "reason": "I do not want to arrive to a warmer home.",
            },
            {
                "dimension": "appliance service",
                "delta": 0.04,
                "evidence": "The washer still completes before its deadline.",
                "reason": "The schedule preserves the chore I need today.",
            },
        ],
        "final_acceptance_probability": 0.28,
        "confidence": 0.74,
        "evidence": [
            {
                "source": "resume",
                "fact": "Comfort on arrival is a recurring preference.",
                "effect": "supports_rejection",
            },
            {
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
                "generated by SECRET_AGENT_MODEL with mpc_dynamic."
            ),
            "appliance_actions": {
                "washer_start_h": 19.5,
                "washer_skip": False,
                "washer": {
                    "start_h": 19.5,
                    "status": "selected by SECRET_NESTED_MODEL_SENTINEL",
                    "model": "SECRET_NESTED_MODEL_SENTINEL",
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
    assert "EnergyBridge recommends" not in serialized
    assert "fixed probability band" not in (system + user).lower()
    assert "probability floor" not in (system + user).lower()
    assert "probability cap" not in (system + user).lower()


def test_persona_prior_is_transparent_and_varies_by_family() -> None:
    a = _load_persona("basic_role_a_commuter_price_cooperative")
    e = _load_persona("basic_role_e_caregiver_low_dr")
    _, _, payload_a = build_roleplay_acceptance_prompts(persona_config=a)
    _, _, payload_e = build_roleplay_acceptance_prompts(persona_config=e)

    assert payload_a["persona_baseline_acceptance_probability"] == pytest.approx(0.8)
    assert payload_e["persona_baseline_acceptance_probability"] == pytest.approx(0.1)
    assert payload_a["persona_baseline_audit"]["formula"] == "1 - vpp_override_prob"
    assert payload_a["household_resume"]["audit"]["profile_fingerprint"] != (
        payload_e["household_resume"]["audit"]["profile_fingerprint"]
    )


def test_normalizer_preserves_arithmetic_model_probability_without_reshaping() -> None:
    normalized = normalize_roleplay_acceptance_response(
        _valid_response(),
        expected_baseline=0.31,
    )

    assert normalized["decision"] == "counteroffer"
    assert normalized["baseline_acceptance_probability"] == 0.31
    assert normalized["final_acceptance_probability"] == 0.28
    assert normalized["acceptance_probability"] == 0.28
    assert normalized["confidence"] == 0.74
    assert normalized["adjustments"][0]["delta"] == -0.07
    assert normalized["counterfactual"]["decision_if_changed"] == "accept"
    assert normalized["normalization"]["adjustment_sum"] == pytest.approx(-0.03)
    assert normalized["normalization"]["arithmetic_residual"] == pytest.approx(0.0)
    assert normalized["normalization"]["expected_baseline"] == 0.31
    assert normalized["hard_veto_applied"] is False


def test_normalizer_rejects_baseline_mismatch_empty_adjustments_and_unexplained_final() -> None:
    with pytest.raises(RoleplayResponseError, match="supplied persona baseline"):
        normalize_roleplay_acceptance_response(
            _valid_response(baseline_acceptance_probability=0.44, final_acceptance_probability=0.41),
            expected_baseline=0.31,
        )

    with pytest.raises(RoleplayResponseError, match="at least one signed item"):
        normalize_roleplay_acceptance_response(
            _valid_response(adjustments=[], final_acceptance_probability=0.31),
            expected_baseline=0.31,
        )

    with pytest.raises(RoleplayResponseError, match="non-zero signed adjustment"):
        normalize_roleplay_acceptance_response(
            _valid_response(
                adjustments=[{
                    "dimension": "no material change",
                    "delta": 0.0,
                    "evidence": "E1",
                    "reason": "Nothing changed.",
                }],
                final_acceptance_probability=0.31,
            ),
            expected_baseline=0.31,
        )

    with pytest.raises(RoleplayResponseError, match="baseline plus signed adjustments"):
        normalize_roleplay_acceptance_response(
            _valid_response(final_acceptance_probability=0.613),
            expected_baseline=0.31,
        )


def test_normalizer_allows_only_ordinary_decimal_rounding_residual() -> None:
    response = _valid_response(
        baseline_acceptance_probability=0.3137,
        adjustments=[{
            "dimension": "routine fit",
            "delta": 0.041,
            "evidence": "E1",
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
        expected_baseline=0.31,
    )

    assert normalized["decision"] == "reject"
    assert normalized["final_acceptance_probability"] == 0.28
    assert normalized["normalization"]["source"] == "caller_fallback"
    assert "roleplay_error" in normalized["normalization"]


def test_hard_safety_veto_is_the_only_probability_override() -> None:
    normalized = normalize_roleplay_acceptance_response(
        _valid_response(
            decision="accept",
            adjustments=[{
                "dimension": "service reliability",
                "delta": 0.60,
                "evidence": "E1",
                "reason": "Every required service remains ready.",
            }],
            final_acceptance_probability=0.91,
        ),
        hard_veto_reasons=["EV cannot reach the required departure SOC."],
        expected_baseline=0.31,
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
