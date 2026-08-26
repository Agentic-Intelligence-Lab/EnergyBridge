from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from energybridge.harness.memory import build_event_context, initialize_memory
from experiments.benchmark import family_runner as fr


def _fixture() -> tuple[dict, dict, dict, dict, dict]:
    persona = {
        "id": "v2_household",
        "display_name": "Evening commuter household",
        "description": "Returns near the event and wants specific, reversible changes.",
        "tags": {"control": "confirm_required", "price": "price_sensitive"},
        "preferences": {
            "scoring_weights": {"comfort": 0.45, "energy": 0.30, "vpp": 0.25},
            "vpp_override_prob": 0.25,
        },
    }
    event = {"id": "vpp1", "day": 1, "trigger_h": 18.0, "end_h": 19.0}
    appliances = {
        "ac": {
            "present": True,
            "setpoint_preferred_min_c": 24.0,
            "setpoint_preferred_max_c": 26.0,
            "temp_tolerance_c": 1.0,
        },
        "washer": {"present": True, "shiftable": True, "dr_adjustable": True},
    }
    ordinary = {
        "setpoint": 25.5,
        "appliance_actions": {"washer_start_h": 18.0, "washer_skip": False},
        "reason": "normal routine",
    }
    offered = {
        "setpoint": 26.0,
        "appliance_actions": {"washer_start_h": 20.0, "washer_skip": False},
        "reason": "Move the washer after the event and keep the agreed comfort limit.",
        "strategy_explanation": {
            "natural_language": "I will keep 26 C and run the washer at 20:00; you can opt out.",
            "why_request": "A one-hour event is expected at 18:00.",
            "recommended_actions": [{"device": "washer", "action": "start at 20:00"}],
            "protected_constraints": ["comfort", "washer completion"],
            "user_control": ["opt out"],
        },
    }
    return persona, event, appliances, ordinary, offered


def _response(*, decision: str, probability: float, delta: float, baseline: float = 0.5) -> dict:
    return {
        "decision": decision,
        "baseline_acceptance_probability": baseline,
        "adjustments": [
            {
                "dimension": "routine fit",
                "delta": delta,
                "evidence": "E1",
                "reason": "the chore still finishes",
            }
        ],
        "final_acceptance_probability": probability,
        "confidence": 0.81,
        "evidence": [
            {
                "id": "E1",
                "source": "plan",
                "fact": "washer starts at 20:00",
                "effect": "supports_acceptance" if delta > 0 else "supports_rejection",
            }
        ],
        "counterfactual": {
            "changes": [],
            "decision_if_changed": "uncertain",
            "acceptance_probability_if_changed": None,
            "reason": "no change requested",
        },
        "reason": "This fits my evening well." if decision == "accept" else "I would rather keep the routine.",
        "user_feedback": "Keep using the concrete schedule.",
    }


def test_v2_gate_preserves_roleplay_probability_and_direct_decision(monkeypatch) -> None:
    persona, event, appliances, ordinary, offered = _fixture()
    monkeypatch.setenv("ENERGYBRIDGE_HARNESS_PROFILE", "adaptive_v2")
    monkeypatch.setenv("ENERGYBRIDGE_VPP_ACCEPTANCE_GATE", "adaptive_roleplay_v2")
    monkeypatch.setenv("ENERGYBRIDGE_ROLEPLAY_ACCEPTANCE_GATE_USE_LLM", "1")

    monkeypatch.setattr(
        fr,
        "_call_roleplay_acceptance_gate_llm",
        lambda system, user, **kwargs: (_response(decision="accept", probability=0.513, delta=0.013), {"used": True, "model": "model-a"}),
    )
    accepted = fr._evaluate_vpp_plan_acceptance_gate(
        method="agent",
        persona_config=persona,
        appliance_config=appliances,
        event=event,
        proposed_plan=offered,
        default_plan=ordinary,
        user_preference_text="Please keep the change specific and reversible.",
    )
    assert accepted["version"] == "vpp_plan_acceptance_gate_adaptive_roleplay_v2"
    assert accepted["acceptance_probability"] == pytest.approx(0.513)
    assert accepted["accepted"] is True
    assert accepted["stable_draw"] is None
    assert accepted["prompt_gate_metrics"]["model"] == "model-a"

    monkeypatch.setattr(
        fr,
        "_call_roleplay_acceptance_gate_llm",
        lambda system, user, **kwargs: (_response(decision="reject", probability=0.171, delta=-0.329), {"used": True, "model": "model-b"}),
    )
    rejected = fr._evaluate_vpp_plan_acceptance_gate(
        method="agent",
        persona_config=persona,
        appliance_config=appliances,
        event=event,
        proposed_plan=offered,
        default_plan=ordinary,
        user_preference_text="Please keep the change specific and reversible.",
    )
    assert rejected["acceptance_probability"] == pytest.approx(0.171)
    assert rejected["accepted"] is False
    assert "floor" not in " ".join(rejected["factors"]).lower()
    assert "cap" not in " ".join(rejected["factors"]).lower()


