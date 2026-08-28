from __future__ import annotations

import json
from copy import deepcopy
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
    impact = resolution["final_portfolio_audit"]["professional_impact_evidence"]
    assert impact["ranking_performed"] is False
    assert impact["selected_candidate_id"] is None


def test_resolution_audits_the_bounded_calibration_memory_seen_by_model() -> None:
    inputs = _planning_inputs()
    calibration = {
        "schema_version": "energybridge.calibration_capsule.v1",
        "source_record_count": 1,
        "signal_summaries": {
            "service_completion": {
                "observation_count": 2,
                "agreement_count": 2,
                "disagreement_count": 0,
            }
        },
        "recent_disagreements": [],
        "policy_update_performed": False,
        "ranking_performed": False,
    }
    inputs["memory"]["professional_calibration"] = calibration

    resolution = fr._adaptive_v3_resolve_planning_response(
        {
            "candidate_plans": [_candidate("model-owned", 26.0)],
            "selected_candidate_id": "model-owned",
        },
        planning_inputs=inputs,
    )

    assert resolution["calibration_memory_used"] == calibration
    assert resolution["calibration_memory_used"]["policy_update_performed"] is False
    assert resolution["calibration_memory_used"]["ranking_performed"] is False


def test_model_can_request_one_decision_relevant_clarification_without_forcing_it() -> None:
    request = fr._adaptive_v3_requested_clarification({
        "clarification_request": {
            "question": "Is the 21:00 shower fixed tonight?",
            "decision_relevance": "A fixed shower changes the hot-water window.",
            "evidence_gap": "/observable_profile/decision_unknowns/0",
        }
    })

    assert request is not None
    assert request["question"] == "Is the 21:00 shower fixed tonight?"
    assert request["evidence_gap_citations"] == [
        "/observable_profile/decision_unknowns/0"
    ]
    assert fr._adaptive_v3_requested_clarification({
        "clarification_request": {"question": "What do you prefer?"}
    }) is None
    assert fr._adaptive_v3_requested_clarification({
        "candidate_plans": [_candidate("ready", 26.0)],
        "selected_candidate_id": "ready",
        "information_requests": ["Optional future question"],
    }) is None
    assert fr._adaptive_v3_requested_clarification({
        "skill_calls": ["forecast_control"],
        "clarification_request": {
            "question": "Ask too?",
            "decision_relevance": "Ambiguous request types.",
        },
    }) is None


def test_direct_clarification_reply_becomes_observable_current_evidence(monkeypatch) -> None:
    from energybridge.llm import roleplay_user

    captured: dict = {}

    class FakeRoleplayUserSimulator:
        def answer_context_question(self, persona, question, scenario):
            captured.update({"persona": persona, "question": question, "scenario": scenario})
            return {
                "data": {
                    "answer": "The 21:00 shower is fixed, but laundry can move later.",
                    "certainty": "known",
                    "conditions": "Keep hot water ready by 21:00.",
                },
                "metrics": {"used": True, "token_usage": {"prompt_tokens": 10}},
                "privacy": {"hidden_resume_returned": False},
            }

    monkeypatch.setattr(roleplay_user, "RoleplayUserSimulator", FakeRoleplayUserSimulator)
    inputs = _planning_inputs()
    inputs["observable_profile"]["decision_unknowns"] = [{
        "question_id": "shower_time",
        "dimension": "routine_protection",
        "question": "Is the shower time fixed?",
        "reason": "limited_evidence",
    }]
    request = {
        "request_id": "information_request_01",
        "question": "Is the 21:00 shower fixed tonight?",
        "decision_relevance": "It changes the hot-water schedule.",
        "linked_question_id": "shower_time",
        "evidence_gap_citations": ["/observable_profile/decision_unknowns/0"],
    }

    updated, audit, metrics = fr._adaptive_v3_answer_clarification(
        inputs,
        request,
        persona_config={"description": "I shower at 21:00."},
    )
    reply = updated["observable_profile"]["event_clarification"]

    assert inputs["observable_profile"].get("event_clarification") is None
    assert reply["source"] == "direct_household_clarification"
    assert reply["certainty"] == "known"
    assert "laundry can move later" in reply["answer"]
    assert audit["status"] == "answered"
    assert audit["question_selected_or_scored_by_harness"] is False
    assert metrics["used"] is True
    assert captured["scenario"]["event"]["event_id"] == "event-1"
    assert "persona" not in json.dumps(updated, ensure_ascii=False).lower()


