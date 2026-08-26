from __future__ import annotations

from types import SimpleNamespace

from experiments.benchmark import family_runner as fr


def _planning_inputs() -> dict:
    return {
        "observable_state": {
            "control_limits": {"setpoint": {"min": 22.0, "max": 28.0}},
        },
        "observable_profile": {
            "summary": "The household prefers a quiet evening.",
        },
        "memory": {
            "relevant_episodes": [{"evidence_ref": "event:prior", "feedback": "This worked."}],
        },
        "event": {"event_id": "event-1", "trigger_h": 18.0, "end_h": 19.0},
        "explicit_constraints": [
            {"constraint_id": "sp", "kind": "required", "path": "/setpoint"},
            {"constraint_id": "appliances", "kind": "required", "path": "/appliances"},
        ],
    }


def _candidate(candidate_id: str, setpoint: float) -> dict:
    return {
        "candidate_id": candidate_id,
        "plan": {
            "setpoint": setpoint,
            "next_check_hour": None,
            "appliances": {},
            "reason": f"candidate {candidate_id}",
        },
        "objective_estimates": {
            "comfort": {"value": 4.0, "direction": "maximize", "confidence": 0.7},
        },
    }


def test_portfolio_keeps_model_choice_across_distinct_candidates() -> None:
    raw = {
        "candidate_plans": [_candidate("low_drift", 25.0), _candidate("late_restore", 27.0)],
        "selected_candidate_id": "late_restore",
        "selection_reason": "The current calendar makes the later restoration preferable.",
    }

    resolution = fr._adaptive_v3_resolve_planning_response(
        raw,
        planning_inputs=_planning_inputs(),
    )

    assert resolution["status"] == "selected"
    assert resolution["selected_candidate_id"] == "late_restore"
    assert resolution["selected_executable_plan"]["setpoint"] == 27.0
    assert resolution["selected_raw_plan"]["reason"] == "candidate late_restore"
    assert resolution["semantic_replan_attempted"] is False


def test_advisor_is_compared_but_never_replaces_invalid_model_selection() -> None:
    advisors = [{
        "source_skill": "mpc_dynamic",
        "plan": {"setpoint": 26.0, "appliances": {}, "reason": "MPC proposal"},
    }]
    invalid = {
        "candidate_plans": [_candidate("model_plan", 25.5)],
        "selected_candidate_id": "advisor_01",
    }
    retries: list[dict] = []

    def replan(feedback: dict) -> dict:
        retries.append(feedback)
        return invalid

    resolution = fr._adaptive_v3_resolve_planning_response(
        invalid,
        planning_inputs=_planning_inputs(),
        advisor_candidates=advisors,
        replan_fn=replan,
    )

    assert resolution["status"] == "fallback_required"
    assert resolution["selected_executable_plan"] is None
    assert resolution["advisor_override_allowed"] is False
    assert len(retries) == 1
    assert len(resolution["attempts"]) == 2
    assert retries[0]["validator_reason"] == "selected ID is not a base-model candidate"


def test_invalid_selection_gets_one_semantic_replan_then_model_choice_is_evaluated() -> None:
    first = {
        "candidate_plans": [_candidate("only", 25.0)],
        "selected_candidate_id": "missing",
    }
    calls: list[dict] = []

    def replan(feedback: dict) -> dict:
        calls.append(feedback)
        return {
            "candidate_plans": [_candidate("revised_a", 24.5), _candidate("revised_b", 26.5)],
            "selected_candidate_id": "revised_b",
            "selection_reason": "The revised schedule resolves the constraint.",
        }

    resolution = fr._adaptive_v3_resolve_planning_response(
        first,
        planning_inputs=_planning_inputs(),
        replan_fn=replan,
    )

    assert len(calls) == 1
    assert resolution["semantic_replan_attempted"] is True
    assert resolution["selected_candidate_id"] == "revised_b"
    assert resolution["selected_executable_plan"]["setpoint"] == 26.5
    assert [attempt["kind"] for attempt in resolution["attempts"]] == [
        "initial_model_response",
        "semantic_replan",
    ]


def test_semantic_replan_exception_audit_is_privacy_clean() -> None:
    first = {
        "candidate_plans": [_candidate("only", 25.0)],
        "selected_candidate_id": "missing",
    }

    def replan(_feedback: dict) -> dict:
        raise RuntimeError(
            "provider=SecretProvider model=SecretModel evaluator=SecretJudge "
            "api_key=sk-EXCEPTIONSECRET123 endpoint=https://private.example/v1"
        )

    resolution = fr._adaptive_v3_resolve_planning_response(
        first,
        planning_inputs=_planning_inputs(),
        replan_fn=replan,
    )

    error = resolution["semantic_replan_error"]
    assert error.startswith("RuntimeError:")
    for hidden in (
        "SecretProvider",
        "SecretModel",
        "SecretJudge",
        "sk-EXCEPTIONSECRET123",
        "private.example",
    ):
        assert hidden not in error