def test_v2_roleplay_prompt_is_method_blind(monkeypatch) -> None:
    persona, event, appliances, ordinary, offered = _fixture()
    monkeypatch.setenv("ENERGYBRIDGE_HARNESS_PROFILE", "adaptive_v2")
    monkeypatch.setenv("ENERGYBRIDGE_VPP_ACCEPTANCE_GATE", "adaptive_roleplay_v2")
    monkeypatch.setenv("ENERGYBRIDGE_ROLEPLAY_ACCEPTANCE_GATE_USE_LLM", "1")
    prompts: list[tuple[str, str]] = []

    def fake_call(system: str, user: str, **kwargs):
        prompts.append((system, user))
        return _response(decision="accept", probability=0.54, delta=0.04), {"used": True, "model": "same-model"}

    monkeypatch.setattr(fr, "_call_roleplay_acceptance_gate_llm", fake_call)
    for method in ("agent", "mpc_dynamic"):
        fr._evaluate_vpp_plan_acceptance_gate(
            method=method,
            persona_config=persona,
            appliance_config=appliances,
            event=event,
            proposed_plan=offered,
            default_plan=ordinary,
            user_preference_text="A specific reversible plan is easier to accept.",
        )
    assert prompts[0] == prompts[1]
    assert "agent" not in prompts[0][1].lower()
    assert "mpc" not in prompts[0][1].lower()


def test_v2_hidden_override_cannot_change_prompt_or_public_gate(monkeypatch) -> None:
    persona, event, appliances, ordinary, offered = _fixture()
    monkeypatch.setenv("ENERGYBRIDGE_HARNESS_PROFILE", "adaptive_v2")
    monkeypatch.setenv("ENERGYBRIDGE_VPP_ACCEPTANCE_GATE", "adaptive_roleplay_v2")
    monkeypatch.setenv("ENERGYBRIDGE_ROLEPLAY_ACCEPTANCE_GATE_USE_LLM", "1")
    prompts: list[tuple[str, str]] = []

    def fake_call(system: str, user: str, **kwargs):
        prompts.append((system, user))
        return _response(decision="accept", probability=0.54, delta=0.04), {
            "used": True,
            "model": "same-model",
        }

    monkeypatch.setattr(fr, "_call_roleplay_acceptance_gate_llm", fake_call)
    left = json.loads(json.dumps(persona))
    right = json.loads(json.dumps(persona))
    left["preferences"]["vpp_override_prob"] = 0.01
    right["preferences"]["vpp_override_prob"] = 0.99

    gates = [
        fr._evaluate_vpp_plan_acceptance_gate(
            method="agent",
            persona_config=current,
            appliance_config=appliances,
            event=event,
            proposed_plan=offered,
            default_plan=ordinary,
            user_preference_text="A specific reversible plan is easier to accept.",
        )
        for current in (left, right)
    ]

    assert prompts[0] == prompts[1]
    assert gates[0] == gates[1]
    assert gates[0]["base_override_probability"] is None