def test_candidate_level_explanation_reaches_selected_raw_and_executable_plan() -> None:
    explanation = {
        "natural_language": "Use a household-specific schedule with a supported tradeoff.",
        "expected_benefit": "A measured comparison is available.",
    }
    raw = {
        "candidate_plans": [
            {
                **_candidate("explained", 26.0),
                "strategy_explanation": explanation,
            }
        ],
        "selected_candidate_id": "explained",
    }

    resolution = fr._adaptive_v3_resolve_planning_response(
        raw,
        planning_inputs=_planning_inputs(),
    )

    assert resolution["selected_executable_plan"]["strategy_explanation"] == explanation
    assert resolution["selected_raw_plan"]["strategy_explanation"] == explanation


def test_selected_plan_carries_method_blind_user_visible_assurance() -> None:
    inputs = _planning_inputs()
    inputs["observable_state"].update({
        "ordinary_plan": {
            "setpoint": 25.0,
            "appliances": {"washer_start_h": 18.0, "washer_skip": False},
        },
        "device_capabilities": {
            "ac": {"present": True, "mode": "cooling"},
            "washer": {
                "present": True,
                "service_required_today": True,
                "earliest_h": 8.0,
                "latest_h": 22.0,
                "duration_h": 2.0,
                "power_kw": 1.5,
            },
        },
        "hourly_tariff": {
            "unit": "normalized/kWh",
            "hours": [{"hour": hour, "price": 1.0} for hour in range(24)],
        },
    })
    raw = {
        "candidate_plans": [{
            "candidate_id": "assured",
            "plan": {
                "setpoint": 26.0,
                "appliances": {"washer_start_h": 19.0, "washer_skip": False},
                "reason": "The washer finishes before the household deadline.",
            },
        }],
        "selected_candidate_id": "assured",
    }

    resolution = fr._adaptive_v3_resolve_planning_response(
        raw,
        planning_inputs=inputs,
    )

    selected = resolution["selected_executable_plan"]
    washer = selected["projected_service_outcomes"]["devices"]["washer"]
    assert washer["service_margin_h"] == 1
    assert washer["vpp_overlap_h"] == 0
    assert selected["projected_cost"]["unsupported_claims_must_remain_unknown"] is True
    assert "method" not in json.dumps(selected["projected_service_outcomes"]).lower()


def test_model_can_revise_its_own_choice_after_professional_evidence() -> None:
    inputs = _planning_inputs()
    inputs["observable_state"].update({
        "ordinary_plan": {"setpoint": 25.0, "appliances": {}},
        "device_capabilities": {"ac": {"present": True, "mode": "cooling"}},
        "hourly_tariff": {"hours": [{"hour": hour, "price": 1.0} for hour in range(24)]},
    })
    first = {
        "candidate_plans": [_candidate("cooler", 24.0), _candidate("warmer", 27.0)],
        "selected_candidate_id": "cooler",
    }
    reviews: list[dict] = []

    def review(evidence: dict) -> dict:
        reviews.append(evidence)
        return {
            "candidate_plans": [_candidate("cooler", 24.0), _candidate("warmer", 27.0)],
            "selected_candidate_id": "warmer",
            "selection_reason": "The directional evidence changes my tradeoff judgment.",
        }

    resolution = fr._adaptive_v3_resolve_planning_response(
        first,
        planning_inputs=inputs,
        impact_review_fn=review,
    )

    assert len(reviews) == 1
    review_evidence = reviews[0]["professional_impact_evidence"]
    assert review_evidence["schema_version"] == "energybridge.impact_review_capsule.v1"
    assert review_evidence["candidate_count"] == 2
    assert "evidence_paths" not in json.dumps(review_evidence)
    cards = review_evidence["candidate_impacts"]
    direction_index = review_evidence["hvac_impact_columns"].index(
        "expected_demand_direction"
    )
    assert cards[0]["hvac_impact_values"][direction_index] == "higher_cooling_demand"
    assert cards[1]["hvac_impact_values"][direction_index] == "lower_cooling_demand"
    assert reviews[0]["professional_impact_evidence"]["selected_candidate_id"] is None
    assert resolution["selected_candidate_id"] == "warmer"
    assert resolution["evidence_review_attempted"] is True
    assert [attempt["kind"] for attempt in resolution["attempts"]] == [
        "initial_model_response",
        "model_evidence_deliberation",
    ]
    projection = resolution["attempts"][1]["review_evidence_projection"]
    assert projection == {
        "schema_version": "energybridge.impact_review_capsule.v1",
        "source_schema_version": resolution["attempts"][0]["evaluation"][
            "professional_impact_evidence"
        ]["schema_version"],
        "candidate_count": 2,
        "ranking_performed": False,
    }
    full_audit = resolution["final_portfolio_audit"]["professional_impact_evidence"]
    assert full_audit["schema_version"] != review_evidence["schema_version"]