def test_legacy_single_plan_still_passes_common_evaluator() -> None:
    legacy = {
        "setpoint": 25.5,
        "next_check_hour": None,
        "appliances": {},
        "reason": "A valid old single-plan response.",
    }

    resolution = fr._adaptive_v3_resolve_planning_response(
        legacy,
        planning_inputs=_planning_inputs(),
    )

    assert resolution["status"] == "selected"
    assert resolution["selected_executable_plan"]["setpoint"] == 25.5
    audit = resolution["final_portfolio_audit"]
    assert audit["legacy_single_plan"] is True
    assert audit["model_selection"]["status"] == "selected"


def test_runtime_contract_error_replans_once_instead_of_patching_choice() -> None:
    first = {
        "candidate_plans": [_candidate("missing_service", 25.0)],
        "selected_candidate_id": "missing_service",
    }

    def policy_errors(plan: dict) -> list[str]:
        return [] if plan.get("appliances", {}).get("washer_start_h") == 20.0 else ["washer command missing"]

    resolution = fr._adaptive_v3_resolve_planning_response(
        first,
        planning_inputs=_planning_inputs(),
        policy_error_fn=policy_errors,
        replan_fn=lambda feedback: {
            "candidate_plans": [{
                "candidate_id": "service_fixed",
                "plan": {"setpoint": 25.0, "appliances": {"washer_start_h": 20.0}},
            }],
            "selected_candidate_id": "service_fixed",
        },
    )

    assert resolution["status"] == "selected"
    assert resolution["selected_candidate_id"] == "service_fixed"
    assert resolution["selected_executable_plan"]["appliances"]["washer_start_h"] == 20.0
    assert resolution["attempts"][0]["runtime_contract_errors"] == ["washer command missing"]


def test_final_planning_prompt_anonymizes_identity_and_target_fields() -> None:
    inputs = _planning_inputs()
    inputs["observable_profile"].update({
        "method_name": "EnergyBridge",
        "model_name": "hidden-base-model-xyz",
        "target_acceptance": 0.812345,
    })
    advisors = [{
        "source_skill": "HEMA",
        "provider": "OpenAI",
        "plan": {"setpoint": 26.0, "appliances": {}, "reason": "MPC chose this"},
    }]

    system_prompt, user_prompt = fr._adaptive_v3_planning_prompts(
        inputs,
        advisor_candidates=advisors,
        allow_skill_request=False,
    )
    combined = system_prompt + "\n" + user_prompt

    assert "hidden-base-model-xyz" not in combined
    assert "0.812345" not in combined
    assert "EnergyBridge" not in combined
    assert "HEMA" not in combined
    assert "OpenAI" not in combined
    assert "MPC" not in combined
    assert "at least one conservative" not in combined
    assert "advisor_01" in combined


def test_observable_adapter_prefers_session_capsules_and_lists_live_requirements() -> None:
    loop = SimpleNamespace(
        current_occupied=True,
        current_occupancy_count=2.0,
        current_occupancy_source="shared_calendar",
        agent_profile_capsule_by_event_id={"e1": {"text": "Evening comfort matters."}},
        agent_memory_capsule_by_event_id={"e1": {"relevant_episodes": [{"id": "old"}]}},
        agent_preference_memory={"persona_id": "must-not-be-needed"},
    )
    inputs = fr._adaptive_v3_observable_planning_inputs(
        loop,
        event_id="e1",
        sim_h=8.0,
        hod=8.0,
        temp=25.0,
        out_t=31.0,
        facility_w=1200.0,
        observable_calendar={"occupied": True},
        memory_event={"id": "e1", "trigger_h": 18.0, "end_h": 19.0},
        vpp_event=None,
        user_input="Please preserve dinner.",
        appliance_config={
            "washer": {"present": True, "duration_h": 1.0, "hidden_persona": "do not copy"},
        },
        setpoint_min_c=22.0,
        setpoint_max_c=28.0,
    )

    assert inputs["observable_profile"] == {"text": "Evening comfort matters."}
    assert inputs["memory"] == {"relevant_episodes": [{"id": "old"}]}
    assert inputs["observable_state"]["facility_load_kw"] == 1.2
    assert inputs["observable_state"]["required_appliance_action_fields"] == [
        "washer_start_h",
        "washer_skip",
    ]
    assert "hidden_persona" not in inputs["observable_state"]["device_capabilities"]["washer"]