def test_v2_model_owns_valid_plan_and_explanation(monkeypatch) -> None:
    monkeypatch.setenv("ENERGYBRIDGE_HARNESS_PROFILE", "adaptive_v2")
    assert fr._agent_model_owns_valid_plan("agent") is True
    assert fr._agent_model_owns_valid_plan("agent", force_mpc_primary=True) is False
    assert fr._agent_model_owns_valid_plan("mpc_dynamic") is False

    raw = {
        "natural_language": "My model-specific explanation remains exactly visible.",
        "why_request": "A short event is approaching.",
        "alternatives": [{"name": "keep routine"}],
    }
    normalized = fr._adaptive_v2_strategy_explanation(raw)
    assert normalized["natural_language"] == raw["natural_language"]
    assert normalized["source"] == "llm_adaptive_v2_uncompleted"
    assert fr._adaptive_v2_strategy_explanation(None) == {}


def test_v2_gate_snapshot_preserves_native_explanation_but_legacy_shape_does_not(
    monkeypatch,
) -> None:
    plan = {
        "setpoint": 25.0,
        "reason": "I shifted one task and kept hot water ready.",
        "appliance_actions": {"washer_start_h": 20.0},
        "strategy_explanation": {
            "natural_language": "I shifted the washer until after the event and protected the shower.",
        },
    }
    monkeypatch.setenv("ENERGYBRIDGE_HARNESS_PROFILE", "adaptive_v2")
    v2_snapshot = fr._plan_snapshot_for_gate(plan)
    assert v2_snapshot["strategy_explanation"] == plan["strategy_explanation"]

    monkeypatch.setenv("ENERGYBRIDGE_HARNESS_PROFILE", "legacy_v1")
    legacy_snapshot = fr._plan_snapshot_for_gate(plan)
    assert "strategy_explanation" not in legacy_snapshot


def test_v2_roleplay_error_fails_closed_without_legacy_estimator(monkeypatch) -> None:
    persona, event, appliances, ordinary, offered = _fixture()
    monkeypatch.setenv("ENERGYBRIDGE_HARNESS_PROFILE", "adaptive_v2")
    monkeypatch.setenv("ENERGYBRIDGE_VPP_ACCEPTANCE_GATE", "adaptive_roleplay_v2")
    monkeypatch.setenv("ENERGYBRIDGE_ROLEPLAY_ACCEPTANCE_GATE_USE_LLM", "1")
    monkeypatch.delenv("ENERGYBRIDGE_DISABLE_ACCEPTANCE_FALLBACK", raising=False)

    def unavailable(*args, **kwargs):
        raise RuntimeError(
            "roleplay endpoint unavailable; provider=SecretProvider model=SecretModel "
            "evaluator=SecretJudge api_key=sk-EXCEPTIONSECRET123 "
            "endpoint=https://private.example/v1"
        )

    monkeypatch.setattr(fr, "_call_roleplay_acceptance_gate_llm", unavailable)
    gate = fr._evaluate_vpp_plan_acceptance_gate(
        method="agent",
        persona_config=persona,
        appliance_config=appliances,
        event=event,
        proposed_plan=offered,
        default_plan=ordinary,
        user_preference_text="Keep the change reversible.",
    )

    # Without an explicit household consent prior, the prompt uses a transparent
    # neutral starting point rather than relabelling other persona weights.
    assert gate["baseline_acceptance_probability"] == pytest.approx(0.5)
    assert gate["acceptance_probability"] == pytest.approx(0.5)
    assert gate["accepted"] is False
    assert gate["roleplay_source"] == "roleplay_model_unavailable_fail_closed"
    assert gate["fallback_source"] == "roleplay_model_unavailable_fail_closed"
    assert "endpoint unavailable" in gate["fallback_error"]
    serialized = json.dumps(gate)
    for hidden in (
        "SecretProvider",
        "SecretModel",
        "SecretJudge",
        "sk-EXCEPTIONSECRET123",
        "private.example",
    ):
        assert hidden not in serialized
    assert all("floor" not in factor.lower() and "cap" not in factor.lower() for factor in gate["factors"])