def test_exact_impact_review_request_reuses_sanitized_model_deliberation() -> None:
    cache: dict[str, dict] = {}
    calls: list[int] = []

    def model_review() -> dict:
        calls.append(1)
        return {
            "candidate_plans": [_candidate("confirmed", 26.0)],
            "selected_candidate_id": "confirmed",
            "selection_reason": "The checked evidence supports this tradeoff.",
        }

    def review(payload: dict) -> dict:
        return fr._adaptive_v3_cached_impact_review(
            cache,
            scope="event-1:pre_event",
            evidence_payload=payload,
            call_model=model_review,
        )

    initial = {
        "candidate_plans": [_candidate("initial", 26.0)],
        "selected_candidate_id": "initial",
    }
    first = fr._adaptive_v3_resolve_planning_response(
        initial,
        planning_inputs=_planning_inputs(),
        impact_review_fn=review,
    )
    second = fr._adaptive_v3_resolve_planning_response(
        deepcopy(initial),
        planning_inputs=deepcopy(_planning_inputs()),
        impact_review_fn=review,
    )

    assert len(calls) == 1
    assert first["selected_candidate_id"] == "confirmed"
    assert second["selected_candidate_id"] == "confirmed"
    first_orchestration = first["attempts"][1]["review_orchestration"]
    second_orchestration = second["attempts"][1]["review_orchestration"]
    assert first_orchestration["cache_status"] == "miss"
    assert first_orchestration["provider_call_performed"] is True
    assert second_orchestration["cache_status"] == "exact_evidence_hit"
    assert second_orchestration["provider_call_performed"] is False
    assert (
        first_orchestration["request_fingerprint"]
        == second_orchestration["request_fingerprint"]
    )

    changed = deepcopy(initial)
    changed["candidate_plans"][0]["plan"]["setpoint"] = 27.0
    fr._adaptive_v3_resolve_planning_response(
        changed,
        planning_inputs=_planning_inputs(),
        impact_review_fn=review,
    )
    assert len(calls) == 2


def test_deferred_clarification_preserves_model_question_only_for_material_choice() -> None:
    resolution = {
        "status": "selected",
        "selected_candidate_id": "ordinary",
        "final_portfolio_audit": {
            "candidate_lifecycles": [
                {"candidate_id": "ordinary", "origin": "model", "feasible": True},
                {"candidate_id": "lower_cost", "origin": "model", "feasible": True},
            ],
            "professional_impact_evidence": {
                "candidate_impacts": [
                    {
                        "candidate_id": "ordinary",
                        "offer_specific_comparison": {
                            "offer_materiality": "no_observable_physical_change",
                            "supported_benefit_claims": [],
                        },
                    },
                    {
                        "candidate_id": "lower_cost",
                        "offer_specific_comparison": {
                            "offer_materiality": "observable_physical_change",
                            "supported_benefit_claims": [{
                                "kind": "normalized_fixed_load_cost_reduction",
                                "amount": 2.7,
                            }],
                        },
                    },
                ],
            },
            "information_acquisition": {
                "requests": [{
                    "request_id": "information_request_01",
                    "question": "Would you prioritize routine timing or the measured tariff reduction?",
                    "decision_relevance": "The answer could change which model candidate is selected.",
                    "grounded_in_supplied_unknown": True,
                    "decision_relevance_stated": True,
                }],
            },
        },
    }

    request = fr._adaptive_v3_deferred_clarification_request(resolution)

    assert request["question"].startswith("Would you prioritize")
    assert request["selected_candidate_id"] == "ordinary"
    assert request["material_alternatives"] == [{
        "candidate_id": "lower_cost",
        "supported_benefit_kinds": ["normalized_fixed_load_cost_reduction"],
    }]
    assert request["question_authored_by_harness"] is False
    assert request["selection_changed_by_harness"] is False


def test_deferred_clarification_does_not_force_question_or_material_choice() -> None:
    base = {
        "status": "selected",
        "selected_candidate_id": "ordinary",
        "final_portfolio_audit": {
            "candidate_lifecycles": [
                {"candidate_id": "ordinary", "origin": "model", "feasible": True},
                {"candidate_id": "different", "origin": "model", "feasible": True},
            ],
            "professional_impact_evidence": {"candidate_impacts": [
                {
                    "candidate_id": "ordinary",
                    "offer_specific_comparison": {
                        "offer_materiality": "no_observable_physical_change",
                    },
                },
                {
                    "candidate_id": "different",
                    "offer_specific_comparison": {
                        "offer_materiality": "observable_physical_change",
                        "supported_benefit_claims": [],
                    },
                },
            ]},
            "information_acquisition": {"requests": [{
                "question": "Should I change the routine?",
                "decision_relevance": "It could affect the plan.",
                "grounded_in_supplied_unknown": True,
                "decision_relevance_stated": True,
            }]},
        },
    }
    assert fr._adaptive_v3_deferred_clarification_request(base) is None

    with_benefit = deepcopy(base)
    alt = with_benefit["final_portfolio_audit"]["professional_impact_evidence"][
        "candidate_impacts"
    ][1]["offer_specific_comparison"]
    alt["supported_benefit_claims"] = [{"kind": "measured_reduction"}]
    with_benefit["final_portfolio_audit"]["information_acquisition"]["requests"] = []
    assert fr._adaptive_v3_deferred_clarification_request(with_benefit) is None


def test_vpp_demand_uses_observable_capacity_when_reference_data_is_missing() -> None:
    demand = fr._call_vpp_demand_agent(
        "event-1",
        {"status": "not_computed"},
        household_capacity={
            "assessment": {
                "committable_kw": 1.4,
                "recommended_bid_kw": 1.0,
                "success_probability": 0.73,
                "safety_margin": 0.7,
            }
        },
        observed_baseline_kw=2.8,
        duration_h=1.5,
    )

    assert demand["source"] == "state_physical_capacity_envelope"
    assert demand["baseline_kwh"] == 4.2
    assert demand["target_shed_kw"] == 1.0
    assert demand["target_shed_kwh"] == 1.5
    assert demand["target_kwh"] == 2.7
    assert demand["capacity_success_probability"] == 0.73
    assert demand["reference_quantification_available"] is False


def test_reference_quantification_remains_authoritative_when_available() -> None:
    demand = fr._call_vpp_demand_agent(
        "event-1",
        {
            "status": "computed",
            "duration_hours": 1.0,
            "avg_p_base_q50_kw": 3.0,
            "vpp_target_kwh": 1.7,
            "vpp_target_capacity_120_kw": 1.3,
            "vpp_target_capacity_energy_kwh": 1.3,
        },
        household_capacity={
            "assessment": {
                "committable_kw": 0.4,
                "recommended_bid_kw": 0.2,
            }
        },
        observed_baseline_kw=2.0,
    )

    assert demand["source"] == "total_quantification_120"
    assert demand["target_kwh"] == 1.7
    assert demand["target_shed_kw"] == 1.3