def test_v2_hidden_persona_sentinel_cannot_enter_controller_prompt(monkeypatch) -> None:
    monkeypatch.setenv("ENERGYBRIDGE_HARNESS_PROFILE", "adaptive_v2")
    questionnaire = {
        "answers": [{
            "id": "vpp_priority",
            "answer": "Balance comfort and savings.",
            "selected_option_ids": ["balanced_tradeoff"],
        }],
        "inferred_profile": {"strategy_bias": "HIDDEN_SENTINEL_PROFILE"},
        "preference_rules": ["DO_NOT_EXPOSE_RULE"],
    }
    memory = initialize_memory(questionnaire, persona_id="opaque-household")
    loop = SimpleNamespace(
        agent_preference_memory=memory,
        agent_memory_context_by_event_id={},
    )
    persona = {
        "tags": {"control": "HIDDEN_SENTINEL_TAG"},
        "schedule": {"private": "SECRET_SCHEDULE"},
        "preferences": {"note": "DO_NOT_EXPOSE_PREF"},
        "calendar": {
            "source": "attached_calendar",
            "days": [{
                "day": 1,
                "weekday": "Monday",
                "events": [{"title": "Dinner", "start_h": 18.0, "end_h": 19.0, "location": "home"}],
                "constraints": {"home_arrival_h": 17.5},
            }],
        },
    }
    fallback_interview = fr._fallback_agent_onboarding_questionnaire(
        persona,
        controller_observable_only=True,
    )
    assert fallback_interview["controller_projection"] == "answers_and_selected_option_ids_only"
    assert fallback_interview["inferred_profile"] == {}
    assert fallback_interview["preference_rules"] == []
    event = {"id": "vpp1", "day": 1, "trigger_h": 18.0, "end_h": 19.0}
    calendar = fr._agent_observable_calendar_context(persona, event, occupied=True, occupancy=2.0)
    prompt = fr._agent_preference_memory_prompt_text(
        loop,
        event=event,
        calendar=calendar,
        home_state={"indoor_temp_c": 25.0, "occupied": True},
        user_input="Please protect dinner.",
    )
    serialized = json.dumps(memory, ensure_ascii=False, sort_keys=True) + prompt + json.dumps(calendar)
    assert "HIDDEN_SENTINEL" not in serialized
    assert "SECRET_SCHEDULE" not in serialized
    assert "DO_NOT_EXPOSE" not in serialized
    assert "Dinner" in prompt
    fr._assert_adaptive_v2_agent_prompt_observable("safe system", prompt, persona)
    with pytest.raises(RuntimeError, match="observable boundary violation"):
        fr._assert_adaptive_v2_agent_prompt_observable(
            "safe system",
            prompt + " HIDDEN_SENTINEL_TAG",
            persona,
        )


def test_v2_distinct_valid_model_plans_keep_distinct_lifecycle_fingerprints() -> None:
    plan_a = {
        "setpoint": 25.0,
        "next_check_hour": 19.0,
        "appliances": {"washer_start_h": 20.0, "washer_skip": False},
        "reason": "Protect comfort and move the washer after the event.",
    }
    plan_b = {
        "setpoint": 26.5,
        "next_check_hour": 18.5,
        "appliances": {"washer_start_h": 21.0, "washer_skip": False},
        "reason": "Use more thermal flexibility and defer the washer longer.",
    }
    lifecycle_a = fr._adaptive_v2_record_plan_stage(
        fr._adaptive_v2_new_plan_lifecycle(plan_a, model="fake-model-a"),
        "validated_plan",
        plan_a,
        validator="hard_safety_service_and_physical_validation",
        status="passed",
    )
    lifecycle_b = fr._adaptive_v2_record_plan_stage(
        fr._adaptive_v2_new_plan_lifecycle(plan_b, model="fake-model-b"),
        "validated_plan",
        plan_b,
        validator="hard_safety_service_and_physical_validation",
        status="passed",
    )
    assert lifecycle_a["stages"]["raw_model_plan"]["fingerprint"] != lifecycle_b["stages"]["raw_model_plan"]["fingerprint"]
    assert lifecycle_a["stages"]["validated_plan"]["fingerprint"] != lifecycle_b["stages"]["validated_plan"]["fingerprint"]
    assert lifecycle_a["validators"][0]["patches"] == []
    assert lifecycle_b["validators"][0]["patches"] == []