def test_invalid_evidence_review_gets_semantic_replan_without_tool_selection() -> None:
    first = {
        "candidate_plans": [_candidate("valid", 25.0)],
        "selected_candidate_id": "valid",
    }
    feedback: list[dict] = []
    resolution = fr._adaptive_v3_resolve_planning_response(
        first,
        planning_inputs=_planning_inputs(),
        impact_review_fn=lambda evidence: {
            "candidate_plans": [_candidate("reviewed", 26.0)],
            "selected_candidate_id": "not-a-candidate",
        },
        replan_fn=lambda findings: feedback.append(findings) or {
            "candidate_plans": [_candidate("repaired", 26.5)],
            "selected_candidate_id": "repaired",
        },
    )

    assert len(feedback) == 1
    assert resolution["selected_candidate_id"] == "repaired"
    assert resolution["semantic_replan_attempted"] is True
    assert [attempt["kind"] for attempt in resolution["attempts"]] == [
        "initial_model_response",
        "model_evidence_deliberation",
        "semantic_replan",
    ]


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


def test_invalid_selection_gets_bounded_semantic_replan_then_model_choice_is_evaluated() -> None:
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


def test_second_semantic_replan_can_resolve_a_distinct_machine_invalid_revision() -> None:
    first = {
        "candidate_plans": [_candidate("only", 25.0)],
        "selected_candidate_id": "missing",
    }
    calls: list[dict] = []

    def replan(feedback: dict) -> dict:
        calls.append(feedback)
        if len(calls) == 1:
            return {
                "candidate_plans": [_candidate("still_invalid", 25.5)],
                "selected_candidate_id": "also_missing",
            }
        return {
            "candidate_plans": [_candidate("resolved", 26.0)],
            "selected_candidate_id": "resolved",
            "selection_reason": "The second revision resolves the remaining machine finding.",
        }

    resolution = fr._adaptive_v3_resolve_planning_response(
        first,
        planning_inputs=_planning_inputs(),
        replan_fn=replan,
    )

    assert len(calls) == 2
    assert resolution["status"] == "selected"
    assert resolution["selected_candidate_id"] == "resolved"
    assert [attempt["kind"] for attempt in resolution["attempts"]] == [
        "initial_model_response",
        "semantic_replan",
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


def test_required_service_skip_triggers_model_semantic_replan() -> None:
    first = {
        "candidate_plans": [{
            "candidate_id": "cancel_service",
            "plan": {
                "setpoint": 25.0,
                "appliances": {"dishwasher_skip": True},
            },
        }],
        "selected_candidate_id": "cancel_service",
    }
    config = {"dishwasher": {"present": True, "service_required_today": True}}

    resolution = fr._adaptive_v3_resolve_planning_response(
        first,
        planning_inputs=_planning_inputs(),
        policy_error_fn=lambda plan: fr._adaptive_v3_appliance_action_contract_errors(
            plan.get("appliances", {}),
            config,
        ),
        replan_fn=lambda feedback: {
            "candidate_plans": [{
                "candidate_id": "complete_service",
                "plan": {
                    "setpoint": 25.0,
                    "appliances": {
                        "dishwasher_skip": False,
                        "dishwasher_start_h": 21.0,
                    },
                },
            }],
            "selected_candidate_id": "complete_service",
            "selection_reason": "The daily service remains required.",
        },
    )

    assert resolution["status"] == "selected"
    assert resolution["selected_candidate_id"] == "complete_service"
    assert resolution["selected_executable_plan"]["appliances"]["dishwasher_skip"] is False
    assert resolution["semantic_replan_attempted"] is True
    assert "cancel a required daily service" in " ".join(
        resolution["attempts"][0]["runtime_contract_errors"]
    )


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
        agent_clarification_by_event_id={"e1": {
            "question": "Can the washer move earlier?",
            "answer": "Yes, when the measured tariff difference is meaningful.",
            "certainty": "conditional",
            "source": "direct_household_clarification",
        }},
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
            "washer": {
                "present": True,
                "earliest_h": 8.0,
                "latest_h": 22.0,
                "preferred_h": 19.0,
                "duration_h": 1.0,
                "power_kw": 1.5,
                "hidden_persona": "do not copy",
            },
        },
        setpoint_min_c=22.0,
        setpoint_max_c=28.0,
        tariff_snapshot={"hours": [{"hour": 18, "price": 2.4}]},
        ordinary_plan={"setpoint": 25.0, "appliance_actions": {"washer_start_h": 19.0}},
        hvac_rollout={
            "schema_version": "energybridge.observable_hvac_rollout.v1",
            "candidate_setpoints": [{"setpoint_c": 25.0, "hvac_energy_kwh": 1.2}],
        },
    )

    assert inputs["observable_profile"] == {
        "text": "Evening comfort matters.",
        "event_clarification": {
            "question": "Can the washer move earlier?",
            "answer": "Yes, when the measured tariff difference is meaningful.",
            "certainty": "conditional",
            "source": "direct_household_clarification",
        },
    }
    assert inputs["memory"] == {"relevant_episodes": [{"id": "old"}]}
    assert inputs["observable_state"]["facility_load_kw"] == 1.2
    assert inputs["observable_state"]["required_appliance_action_fields"] == [
        "washer_start_h",
        "washer_skip",
    ]
    assert "hidden_persona" not in inputs["observable_state"]["device_capabilities"]["washer"]
    assert inputs["observable_state"]["device_capabilities"]["washer"][
        "service_required_today"
    ] is True
    assert inputs["observable_state"]["hourly_tariff"]["hours"][0]["price"] == 2.4
    assert inputs["observable_state"]["ordinary_plan"] == {
        "setpoint": 25.0,
        "appliances": {"washer_start_h": 19.0},
    }
    assert inputs["observable_state"]["professional_hvac_rollout"]["candidate_setpoints"][0][
        "hvac_energy_kwh"
    ] == 1.2
    opportunity = inputs["observable_state"]["professional_flexible_load_opportunities"]
    assert opportunity["selection_performed"] is False
    assert opportunity["ranking_performed"] is False
    washer = opportunity["devices"]["washer"]
    washer_options = {
        dict(zip(washer["option_columns"], values))["start_hod"]: dict(
            zip(washer["option_columns"], values)
        )
        for values in washer["option_rows"]
    }
    assert washer_options[18.0]["event_overlap_h"] == 1.0
    assert washer_options[19.0]["event_overlap_h"] == 0.0


def test_hvac_rollout_snapshot_extends_through_visible_event(monkeypatch) -> None:
    captured: list[dict] = []
    monkeypatch.setattr(
        fr,
        "_build_decision_time_state",
        lambda *args, **kwargs: {"sim_h": kwargs["sim_h"]},
    )

    from experiments.benchmark.baselines import rule_milp

    def fake_rollout(state: dict) -> dict:
        captured.append(dict(state))
        return {
            "candidate_setpoints": [{
                "setpoint_c": 26.0,
                "hvac_energy_kwh": 1.5,
                "vpp_hvac_energy_kwh": 0.3,
                "predicted_final_temp_c": 25.7,
                "comfort_violation_c": 0.0,
            }]
        }

    monkeypatch.setattr(rule_milp, "_dynamic_setpoint_options", fake_rollout)
    result = fr._adaptive_v3_hvac_rollout_snapshot(
        SimpleNamespace(),
        sim_h=16.5,
        hod=16.5,
        temp=25.0,
        out_t=31.0,
        facility_w=2000.0,
        vpp_event={"id": "e1", "trigger_h": 18.0, "end_h": 19.0},
        appliance_config={},
        minimum_horizon_steps=6,
    )

    assert captured[0]["mpc_horizon_steps"] == 15
    assert result["horizon_h"] == 2.5
    assert result["candidate_setpoints"][0]["vpp_hvac_energy_kwh"] == 0.3
    assert result["selection_performed"] is False


def test_day_ahead_consent_selects_next_visible_same_day_event() -> None:
    events = [
        {"id": "past", "trigger_h": 6.0, "end_h": 7.0},
        {"id": "later", "trigger_h": 20.0, "end_h": 21.0},
        {"id": "next", "trigger_h": 18.0, "end_h": 19.0},
        {"id": "tomorrow", "trigger_h": 42.0, "end_h": 43.0},
    ]

    selected = fr._adaptive_v3_day_ahead_consent_event(8.0, events)

    assert selected == {"id": "next", "trigger_h": 18.0, "end_h": 19.0}
    assert fr._adaptive_v3_day_ahead_consent_event(22.0, events) is None