def test_v2_memory_attributes_rejected_proposal_to_executed_fallback() -> None:
    memory = initialize_memory(
        {"answers": [{
            "id": "vpp_priority",
            "answer": "Keep comfort first.",
            "selected_option_ids": ["comfort_routine_first"],
        }]},
        persona_id="household-1",
    )
    proposal = {
        "setpoint": 27.0,
        "appliance_actions": {"washer_start_h": 20.0, "washer_skip": False},
        "reason": "Offer grid support.",
    }
    fallback = {
        "setpoint": 25.0,
        "appliance_actions": {"washer_start_h": 18.0, "washer_skip": False},
        "reason": "Restore the ordinary routine.",
        "fallback_after_vpp_rejection": True,
    }
    lifecycle = fr._adaptive_v2_new_plan_lifecycle(proposal, model="fake-model")
    lifecycle = fr._adaptive_v2_record_plan_stage(lifecycle, "validated_plan", proposal, status="passed")
    lifecycle = fr._adaptive_v2_record_plan_stage(lifecycle, "proposed_plan", proposal, status="offered")
    lifecycle = fr._adaptive_v2_record_plan_stage(lifecycle, "consented_plan", proposal, status="rejected")
    lifecycle = fr._adaptive_v2_record_plan_stage(lifecycle, "executed_plan", fallback, status="fallback_after_rejection")
    context = build_event_context(
        {"id": "vpp1", "day": 1, "trigger_h": 18.0, "end_h": 19.0},
        calendar={"occupied": True, "constraints": ["dinner"]},
        home_state={"indoor_temp_c": 25.0},
        proposed_plan=proposal,
        executed_plan=fallback,
        plan_lifecycle=lifecycle,
    )
    loop = SimpleNamespace(
        agent_preference_memory=memory,
        agent_memory_context_by_event_id={"vpp1": context},
        agent_execution_context_by_event_id={"vpp1": context},
        agent_plan_lifecycle_by_event_id={"vpp1": lifecycle},
        persist_agent_preference_memory=False,
        agent_memory_path=None,
        agent_memory_md_path=None,
    )
    result = {
        "id": "vpp1",
        "day": 1,
        "trigger_h": 18.0,
        "end_h": 19.0,
        "setpoint": 25.0,
        "reason": "Restore the ordinary routine.",
        "vpp_trigger_actions": {"washer_start_h": 18.0, "washer_skip": False},
        "score": 4,
        "comfort_score": 5,
        "energy_score": 3,
        "vpp_score": 1,
        "comment": "The fallback kept my routine.",
        "vpp_acceptance_gate": {
            "accepted": False,
            "proposed_plan": proposal,
            "energybridge_feedback": "Do not move dinner-time chores without consent.",
        },
    }

    fr._update_agent_preference_memory(loop, result, persona_config={"hidden": "unused"})

    event = loop.agent_preference_memory["events"][-1]
    assert event["proposed_plan"]["setpoint"] == 27.0
    assert event["executed_plan"]["setpoint"] == 25.0
    assert event["executed_plan"]["appliance_actions"]["washer_start_h"] == 18.0
    stages = event["plan_lifecycle"]["stages"]
    assert stages["proposed_plan"]["fingerprint"] != stages["executed_plan"]["fingerprint"]
    assert stages["consented_plan"]["status"] == "rejected"