def test_observable_service_first_fallback_is_event_free_and_device_derived() -> None:
    ordinary = fr._adaptive_v3_observable_ordinary_plan(
        {
            "ac": {
                "present": True,
                "setpoint_preferred_min_c": 24.0,
                "setpoint_preferred_max_c": 26.0,
            },
            "washer": {
                "present": True,
                "preferred_h": 18.0,
                "earliest_h": 8.0,
                "latest_h": 22.0,
                "duration_h": 2.0,
            },
            "water_heater": {
                "present": True,
                "normal_start_h": 18.0,
                "normal_end_h": 20.0,
                "normal_temp_c": 60.0,
            },
        },
        current_setpoint=25.0,
        current_hod=0.25,
    )

    assert ordinary["setpoint"] == 24.0
    assert ordinary["appliance_actions"]["washer_start_h"] == 18.0
    assert ordinary["appliance_actions"]["washer_skip"] is False
    assert ordinary["appliance_actions"]["water_heater_preheat_start_h"] == 16.0
    assert ordinary["appliance_actions"]["water_heater_preheat_end_h"] == 20.0
    assert ordinary["objective_source"] == "observable_service_first_evening_routine_v1"
    assert ordinary["safe_fallback_profile"] == "evening_peak_service_first_v1"
    assert "vpp" not in ordinary["reason"].lower()


def test_observable_ordinary_plan_is_materialized_once_before_offer() -> None:
    loop = SimpleNamespace(no_vpp_daily_plan_by_day={})
    config = {
        "washer": {
            "present": True,
            "preferred_h": 19.0,
            "earliest_h": 8.0,
            "latest_h": 22.0,
            "duration_h": 2.0,
        }
    }

    first = fr._adaptive_v3_ensure_observable_ordinary_plan(
        loop,
        0,
        config,
        current_setpoint=26.0,
        current_hod=0.0,
    )
    second = fr._adaptive_v3_ensure_observable_ordinary_plan(
        loop,
        0,
        config,
        current_setpoint=24.0,
        current_hod=16.5,
    )

    assert first is second
    assert second["setpoint"] == 24.0
    assert second["appliance_actions"]["washer_start_h"] == 18.0
    assert second["objective_source"] == "observable_service_first_evening_routine_v1"


def test_historical_ordinary_fallback_remains_explicitly_selectable(monkeypatch) -> None:
    monkeypatch.setenv("ENERGYBRIDGE_SAFE_FALLBACK_PROFILE", "ordinary_v1")
    ordinary = fr._adaptive_v3_observable_ordinary_plan(
        {
            "washer": {
                "present": True,
                "preferred_h": 20.0,
                "earliest_h": 8.0,
                "latest_h": 22.0,
                "duration_h": 2.0,
            },
            "water_heater": {
                "present": True,
                "normal_start_h": 15.0,
                "normal_end_h": 19.0,
            },
        },
        current_setpoint=26.0,
        current_hod=0.0,
    )

    assert ordinary["appliance_actions"]["washer_start_h"] == 20.0
    assert ordinary["appliance_actions"]["water_heater_preheat_start_h"] == 15.0
    assert ordinary["objective_source"] == "observable_ordinary_routine_v3"
    assert ordinary["safe_fallback_profile"] == "ordinary_v1"


def test_service_first_fallback_ac_setpoint_is_configurable_within_comfort_band(
    monkeypatch,
) -> None:
    config = {
        "ac": {
            "present": True,
            "setpoint_preferred_min_c": 24.0,
            "setpoint_preferred_max_c": 26.0,
        }
    }
    monkeypatch.setenv("ENERGYBRIDGE_SAFE_FALLBACK_AC_SETPOINT_C", "25.5")
    configured = fr._adaptive_v3_observable_ordinary_plan(
        config,
        current_setpoint=26.0,
        current_hod=0.0,
    )
    monkeypatch.setenv("ENERGYBRIDGE_SAFE_FALLBACK_AC_SETPOINT_C", "21.0")
    clamped = fr._adaptive_v3_observable_ordinary_plan(
        config,
        current_setpoint=26.0,
        current_hod=0.0,
    )

    assert configured["setpoint"] == 25.5
    assert clamped["setpoint"] == 24.0


def test_service_first_fallback_concentrates_role_f_loads_in_evening_peak() -> None:
    appliances = {
        "washer": {
            "present": True,
            "earliest_h": 8.0,
            "latest_h": 22.0,
            "preferred_h": 20.0,
            "duration_h": 2.0,
        },
        "water_heater": {
            "present": True,
            "pre_heat_window_start_h": 15.0,
            "pre_heat_window_end_h": 19.0,
            "bath_required_h": 20.0,
        },
        "ev": {
            "present": True,
            "arrival_h": 18.5,
            "departure_h": 7.5,
        },
    }
    ordinary = fr._adaptive_v3_observable_ordinary_plan(
        appliances,
        current_setpoint=26.0,
        current_hod=0.0,
    )
    actions = ordinary["appliance_actions"]

    assert actions["washer_start_h"] == 18.0
    assert actions["water_heater_preheat_start_h"] == 14.0
    assert actions["water_heater_preheat_end_h"] == 20.0
    assert actions["ev_charge_start_h"] == 18.5
    conflicts = fr._vpp_appliance_conflicts(
        actions,
        appliances,
        {"trigger_h": 18.0, "end_h": 19.0},
    )
    assert {item.split(":", 1)[0] for item in conflicts} == {
        "ev",
        "washer",
        "water_heater",
    }


def test_consent_explanation_prefers_the_exact_offered_plan_snapshot() -> None:
    offered = {"natural_language": "The offered washer schedule saves a supported amount."}
    later = {"natural_language": "A later controller draft must not replace the offer."}

    result = fr._adaptive_v3_household_explanation_from_gate(
        {"proposed_plan": {"strategy_explanation": offered}},
        {"strategy_explanation": later},
    )

    assert result == offered
    assert result is not offered


def test_daily_llm_usage_separates_transport_and_validation_failures() -> None:
    loop = SimpleNamespace()
    fr._init_daily_llm_usage(loop, 1)

    fr._record_daily_llm_usage(
        loop,
        1,
        0.25,
        {
            "latency_seconds": 1.2,
            "token_usage": {"prompt_tokens": 100, "completion_tokens": 20},
            "retries": 2,
            "validation_failures": 1,
            "provider_failures": 1,
            "empty_response_failures": 0,
            "length_truncation_failures": 1,
            "response_format_requested": "json_object",
            "response_format_fallback": False,
        },
    )
    fr._record_daily_llm_usage(
        loop,
        1,
        0.5,
        {
            "used": False,
            "exhausted": True,
            "latency_seconds": 3.0,
            "token_usage": {"prompt_tokens": 200, "completion_tokens": 40},
            "retries": 4,
            "validation_failures": 5,
            "provider_failures": 0,
            "length_truncation_failures": 5,
            "response_format_requested": "json_object",
            "response_format_fallback": False,
        },
    )

    row = loop.daily_llm_usage[0]
    assert row["llm_calls"] == 1
    assert row["llm_exhausted_calls"] == 1
    assert row["protocol_retries"] == 6
    assert row["validation_failures"] == 6
    assert row["provider_failures"] == 1
    assert row["length_truncation_failures"] == 6
    assert row["structured_output_calls"] == 2
    assert row["structured_output_fallbacks"] == 0


def test_gate_protocol_metrics_keep_superseded_proposal_retries() -> None:
    metrics = fr._aggregate_gate_protocol_metrics([
        {
            "retries": 2,
            "validation_failures": 2,
            "response_format_requested": "json_object",
            "response_format_fallback": False,
        },
        {
            "retries": 0,
            "provider_failures": 0,
            "response_format_requested": "json_object",
            "response_format_fallback": False,
        },
        {
            "used": False,
            "exhausted_calls": 1,
            "provider_failures": 3,
            "response_format_requested": "json_object",
            "response_format_fallback": True,
        },
    ])

    assert metrics == {
        "exhausted_calls": 1,
        "retries": 2,
        "validation_failures": 2,
        "provider_failures": 3,
        "empty_response_failures": 0,
        "length_truncation_failures": 0,
        "structured_output_calls": 3,
        "structured_output_fallbacks": 1,
    }
