from __future__ import annotations

import json
import stat
from copy import deepcopy
from types import SimpleNamespace

import pytest

from energybridge.simulation.appliance_sim import ApplianceSuite
from experiments.benchmark import family_runner as fr


def _questionnaire(*, benefit: str = "bill_savings_first") -> dict:
    return {
        "version": "agent_onboarding_questionnaire_observable_v2",
        "source": "roleplay_questionnaire_llm",
        "question_count": 4,
        "answers": [
            {
                "id": "benefit_priority",
                "question": "Which outcome matters most?",
                "answer": "Please show me a concrete benefit.",
                "selected_option_ids": [benefit],
            },
            {
                "id": "change_permission",
                "question": "Which changes can happen automatically?",
                "answer": "Ask before a material change.",
                "selected_option_ids": ["confirm_before_changes"],
            },
            {
                "id": "protected_routines",
                "question": "What should stay protected?",
                "answer": "Protect dinner and shower time.",
                "selected_option_ids": ["caregiving_sleep_work"],
            },
            {
                "id": "thermostat_flexibility",
                "question": "What thermostat change is reasonable?",
                "answer": "A small short change is fine.",
                "selected_option_ids": ["small_1c_short"],
            },
        ],
    }


def test_attitude_transition_uses_only_live_observable_judgement() -> None:
    audit = {
        "live_acceptance_judgement": True,
        "acceptance_probability": 0.66,
        "normalized_authored_rating": 1.0,
        "signed_rating_minus_acceptance": 0.34,
        "phase_interpretation": (
            "higher_post_event_rating_has_new_positive_outcome_evidence"
        ),
        "post_event_evidence": {
            "realised_plan_basis": "accepted_offered_plan",
            "positive_outcome_evidence": ["observed_comfort_preserved"],
        },
        "score_was_posthoc_remapped": False,
        "method": "HIDDEN_METHOD",
        "target_acceptance": 0.8,
    }

    transition = fr._adaptive_v3_attitude_transition(audit)

    assert transition == {
        "schema_version": "energybridge.observable_attitude_transition.v1",
        "pre_event_willingness": 0.66,
        "post_event_normalized_rating": 1.0,
        "signed_post_minus_pre": 0.34,
        "phase_interpretation": (
            "higher_post_event_rating_has_new_positive_outcome_evidence"
        ),
        "observed_outcome_evidence": {
            "realised_plan_basis": "accepted_offered_plan",
            "positive_outcome_evidence": ["observed_comfort_preserved"],
        },
        "posthoc_score_remap": False,
    }
    assert fr._adaptive_v3_attitude_transition(
        {**audit, "live_acceptance_judgement": False}
    ) == {}


def _init_v3(
    monkeypatch,
    tmp_path,
    *,
    persona_id: str | None = "house-a",
    questionnaire: dict | None = None,
    store: str | None = None,
    load: bool = False,
    persist_review: bool = False,
) -> fr._FamilyLoop:
    monkeypatch.setenv("ENERGYBRIDGE_HARNESS_PROFILE", "adaptive_v2")
    if persist_review:
        monkeypatch.setenv("ENERGYBRIDGE_PERSIST_AGENT_MEMORY", "1")
    else:
        monkeypatch.delenv("ENERGYBRIDGE_PERSIST_AGENT_MEMORY", raising=False)
    if store is None:
        monkeypatch.delenv("ENERGYBRIDGE_AGENT_MEMORY_STORE", raising=False)
    else:
        monkeypatch.setenv("ENERGYBRIDGE_AGENT_MEMORY_STORE", store)
    if load:
        monkeypatch.setenv("ENERGYBRIDGE_LOAD_AGENT_MEMORY", "1")
    else:
        monkeypatch.delenv("ENERGYBRIDGE_LOAD_AGENT_MEMORY", raising=False)
    visible = deepcopy(questionnaire or _questionnaire())
    monkeypatch.setattr(fr, "_run_agent_onboarding_questionnaire", lambda _persona: visible)
    loop = fr._FamilyLoop()
    fr._init_agent_preference_memory(
        loop,
        tmp_path,
        method="agent",
        persona_config={
            "id": persona_id,
            "hidden_persona": "HIDDEN_SENTINEL_DO_NOT_EXPOSE",
            "method_name": "SECRET_METHOD_ID",
            "model_name": "SECRET_MODEL_ID",
        },
        appliance_config={
            "washer": {
                "present": True,
                "shiftable": True,
                "duration_h": 1.0,
                "hidden_persona": "HIDDEN_SENTINEL_DEVICE",
            },
        },
    )
    return loop


def test_v3_init_and_prompt_capsules_are_observable_evidence_only(monkeypatch, tmp_path) -> None:
    loop = _init_v3(monkeypatch, tmp_path)

    assert loop.agent_preference_memory["version"] == "energybridge_evidence_memory_v3"
    assert loop.agent_household_model["schema_version"] == "energybridge.observable_household_model.v3"
    assert loop.agent_household_model["evidence_index"]
    assert isinstance(loop.agent_household_model["unknowns"], list)
    assert loop.agent_memory_v3_audit["memory_session_state"] == "cold"
    assert loop.persist_agent_preference_memory is False
    assert loop.agent_memory_path is None

    prompt = fr._agent_preference_memory_prompt_text(
        loop,
        event={"id": "event-1", "event_type": "demand_response", "trigger_h": 18.0, "end_h": 19.0},
        calendar={"occupied": True, "constraints": ["dinner"]},
        home_state={"indoor_temp_c": 25.5, "outdoor_temp_c": 33.0, "occupied": True},
        user_input="Please keep dinner unchanged.",
    )
    profile = loop.agent_profile_capsule_by_event_id["event-1"]
    memory = loop.agent_memory_capsule_by_event_id["event-1"]
    serialized = json.dumps({"prompt": prompt, "profile": profile, "memory": memory})

    assert profile["evidence_refs"]
    assert memory["memory_version"] == "energybridge_evidence_memory_v3"
    assert "HIDDEN_SENTINEL" not in serialized
    assert "SECRET_METHOD_ID" not in serialized
    assert "SECRET_MODEL_ID" not in serialized
    assert not list(tmp_path.iterdir())


def test_v3_scored_event_keeps_proposal_execution_and_outcome_attribution_separate(
    monkeypatch, tmp_path
) -> None:
    loop = _init_v3(monkeypatch, tmp_path)
    event_id = "event-stage-test"
    fr._agent_preference_memory_prompt_text(
        loop,
        event={"id": event_id, "event_type": "demand_response", "trigger_h": 18.0, "end_h": 19.0},
        calendar={"occupied": True},
        home_state={"indoor_temp_c": 25.0, "occupied": True},
        user_input="I may decline a warm setpoint.",
    )
    raw = {"setpoint": 28.0, "appliances": {"washer_start_h": 18.0, "washer_skip": False}}
    validated = {"setpoint": 27.0, "appliances": {"washer_start_h": 20.0, "washer_skip": False}}
    executed = {"setpoint": 25.0, "appliance_actions": {"washer_start_h": 14.0, "washer_skip": False}}
    lifecycle = fr._adaptive_v2_new_plan_lifecycle(raw)
    lifecycle = fr._adaptive_v2_record_plan_stage(lifecycle, "validated_plan", validated, status="passed")
    lifecycle = fr._adaptive_v2_record_plan_stage(lifecycle, "proposed_plan", validated, status="offered")
    lifecycle = fr._adaptive_v2_record_plan_stage(lifecycle, "consented_plan", validated, status="rejected")
    lifecycle = fr._adaptive_v2_record_plan_stage(lifecycle, "executed_plan", executed, status="fallback_after_rejection")
    loop.agent_plan_lifecycle_by_event_id[event_id] = lifecycle
    result = {
        "id": event_id,
        "day": 1,
        "trigger_h": 18.0,
        "end_h": 19.0,
        "setpoint": 25.0,
        "reason": "Normal household plan after decline.",
        "vpp_trigger_actions": {"washer_start_h": 14.0, "washer_skip": False},
        "vpp_acceptance_gate": {"accepted": False, "proposed_plan": validated},
        "score": 2,
        "comfort_score": 4,
        "energy_score": 2,
        "vpp_score": 1,
        "target_achieved": False,
        "actual_kwh": 1.2,
        "comment": "I declined the warmer proposal.",
    }

    fr._update_agent_preference_memory(loop, result, persona_config={"hidden_persona": "unused"})

    episode = loop.agent_preference_memory["episodes"][-1]
    stages = episode["stages"]
    assert set(stages) == {"raw_proposal", "validated", "consented", "executed", "outcome"}
    assert stages["raw_proposal"]["plan"]["setpoint"] == 28.0
    assert stages["validated"]["plan"]["setpoint"] == 27.0
    assert stages["consented"]["decision"] is False
    assert stages["executed"]["plan"]["setpoint"] == 25.0
    assert stages["outcome"]["observations"]["score"] == 2
    attribution = episode["causal_attribution"]
    assert attribution["decision_exposure_fingerprint"] != attribution["executed_exposure_fingerprint"]
    assert attribution["outcome_attribution"] == "observational_executed_plan"
    audit = result["adaptive_decision_audit"]
    assert audit["memory_update"]["episode_stage_separation"]["episode_stages"] == [
        "consented",
        "executed",
        "outcome",
        "raw_proposal",
        "validated",
    ]


def test_v3_event_memory_attributes_outcome_to_the_full_execution_sequence(
    monkeypatch, tmp_path
) -> None:
    loop = _init_v3(monkeypatch, tmp_path)
    event_id = "event-multi-exposure"
    fr._agent_preference_memory_prompt_text(
        loop,
        event={"id": event_id, "event_type": "demand_response", "trigger_h": 18.0, "end_h": 19.0},
        calendar={"occupied": True},
        home_state={"indoor_temp_c": 25.0, "occupied": True},
        user_input="Keep the evening routine protected.",
    )
    early = {"setpoint": 24.5, "appliance_actions": {"washer_start_h": 20.0}}
    event_plan = {"setpoint": 26.0, "appliance_actions": {"washer_start_h": 20.0}}
    lifecycle = fr._adaptive_v2_new_plan_lifecycle(early)
    lifecycle = fr._adaptive_v2_record_plan_stage(
        lifecycle, "executed_plan", early, status="executed"
    )
    lifecycle = fr._adaptive_v3_append_execution_exposure(
        loop, event_id, 8.0, lifecycle, early
    )
    # Simulate the event-start lifecycle replacing the visible decision stages;
    # the loop-level actuator ledger must still retain the earlier exposure.
    lifecycle = fr._adaptive_v2_new_plan_lifecycle(event_plan)
    lifecycle = fr._adaptive_v2_record_plan_stage(
        lifecycle, "proposed_plan", event_plan, status="offered"
    )
    lifecycle = fr._adaptive_v2_record_plan_stage(
        lifecycle, "consented_plan", event_plan, status="accepted"
    )
    lifecycle = fr._adaptive_v2_record_plan_stage(
        lifecycle, "executed_plan", event_plan, status="executed"
    )
    lifecycle = fr._adaptive_v3_append_execution_exposure(
        loop, event_id, 18.0, lifecycle, event_plan
    )
    loop.agent_plan_lifecycle_by_event_id[event_id] = lifecycle
    result = {
        "id": event_id,
        "day": 1,
        "trigger_h": 18.0,
        "end_h": 19.0,
        "setpoint": 26.0,
        "vpp_trigger_actions": {"washer_start_h": 20.0},
        "vpp_acceptance_gate": {"accepted": True, "proposed_plan": event_plan},
        "score": 4,
        "comfort_score": 4,
        "energy_score": 4,
        "vpp_score": 4,
        "actual_kwh": 1.0,
        "comment": "Both dispatches were applied.",
    }

    fr._update_agent_preference_memory(loop, result, persona_config={})

    exposures = result["adaptive_decision_audit"]["plan_lifecycle"]["execution_exposures"]
    assert [item["simulation_hour"] for item in exposures] == [8.0, 18.0]
    assert [item["plan"]["setpoint"] for item in exposures] == [24.5, 26.0]
    episode = loop.agent_preference_memory["episodes"][-1]
    executed = episode["stages"]["executed"]["plan"]
    assert len(executed["execution_exposures"]) == 2
    assert episode["context"]["observations"]["execution_exposure_count"] == 2
    assert episode["causal_attribution"]["outcome_attribution"] == (
        "observational_executed_plan"
    )


def test_v3_execution_binds_professional_forecast_and_calibrates_outcome(
    monkeypatch, tmp_path
) -> None:
    loop = _init_v3(monkeypatch, tmp_path)
    event_id = "event-calibrated-forecast"
    fr._agent_preference_memory_prompt_text(
        loop,
        event={"id": event_id, "event_type": "demand_response", "trigger_h": 18.0, "end_h": 19.0},
        calendar={"occupied": True},
        home_state={"indoor_temp_c": 25.0, "occupied": True},
        user_input="Keep the washer outside the event.",
    )
    plan = {
        "setpoint": 26.0,
        "appliance_actions": {"washer_start_h": 20.0, "washer_skip": False},
    }
    candidate_id = "model-plan"
    planning_evidence = {
        "schema_version": "energybridge.candidate_impact.v3",
        "candidate_id": candidate_id,
        "plan_fingerprint": "professional-forecast-1",
        "device_impacts": {
            "washer": {
                "task_completed": True,
                "vpp_overlap_h": 0.0,
                "vpp_overlap_energy_kwh": 0.0,
            }
        },
        "hvac_impact": {"comfort_violation_c": 0.0},
    }
    lifecycle = fr._adaptive_v2_new_plan_lifecycle(plan)
    lifecycle["portfolio_planning"] = {
        "selected_candidate_id": candidate_id,
        "final_portfolio_audit": {
            "professional_impact_evidence": {"candidate_impacts": [planning_evidence]}
        },
    }
    lifecycle = fr._adaptive_v2_record_plan_stage(
        lifecycle, "consented_plan", plan, status="accepted"
    )
    lifecycle = fr._adaptive_v2_record_plan_stage(
        lifecycle, "executed_plan", plan, status="executed"
    )
    lifecycle = fr._adaptive_v3_append_execution_exposure(
        loop, event_id, 17.0, lifecycle, plan
    )
    loop.agent_plan_lifecycle_by_event_id[event_id] = lifecycle
    result = {
        "id": event_id,
        "day": 1,
        "trigger_h": 18.0,
        "end_h": 19.0,
        "setpoint": 26.0,
        "vpp_trigger_actions": plan["appliance_actions"],
        "vpp_acceptance_gate": {"accepted": True, "proposed_plan": plan},
        "score": 4,
        "comfort_score": 4,
        "energy_score": 4,
        "vpp_score": 5,
        "actual_kwh": 1.0,
        "comfort_violation_minutes": 0.0,
        "appliance_summary": {
            "washer": {"present": True, "completed": True, "ran_during_vpp": False}
        },
        "comment": "The schedule worked.",
    }

    fr._update_agent_preference_memory(loop, result, persona_config={})

    audit = result["adaptive_decision_audit"]["planning_calibration"]
    assert audit["status"] == "observational_execution_calibrated"
    assert audit["record"]["observation_count"] == 3
    assert all(item["agreement"] for item in audit["record"]["observations"])
    episode = loop.agent_preference_memory["episodes"][-1]
    stored = episode["stages"]["outcome"]["observations"]["planning_calibration"]
    assert stored["plan_fingerprint"] == "professional-forecast-1"
    assert stored["policy_update_performed"] is False
    assert stored["ranking_performed"] is False


def test_v3_explicit_store_warm_load_reconciles_current_onboarding(monkeypatch, tmp_path) -> None:
    store_file = tmp_path / "shared-memory.json"
    cold_dir = tmp_path / "cold-run"
    cold_dir.mkdir(mode=0o700)
    cold_dir.chmod(0o700)
    first = _init_v3(
        monkeypatch,
        cold_dir,
        persona_id="same-household",
        questionnaire=_questionnaire(benefit="bill_savings_first"),
        store=str(store_file),
    )
    fr._write_agent_preference_memory(first)

    assert store_file.exists()
    assert stat.S_IMODE(store_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(first.agent_memory_path.stat().st_mode) == 0o600
    raw_store = store_file.read_text(encoding="utf-8")
    assert "same-household" not in raw_store
    assert "HIDDEN_SENTINEL" not in raw_store
    assert "SECRET_METHOD_ID" not in raw_store

    warm_dir = tmp_path / "warm-run"
    warm_dir.mkdir(mode=0o700)
    warm_dir.chmod(0o700)
    second = _init_v3(
        monkeypatch,
        warm_dir,
        persona_id="same-household",
        questionnaire=_questionnaire(benefit="grid_support_first"),
        store=str(store_file),
        load=True,
    )

    assert second.agent_memory_v3_audit["memory_session_state"] == "warm"
    assert second.agent_memory_v3_audit["load_status"] == "loaded_and_reconciled"
    answers = second.agent_preference_memory["onboarding"]["answers"]
    assert answers[0]["selected_option_ids"] == ["grid_support_first"]
    benefit_evidence = second.agent_preference_memory["stable_beliefs"]["benefit_priority"]["evidence"]
    assert {item["value"] for item in benefit_evidence} == {"grid_support_first"}
    assert str(store_file) not in json.dumps(fr._adaptive_v3_component_audit(second))


def test_v3_store_refuses_cross_household_load_and_default_is_cold(monkeypatch, tmp_path) -> None:
    store_file = tmp_path / "one-household.json"
    first_dir = tmp_path / "first"
    first_dir.mkdir()
    first = _init_v3(monkeypatch, first_dir, persona_id="house-one", store=str(store_file))
    fr._write_agent_preference_memory(first)
    original = store_file.read_bytes()

    other_dir = tmp_path / "other"
    other_dir.mkdir()
    other = _init_v3(
        monkeypatch,
        other_dir,
        persona_id="house-two",
        store=str(store_file),
        load=True,
    )
    assert other.agent_memory_v3_audit["memory_session_state"] == "cold"
    assert other.agent_memory_v3_audit["load_status"].startswith("cold_load_failed:")
    assert other.agent_memory_v3_save_allowed is False
    fr._write_agent_preference_memory(other)
    assert store_file.read_bytes() == original
    assert str(store_file) not in json.dumps(fr._adaptive_v3_component_audit(other))

    default_dir = tmp_path / "default"
    default_dir.mkdir()
    default = _init_v3(monkeypatch, default_dir, persona_id="house-three")
    assert default.agent_memory_v3_audit == {
        "memory_session_state": "cold",
        "load_status": "not_requested",
        "persistence_enabled": False,
        "save_status": "not_allowed",
    }
    assert default.agent_memory_v3_store_path is None
    assert default.agent_memory_path is None
    assert not list(default_dir.iterdir())


def test_v3_anonymous_households_with_same_answers_cannot_share_store(monkeypatch, tmp_path) -> None:
    store_file = tmp_path / "anonymous-memory.json"
    first_dir = tmp_path / "anonymous-first"
    second_dir = tmp_path / "anonymous-second"
    first_dir.mkdir()
    second_dir.mkdir()

    first = _init_v3(
        monkeypatch,
        first_dir,
        persona_id=None,
        questionnaire=_questionnaire(),
        store=str(store_file),
    )
    fr._write_agent_preference_memory(first)
    second = _init_v3(
        monkeypatch,
        second_dir,
        persona_id=None,
        questionnaire=_questionnaire(),
        store=str(store_file),
        load=True,
    )
    fr._write_agent_preference_memory(second)

    assert first.agent_preference_memory["owner"]["household_id"] != second.agent_preference_memory["owner"]["household_id"]
    assert first.agent_memory_v3_audit["load_status"] == "cold_identity_unavailable"
    assert second.agent_memory_v3_audit["load_status"] == "cold_identity_unavailable"
    assert first.agent_memory_v3_audit["persistence_enabled"] is False
    assert second.agent_memory_v3_audit["persistence_enabled"] is False
    assert first.agent_memory_v3_store_path is None
    assert second.agent_memory_v3_store_path is None
    assert not store_file.exists()


def test_v3_run_local_review_flag_does_not_enable_cross_run_store(monkeypatch, tmp_path) -> None:
    output_dir = tmp_path / "shared-run-output"
    output_dir.mkdir(mode=0o775)
    output_dir.chmod(0o775)
    loop = _init_v3(monkeypatch, output_dir, persist_review=True)

    assert loop.persist_agent_preference_memory is True
    assert loop.agent_memory_v3_store_path is None
    assert loop.agent_memory_v3_save_allowed is False
    assert loop.agent_memory_v3_audit["persistence_enabled"] is False

    fr._write_agent_preference_memory(loop)

    private_dir = output_dir / ".adaptive_harness_private"
    assert loop.agent_memory_path == private_dir / "agent_memory_v3_review.json"
    assert loop.agent_memory_md_path == private_dir / "agent_memory_v3_review.md"
    assert stat.S_IMODE(private_dir.stat().st_mode) == 0o700
    assert loop.agent_memory_path.exists()
    assert loop.agent_memory_md_path.exists()
    assert stat.S_IMODE(loop.agent_memory_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(loop.agent_memory_md_path.stat().st_mode) == 0o600
    assert "store" not in json.loads(loop.agent_memory_path.read_text(encoding="utf-8"))


def test_v3_run_local_review_writer_is_atomic_and_owner_only(tmp_path) -> None:
    private = tmp_path / "private-run"
    private.mkdir(mode=0o700)
    private.chmod(0o700)
    target = private / "agent_memory_v3_review.json"
    target.write_text("old", encoding="utf-8")
    target.chmod(0o666)

    written = fr._adaptive_v3_atomic_private_artifact_write(target, '{"safe": true}')

    assert written == target
    assert target.read_text(encoding="utf-8") == '{"safe": true}'
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert not list(private.glob(f".{target.name}.*.tmp"))


def test_v3_run_local_review_writer_refuses_leaf_symlink(tmp_path) -> None:
    private = tmp_path / "private-run"
    private.mkdir(mode=0o700)
    private.chmod(0o700)
    outside = tmp_path / "outside.json"
    outside.write_text("do-not-overwrite", encoding="utf-8")
    leaf = private / "agent_memory_v3_review.json"
    leaf.symlink_to(outside)

    with pytest.raises(ValueError, match="symlink"):
        fr._adaptive_v3_atomic_private_artifact_write(leaf, "private-review")

    assert leaf.is_symlink()
    assert outside.read_text(encoding="utf-8") == "do-not-overwrite"
    assert not list(private.glob(f".{leaf.name}.*.tmp"))


def test_v3_run_local_review_writer_refuses_ancestor_symlink(tmp_path) -> None:
    actual = tmp_path / "actual-private-run"
    actual.mkdir(mode=0o700)
    actual.chmod(0o700)
    redirect = tmp_path / "redirect"
    redirect.symlink_to(actual, target_is_directory=True)
    target = redirect / "agent_memory_v3_review.md"

    with pytest.raises(ValueError, match="symlink"):
        fr._adaptive_v3_atomic_private_artifact_write(target, "private-review")

    assert not (actual / target.name).exists()


def test_v3_store_creation_refuses_existing_ancestor_symlink(tmp_path) -> None:
    actual = tmp_path / "actual"
    actual.mkdir()
    redirect = tmp_path / "redirect"
    redirect.symlink_to(actual, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        fr._adaptive_v3_memory_store_file(
            str(redirect / "not-created" / "memory.json"),
            "household-opaque",
        )

    assert not (actual / "not-created").exists()


def test_v3_store_refuses_public_directory_and_creates_private_directory(tmp_path) -> None:
    public = tmp_path / "public-store"
    public.mkdir(mode=0o777)
    public.chmod(0o777)

    with pytest.raises(PermissionError, match="group/other access"):
        fr._adaptive_v3_memory_store_file(str(public), "household-opaque")

    private = tmp_path / "private-store"
    target = fr._adaptive_v3_memory_store_file(str(private), "household-opaque")
    assert target.parent == private
    assert stat.S_IMODE(private.stat().st_mode) == 0o700


def test_warm_profile_history_uses_only_attributed_executed_outcomes() -> None:
    memory = {
        "episodes": [
            {
                "episode_id": "consent-only",
                "causal_attribution": {
                    "outcome_attribution": "unattributed_no_execution_evidence"
                },
                "stages": {
                    "outcome": {
                        "recorded_at": "2026-01-01T00:00:00Z",
                        "observations": {"score": 1, "comment": "I rejected the offer."},
                    }
                },
            },
            {
                "episode_id": "actually-executed",
                "causal_attribution": {
                    "outcome_attribution": "observational_executed_plan"
                },
                "context": {"event": {"event_type": "demand_response"}},
                "stages": {
                    "outcome": {
                        "recorded_at": "2026-01-02T00:00:00Z",
                        "observations": {"score": 4, "comment": "The executed plan worked."},
                    }
                },
            },
        ]
    }

    history = fr._adaptive_v3_profile_feedback_history(memory)

    assert history == [
        {
            "score": 4,
            "comment": "The executed plan worked.",
            "event_id": "actually-executed",
            "event_context": {"event": {"event_type": "demand_response"}},
            "observed_at": "2026-01-02T00:00:00Z",
        }
    ]


def test_adaptive_water_heater_false_disables_and_never_uses_attached_schedule() -> None:
    config = {
        "water_heater": {
            "present": True,
            "dr_adjustable": True,
            "pre_heat_window_start_h": 15.0,
            "pre_heat_window_end_h": 18.0,
        }
    }
    suite = ApplianceSuite(config, sim_days=1, explicit_only=True)
    assert suite.set_ewh_preheat_schedule(0, start_h=14.0, end_h=18.0, temp_c=65.0)
    assert suite._water_heater._days[0]["preheat_requested"] is True

    report = fr._adaptive_v3_apply_appliance_actions(
        suite,
        {
            "water_heater_preheat": False,
            "water_heater_preheat_start_h": 12.0,
            "water_heater_preheat_end_h": 18.0,
            "water_heater_preheat_temp_c": 70.0,
        },
        sim_h=8.0,
    )

    state = suite._water_heater._days[0]
    assert state["preheat_requested"] is False
    assert state["preheat_start_h"] is None
    assert state["preheat_end_h"] is None
    assert state["preheat_temp_c"] is None
    assert report["applied_actions"] == {"water_heater_preheat": False}
    assert report["rejections"] == []
    assert any(
        patch["path"] == "/water_heater_preheat_start_h"
        for patch in report["patches"]
    )


def test_adaptive_shiftable_schedule_cannot_be_moved_into_the_past() -> None:
    config = {
        "washer": {
            "present": True,
            "earliest_h": 8.0,
            "latest_h": 22.0,
            "preferred_h": 19.0,
            "duration_h": 2.0,
            "shiftable": True,
            "dr_adjustable": True,
        }
    }
    suite = ApplianceSuite(config, sim_days=1, explicit_only=True)
    assert suite.shift_appliance("washer", 0, 19.0)

    errors = fr._adaptive_v3_shiftable_runtime_errors(
        {"washer_start_h": 15.0, "washer_skip": False},
        suite,
        sim_h=16.5,
    )
    report = fr._adaptive_v3_apply_appliance_actions(
        suite,
        {"washer_start_h": 15.0, "washer_skip": False},
        sim_h=16.5,
    )

    assert any("before the current decision clock" in item for item in errors)
    assert report["applied_actions"] == {}
    assert report["rejections"] == [{"service": "washer", "reason": "runtime_past"}]
    assert suite._shiftable["washer"]._days[0].scheduled_abs_h == 19.0


def test_adaptive_reapply_of_same_started_schedule_is_idempotent() -> None:
    config = {
        "washer": {
            "present": True,
            "earliest_h": 8.0,
            "latest_h": 22.0,
            "preferred_h": 15.0,
            "duration_h": 2.0,
            "shiftable": True,
            "dr_adjustable": True,
        }
    }
    suite = ApplianceSuite(config, sim_days=1, explicit_only=True)
    assert suite.shift_appliance("washer", 0, 15.0)
    suite.step(15.0, 1 / 6)

    errors = fr._adaptive_v3_shiftable_runtime_errors(
        {"washer_start_h": 15.0, "washer_skip": False},
        suite,
        sim_h=16.5,
    )
    report = fr._adaptive_v3_apply_appliance_actions(
        suite,
        {"washer_start_h": 15.0, "washer_skip": False},
        sim_h=16.5,
    )

    assert errors == []
    assert report["rejections"] == []
    assert report["applied_actions"] == {
        "washer_start_h": 15.0,
        "washer_skip": False,
    }


def test_adaptive_actual_execution_records_bounded_actions_and_fingerprint() -> None:
    config = {
        "water_heater": {
            "present": True,
            "dr_adjustable": True,
            "pre_heat_window_start_h": 15.0,
            "pre_heat_window_end_h": 18.0,
        }
    }
    suite = ApplianceSuite(config, sim_days=1, explicit_only=True)
    requested = {
        "setpoint": 27.0,
        "reason": "Prepare hot water before the event.",
        "appliance_actions": {
            "water_heater_preheat": True,
            "water_heater_preheat_start_h": 10.0,
            "water_heater_preheat_end_h": 18.0,
            "water_heater_preheat_temp_c": 65.0,
        },
    }
    report = fr._adaptive_v3_apply_appliance_actions(
        suite, requested["appliance_actions"], sim_h=8.0
    )
    lifecycle = fr._adaptive_v2_new_plan_lifecycle(requested)
    lifecycle = fr._adaptive_v2_record_plan_stage(
        lifecycle, "consented_plan", requested, status="accepted"
    )

    lifecycle, actual = fr._adaptive_v3_record_actuator_execution(
        lifecycle,
        requested,
        effective_setpoint=27.0,
        application_report=report,
        hvac_actuator_available=True,
    )

    executed = lifecycle["stages"]["executed_plan"]
    assert actual["appliance_actions"]["water_heater_preheat_start_h"] == 14.0
    assert executed["plan"] == fr._adaptive_v2_plan_snapshot(actual)
    assert executed["fingerprint"] == fr._adaptive_v2_plan_fingerprint(actual)
    assert executed["fingerprint"] != lifecycle["stages"]["consented_plan"]["fingerprint"]
    assert executed["status"] == "executed_with_runtime_normalization"
    assert any(
        patch["path"] == "/appliance_actions/water_heater_preheat_start_h"
        for patch in executed["patches"]
    )
    assert executed["application_report"]["patches"]


def test_rejected_offer_resolution_is_separate_from_actuator_normalization() -> None:
    proposed = {
        "setpoint": 28.0,
        "appliance_actions": {"washer_start_h": 20.0},
    }
    fallback = {
        "setpoint": 25.0,
        "appliance_actions": {"washer_start_h": 14.0},
        "fallback_after_vpp_rejection": True,
    }
    lifecycle = fr._adaptive_v2_new_plan_lifecycle(proposed)
    lifecycle = fr._adaptive_v2_record_plan_stage(
        lifecycle, "proposed_plan", proposed, status="offered"
    )
    lifecycle = fr._adaptive_v2_record_plan_stage(
        lifecycle, "consented_plan", proposed, status="rejected"
    )
    lifecycle = fr._adaptive_v2_record_plan_stage(
        lifecycle,
        "resolved_execution_plan",
        fallback,
        validator="household_consent_resolution",
        status="fallback_after_rejection",
    )
    report = {
        "version": "energybridge.actuator_application.v3",
        "requested_actions": {"washer_start_h": 14.0},
        "applied_actions": {"washer_start_h": 14.0},
        "patches": [],
        "rejections": [],
        "status": "applied",
    }

    lifecycle, actual = fr._adaptive_v3_record_actuator_execution(
        lifecycle,
        fallback,
        effective_setpoint=25.0,
        application_report=report,
        hvac_actuator_available=True,
        status="fallback_after_rejection",
    )

    resolved = lifecycle["stages"]["resolved_execution_plan"]
    executed = lifecycle["stages"]["executed_plan"]
    assert resolved["plan"]["setpoint"] == 25.0
    assert any(
        patch["path"] == "/setpoint" and patch["old_value"] == 28.0
        for patch in resolved["patches"]
    )
    assert executed["from_stage"] == "resolved_execution_plan"
    assert executed["resolution_status"] == "fallback_after_rejection"
    assert executed["status"] == "fallback_after_rejection"
    assert actual["setpoint"] == 25.0
    assert executed["application_report"]["hvac_application"] == {
        "service": "hvac_cooling_setpoint",
        "requested_setpoint": 25.0,
        "status": "actuator_available",
        "applied_setpoint": 25.0,
    }


def test_adaptive_actual_execution_does_not_invent_setpoint_without_hvac_actuator() -> None:
    requested = {
        "setpoint": 27.0,
        "reason": "Temporarily reduce peak demand.",
        "appliance_actions": {},
    }
    lifecycle = fr._adaptive_v2_new_plan_lifecycle(requested)
    lifecycle = fr._adaptive_v2_record_plan_stage(
        lifecycle, "consented_plan", requested, status="accepted"
    )
    empty_appliance_report = {
        "version": "energybridge.actuator_application.v3",
        "requested_actions": {},
        "applied_actions": {},
        "patches": [],
        "rejections": [],
        "status": "no_appliance_actions_requested",
    }

    lifecycle, actual = fr._adaptive_v3_record_actuator_execution(
        lifecycle,
        requested,
        effective_setpoint=27.0,
        application_report=empty_appliance_report,
        hvac_actuator_available=False,
    )

    executed = lifecycle["stages"]["executed_plan"]
    assert actual == {}
    assert executed["plan"] == {}
    assert executed["fingerprint"] is None
    assert executed["status"] == "execution_rejected_actuator_unavailable"
    assert executed["application_report"]["hvac_application"]["status"] == "actuator_unavailable"
    assert {
        "service": "hvac_cooling_setpoint",
        "reason": "cooling_setpoint_actuator_unavailable",
    } in executed["application_report"]["rejections"]


def test_v3_event_memory_does_not_recreate_unobserved_hvac_execution(
    monkeypatch, tmp_path
) -> None:
    loop = _init_v3(monkeypatch, tmp_path)
    event_id = "event-no-hvac-actuator"
    fr._agent_preference_memory_prompt_text(
        loop,
        event={"id": event_id, "event_type": "demand_response", "trigger_h": 18.0, "end_h": 19.0},
        calendar={"occupied": True},
        home_state={"indoor_temp_c": 25.0, "occupied": True},
        user_input="A small temporary thermostat change is acceptable.",
    )
    requested = {"setpoint": 27.0, "appliance_actions": {}, "reason": "Peak event request."}
    lifecycle = fr._adaptive_v2_new_plan_lifecycle(requested)
    lifecycle = fr._adaptive_v2_record_plan_stage(
        lifecycle, "validated_plan", requested, status="passed"
    )
    lifecycle = fr._adaptive_v2_record_plan_stage(
        lifecycle, "proposed_plan", requested, status="offered"
    )
    lifecycle = fr._adaptive_v2_record_plan_stage(
        lifecycle, "consented_plan", requested, status="accepted"
    )
    lifecycle, actual = fr._adaptive_v3_record_actuator_execution(
        lifecycle,
        requested,
        effective_setpoint=27.0,
        application_report={
            "version": "energybridge.actuator_application.v3",
            "requested_actions": {},
            "applied_actions": {},
            "patches": [],
            "rejections": [],
            "status": "no_appliance_actions_requested",
        },
        hvac_actuator_available=False,
    )
    assert actual == {}
    loop.agent_plan_lifecycle_by_event_id[event_id] = lifecycle
    result = {
        "id": event_id,
        "day": 1,
        "trigger_h": 18.0,
        "end_h": 19.0,
        # This is a policy/event summary, not proof that EnergyPlus accepted it.
        "setpoint": 27.0,
        "vpp_trigger_actions": {},
        "vpp_acceptance_gate": {"accepted": True, "proposed_plan": requested},
        "score": 4,
        "comfort_score": 4,
        "energy_score": 4,
        "vpp_score": 4,
        "actual_kwh": 1.0,
        "comment": "The event completed.",
    }

    fr._update_agent_preference_memory(loop, result, persona_config={})

    recorded = loop.agent_plan_lifecycle_by_event_id[event_id]
    executed = recorded["stages"]["executed_plan"]
    assert executed["plan"] == {}
    assert executed["fingerprint"] is None
    validator = recorded["validators"][-1]
    assert validator["status"] == "execution_unobserved_event_summary_ignored"
    assert validator["ignored_event_summary_fields"] == ["/setpoint"]
    episode = loop.agent_preference_memory["episodes"][-1]
    assert "executed" not in episode["stages"]
    assert episode["causal_attribution"]["execution_observed"] is False
    assert episode["causal_attribution"]["outcome_attribution"] == (
        "unattributed_no_execution_evidence"
    )
    assert result["adaptive_decision_audit"]["executed_plan_fingerprint"] is None


def test_adaptive_runtime_contract_rejects_noncanonical_types_hours_and_next_check() -> None:
    config = {
        "washer": {"present": True},
        "water_heater": {"present": True},
        "ev": {"present": True},
    }
    assert fr._missing_explicit_appliance_actions(
        {"water_heater_preheat": False},
        {"water_heater": {"present": True}},
        adaptive_contract=True,
    ) == []
    bad_actions = {
        "washer_start_h": 48,
        "washer_skip": 0,
        "water_heater_preheat": True,
        "water_heater_preheat_start_h": 12.0,
        "water_heater_preheat_end_h": 30.0,
        "water_heater_preheat_temp_c": "hot",
        "ev_mode": "garbage",
        "ev_charge_start_h": -1.0,
        "ev_charge_end_h": 48.0,
    }
    errors = fr._adaptive_v3_appliance_action_contract_errors(bad_actions, config)
    joined = " | ".join(errors)
    assert "washer_skip must be an exact boolean" in joined
    assert "washer_start_h must use canonical local hours" in joined
    assert "water_heater_preheat_end_h must be in [0, 24]" in joined
    assert "water_heater_preheat_temp_c must be a finite number" in joined
    assert "ev_mode must be one of" in joined
    assert "ev_charge_start_h must use canonical local hours" in joined
    assert "ev_charge_end_h must use canonical local hours" in joined

    plan_errors = fr._adaptive_v3_plan_control_errors(
        {
            "setpoint": 27.0,
            "next_check_hour": "abc",
            "appliances": bad_actions,
        },
        sim_h=8.0,
        total_sim_hours=24.0,
        setpoint_min_c=22.0,
        setpoint_max_c=28.0,
    )
    assert "next_check_hour must be a finite number or null" in plan_errors
    assert fr._adaptive_v3_plan_control_errors(
        {"setpoint": 27.0, "next_check_hour": 8.25, "appliances": {}},
        sim_h=8.0,
        total_sim_hours=24.0,
        setpoint_min_c=22.0,
        setpoint_max_c=28.0,
    ) == []
    assert "next_check_hour must be in the future" in (
        fr._adaptive_v3_plan_control_errors(
            {"setpoint": 27.0, "next_check_hour": 8.249, "appliances": {}},
            sim_h=8.0,
            total_sim_hours=24.0,
            setpoint_min_c=22.0,
            setpoint_max_c=28.0,
        )
    )


def test_adaptive_ev_mode_contract_matches_the_appliance_simulator() -> None:
    config = {"ev": {"present": True}}

    assert fr._adaptive_v3_appliance_action_contract_errors(
        {"ev_mode": "delay"}, config
    ) == []
    assert "ev_mode must be one of: normal, smart, delay" in (
        fr._adaptive_v3_appliance_action_contract_errors(
            {"ev_mode": "off"}, config
        )
    )


def test_adaptive_contract_replans_required_service_skip_instead_of_executing_it() -> None:
    required = {"dishwasher": {"present": True}}
    errors = fr._adaptive_v3_appliance_action_contract_errors(
        {"dishwasher_skip": True},
        required,
    )
    assert any("cancel a required daily service" in item for item in errors)

    explicitly_not_required = {
        "dishwasher": {"present": True, "service_required_today": False},
    }
    assert fr._adaptive_v3_appliance_action_contract_errors(
        {"dishwasher_skip": True},
        explicitly_not_required,
    ) == []


def test_adaptive_v3_numeric_contract_rejects_json_booleans(monkeypatch) -> None:
    monkeypatch.setenv("ENERGYBRIDGE_HARNESS_PROFILE", "adaptive_v2")
    config = {
        "washer": {"present": True},
        "dishwasher": {"present": False},
        "dryer": {"present": False},
        "water_heater": {"present": True},
        "ev": {"present": True},
    }
    actions = {
        "washer_skip": False,
        "washer_start_h": True,
        "water_heater_preheat": True,
        "water_heater_preheat_start_h": True,
        "water_heater_preheat_end_h": 2.0,
        "water_heater_preheat_temp_c": 60.0,
        "ev_mode": "smart",
        "ev_charge_start_h": True,
        "ev_charge_end_h": 6.0,
    }

    joined = " | ".join(
        fr._adaptive_v3_appliance_action_contract_errors(actions, config)
    )
    assert "washer_start_h must be numeric" in joined
    assert "water_heater_preheat_start_h must be a finite number" in joined
    assert "ev_charge_start_h must be numeric" in joined

    assert "setpoint must be finite" in fr._adaptive_v3_plan_control_errors(
        {"setpoint": True, "next_check_hour": 12.0, "appliances": {}},
        sim_h=8.0,
        total_sim_hours=24.0,
        setpoint_min_c=22.0,
        setpoint_max_c=28.0,
    )
    assert "next_check_hour must be a finite number or null" in (
        fr._adaptive_v3_plan_control_errors(
            {"setpoint": 25.0, "next_check_hour": True, "appliances": {}},
            sim_h=8.0,
            total_sim_hours=24.0,
            setpoint_min_c=22.0,
            setpoint_max_c=28.0,
        )
    )


def test_adaptive_valid_ev_plan_is_not_silently_repaired() -> None:
    config = {
        "ev": {
            "present": True,
            "charger_kw": 7.4,
            "efficiency": 0.92,
            "daily_drive_kwh": 8.0,
            "arrival_h": 18.0,
            "departure_h": 7.5,
        }
    }
    event = {"id": "event", "trigger_h": 18.0, "end_h": 19.0}
    actions = {
        "ev_mode": "smart",
        "ev_charge_start_h": 19.0,
        "ev_charge_end_h": 23.0,
    }

    repaired, changed = fr._agent_repair_ev_service_actions(
        actions,
        appliance_config=config,
        event=event,
        hod=16.0,
    )

    assert changed is False
    assert repaired == actions
    assert fr._adaptive_v3_appliance_action_contract_errors(repaired, config) == []


def test_adaptive_public_setpoint_envelope_accepts_valid_model_judgement() -> None:
    assert fr._adaptive_v3_plan_control_errors(
        {"setpoint": 27.0, "next_check_hour": 10.0, "appliances": {}},
        sim_h=8.0,
        total_sim_hours=24.0,
        setpoint_min_c=22.0,
        setpoint_max_c=28.0,
    ) == []


@pytest.mark.parametrize("profile", ["legacy_v1", "paper_v1"])
def test_legacy_and_paper_memory_initialization_remains_v1(monkeypatch, tmp_path, profile) -> None:
    monkeypatch.setenv("ENERGYBRIDGE_HARNESS_PROFILE", profile)
    monkeypatch.delenv("ENERGYBRIDGE_PERSIST_AGENT_MEMORY", raising=False)
    monkeypatch.setenv("ENERGYBRIDGE_AGENT_MEMORY_STORE", str(tmp_path / "ignored-v3-store.json"))
    monkeypatch.setattr(fr, "_run_agent_onboarding_questionnaire", lambda _persona: _questionnaire())
    loop = fr._FamilyLoop()

    fr._init_agent_preference_memory(
        loop,
        tmp_path,
        method="agent",
        persona_config={"id": "legacy-persona"},
    )

    assert loop.agent_preference_memory["version"] == "agent_preference_memory_v1"
    assert loop.agent_preference_memory["method"] == "agent"
    assert loop.agent_preference_memory["persona_id"] == "legacy-persona"
    assert loop.agent_household_model == {}
    assert not (tmp_path / "ignored-v3-store.json").exists()


def test_planning_prompt_is_identity_invariant_and_live_device_state_is_retained() -> None:
    class FakeSuite:
        _last_powers = {"ev": 2.5, "washer": 0.0}

        def status_lines(self, sim_h):
            return ["ev: SOC=42% target=80% [at_home]", "washer: done_today"]

        def all_results(self):
            return {
                "ev": [{"day": 0, "present": True, "soc_end": 0.42, "target_reached": False}],
                "washer": [{"day": 0, "present": True, "completed": True}],
            }

    loop = SimpleNamespace(
        appliance_suite=FakeSuite(),
        current_occupied=True,
        current_occupancy_count=1.0,
        current_occupancy_source="shared_calendar",
        agent_profile_capsule_by_event_id={"e": {"text": "Visible facts only."}},
        agent_memory_capsule_by_event_id={"e": {"relevant_episodes": []}},
        agent_preference_memory={},
    )
    common = dict(
        loop=loop,
        event_id="e",
        sim_h=8.0,
        hod=8.0,
        temp=25.0,
        out_t=30.0,
        facility_w=1500.0,
        observable_calendar={"occupied": True},
        memory_event={"id": "e"},
        vpp_event=None,
        user_input="",
        appliance_config={"ev": {"present": True, "target_soc": 0.8}},
        setpoint_min_c=22.0,
        setpoint_max_c=28.0,
    )
    left = fr._adaptive_v3_observable_planning_inputs(**common)
    right = deepcopy(left)
    left["observable_profile"].update({"method_name": "method-a", "model_name": "model-a"})
    right["observable_profile"].update({"method_name": "method-b", "model_name": "model-b"})
    left_prompts = fr._adaptive_v3_planning_prompts(left, allow_skill_request=True)
    right_prompts = fr._adaptive_v3_planning_prompts(right, allow_skill_request=True)

    assert left_prompts == right_prompts
    prompt_text = "\n".join(left_prompts)
    assert "SOC=42%" in prompt_text
    assert "washer: done_today" in prompt_text
    assert "forecast_control" in prompt_text
    assert "constraint_scheduler" in prompt_text
    assert "mpc_dynamic" not in prompt_text
    assert "rule_milp" not in prompt_text
    assert fr._requested_agent_skill_names(
        {"skill_calls": ["forecast_control", "constraint_scheduler", "thermal_dynamics"]}
    ) == ["mpc_dynamic", "rule_milp", "dynamic_hvac"]


def test_ev_feasibility_inputs_expose_every_parameter_used_by_hard_validation() -> None:
    loop = SimpleNamespace(
        appliance_suite=None,
        current_occupied=True,
        current_occupancy_count=1.0,
        current_occupancy_source="shared_calendar",
        agent_profile_capsule_by_event_id={"e": {"text": "The EV is needed tomorrow."}},
        agent_memory_capsule_by_event_id={"e": {}},
        agent_preference_memory={},
    )
    ev = {
        "present": True,
        "arrival_h": 18.0,
        "departure_h": 7.5,
        "target_soc": 0.8,
        "capacity_kwh": 72.0,
        "charger_kw": 1.0,
        "efficiency": 0.81,
        "daily_drive_kwh": 12.5,
    }
    inputs = fr._adaptive_v3_observable_planning_inputs(
        loop,
        event_id="e",
        sim_h=16.0,
        hod=16.0,
        temp=25.0,
        out_t=30.0,
        facility_w=1000.0,
        observable_calendar={"occupied": True},
        memory_event={"id": "e", "trigger_h": 18.0, "end_h": 19.0},
        vpp_event={"id": "e", "trigger_h": 18.0, "end_h": 19.0},
        user_input="Please keep the EV ready.",
        appliance_config={"ev": ev},
        setpoint_min_c=22.0,
        setpoint_max_c=28.0,
    )

    visible = inputs["observable_state"]["device_capabilities"]["ev"]
    for key in ("capacity_kwh", "charger_kw", "efficiency", "daily_drive_kwh"):
        assert visible[key] == ev[key]


def test_planning_inputs_expose_machine_checked_event_timing_constraints() -> None:
    loop = SimpleNamespace(
        appliance_suite=None,
        current_occupied=True,
        current_occupancy_count=1.0,
        current_occupancy_source="shared_calendar",
        agent_profile_capsule_by_event_id={"e": {"text": "Keep chores outside the event."}},
        agent_memory_capsule_by_event_id={"e": {}},
        agent_preference_memory={},
    )
    inputs = fr._adaptive_v3_observable_planning_inputs(
        loop,
        event_id="e",
        sim_h=40.5,
        hod=16.5,
        temp=25.0,
        out_t=30.0,
        facility_w=1000.0,
        observable_calendar={"occupied": True},
        memory_event={"id": "e", "trigger_h": 42.0, "end_h": 43.0},
        vpp_event={"id": "e", "trigger_h": 42.0, "end_h": 43.0},
        user_input="Avoid the event window.",
        appliance_config={
            "washer": {
                "present": True,
                "shiftable": True,
                "dr_adjustable": True,
                "duration_h": 2.0,
            },
            "water_heater": {
                "present": True,
                "dr_adjustable": True,
            },
            "ev": {
                "present": True,
                "dr_adjustable": True,
                "arrival_h": 18.5,
                "departure_h": 7.5,
                "charger_kw": 7.4,
                "efficiency": 0.92,
                "daily_drive_kwh": 8.0,
            },
        },
        setpoint_min_c=22.0,
        setpoint_max_c=28.0,
    )

    by_id = {item["constraint_id"]: item for item in inputs["explicit_constraints"]}
    epochs = inputs["observable_state"]["professional_decision_epochs"]
    assert epochs["schema_version"] == "energybridge.decision_epochs.v1"
    assert epochs["selection_performed"] is False
    assert epochs["ranking_performed"] is False
    assert any(row[0] == 42.0 for row in epochs["epoch_rows"])
    assert any(row[0] == 43.0 for row in epochs["epoch_rows"])
    assert by_id["future_next_check_when_present"] == {
        "constraint_id": "future_next_check_when_present",
        "kind": "range",
        "path": "/next_check_hour",
        "min": 40.75,
        "nullable": True,
        "severity": "hard",
        "evidence_paths": ["/observable_state/time/simulation_hour"],
    }
    assert by_id["washer_outside_vpp_window"] == {
        "constraint_id": "washer_outside_vpp_window",
        "kind": "disjoint_interval_duration",
        "path": "/appliances/washer_start_h",
        "duration_h": 2.0,
        "forbidden_window": [18.0, 19.0],
        "severity": "hard",
        "evidence_paths": [
            "/observable_state/device_capabilities/washer/duration_h",
            "/event/trigger_h",
            "/event/end_h",
        ],
    }
    assert by_id["water_heater_outside_vpp_window_when_preheating"] == {
        "constraint_id": "water_heater_outside_vpp_window_when_preheating",
        "kind": "disjoint_interval_if_true",
        "enabled_path": "/appliances/water_heater_preheat",
        "start_path": "/appliances/water_heater_preheat_start_h",
        "end_path": "/appliances/water_heater_preheat_end_h",
        "forbidden_window": [18.0, 19.0],
        "severity": "hard",
        "evidence_paths": [
            "/event/trigger_h",
            "/event/end_h",
            "/observable_state/device_capabilities/water_heater",
        ],
    }
    assert by_id["ev_charge_starts_after_observed_arrival"]["min"] == 18.5
    assert by_id["ev_charge_window_outside_vpp"] == {
        "constraint_id": "ev_charge_window_outside_vpp",
        "kind": "cyclic_disjoint_interval",
        "start_path": "/appliances/ev_charge_start_h",
        "end_path": "/appliances/ev_charge_end_h",
        "forbidden_window": [18.0, 19.0],
        "severity": "hard",
        "evidence_paths": [
            "/event/trigger_h",
            "/event/end_h",
            "/observable_state/device_capabilities/ev",
        ],
    }
    assert by_id["ev_charge_window_service_duration"]["min_duration_h"] > 1.0


def test_completed_shiftable_service_is_not_required_again_but_cannot_be_rescheduled() -> None:
    class CompletedWasherSuite:
        _last_powers = {"washer": 0.0, "dishwasher": 0.0}

        @staticmethod
        def status_lines(sim_h):
            return ["washer completed at 08:00", "dishwasher pending"]

        @staticmethod
        def all_results():
            return {
                "washer": [{"day": 0, "completed": True, "start_h": 8.0}],
                "dishwasher": [{"day": 0, "completed": False, "status": "pending"}],
            }

    loop = SimpleNamespace(
        appliance_suite=CompletedWasherSuite(),
        current_occupied=True,
        current_occupancy_count=1.0,
        current_occupancy_source="shared_calendar",
        agent_profile_capsule_by_event_id={"e": {"text": "Visible facts only."}},
        agent_memory_capsule_by_event_id={"e": {}},
        agent_preference_memory={},
    )
    appliance_config = {
        "washer": {"present": True, "shiftable": True, "duration_h": 2.0},
        "dishwasher": {"present": True, "shiftable": True, "duration_h": 1.5},
    }

    inputs = fr._adaptive_v3_observable_planning_inputs(
        loop,
        event_id="e",
        sim_h=16.5,
        hod=16.5,
        temp=25.0,
        out_t=30.0,
        facility_w=1000.0,
        observable_calendar={"occupied": True},
        memory_event={"id": "e", "trigger_h": 18.0, "end_h": 19.0},
        vpp_event={"id": "e", "trigger_h": 18.0, "end_h": 19.0},
        user_input="Keep completed tasks unchanged.",
        appliance_config=appliance_config,
        setpoint_min_c=22.0,
        setpoint_max_c=28.0,
    )

    required = inputs["observable_state"]["required_appliance_action_fields"]
    constraint_ids = {
        item["constraint_id"] for item in inputs["explicit_constraints"]
    }
    assert "washer_start_h" not in required
    assert "washer_skip" not in required
    assert "dishwasher_start_h" in required
    assert "dishwasher_skip" in required
    assert "washer_outside_vpp_window" not in constraint_ids
    assert "dishwasher_outside_vpp_window" in constraint_ids
    assert fr._missing_explicit_appliance_actions(
        {"dishwasher_start_h": 20.0, "dishwasher_skip": False},
        appliance_config,
        adaptive_contract=True,
        completed_services={"washer"},
    ) == []


def test_acceptance_gate_is_reused_only_for_the_same_concrete_plan() -> None:
    gate = {
        "accepted": True,
        "accepted_execution_plan": {
            "setpoint": 26.0,
            "appliance_actions": {"washer_start_h": 20.0},
        },
    }

    assert fr._vpp_gate_matches_current_plan(
        gate,
        {"setpoint": 26.1, "appliance_actions": {"washer_start_h": 20.0}},
    )
    assert not fr._vpp_gate_matches_current_plan(
        gate,
        {"setpoint": 26.0, "appliance_actions": {"washer_start_h": 14.0}},
    )
    assert not fr._vpp_gate_matches_current_plan(
        gate,
        {"setpoint": 26.0, "appliance_actions": {}},
    )


def test_v3_feasible_pre_event_consent_is_retained_at_event_start() -> None:
    gate = {
        "accepted": True,
        "accepted_execution_plan": {
            "setpoint": 26.0,
            "appliance_actions": {
                "washer_start_h": 19.0,
                "washer_skip": False,
            },
        },
    }
    replacement = {
        "setpoint": 27.0,
        "appliance_actions": {"washer_start_h": 18.0, "washer_skip": False},
    }

    retained, reused, errors = fr._adaptive_v3_retain_feasible_accepted_commitment(
        gate,
        replacement,
        action_validator=lambda actions: [],
        setpoint_min_c=23.0,
        setpoint_max_c=28.0,
    )

    assert reused is True
    assert errors == []
    assert retained["setpoint"] == 26.0
    assert retained["appliance_actions"]["washer_start_h"] == 19.0
    assert retained["accepted_commitment_reused"] is True

    not_retained, reused, errors = fr._adaptive_v3_retain_feasible_accepted_commitment(
        gate,
        replacement,
        action_validator=lambda actions: ["washer schedule became infeasible"],
        setpoint_min_c=23.0,
        setpoint_max_c=28.0,
    )
    assert reused is False
    assert errors == ["washer schedule became infeasible"]
    assert not_retained == replacement


def test_callback_scope_commitment_validator_rechecks_current_appliance_contract() -> None:
    appliance_config = {
        "washer": {
            "present": True,
            "earliest_h": 8.0,
            "latest_h": 22.0,
            "duration_h": 2.0,
            "shiftable": True,
            "dr_adjustable": True,
        },
        "dishwasher": {"present": False},
        "dryer": {"present": False},
        "water_heater": {"present": False},
        "ev": {"present": False},
    }
    event = {"id": "vpp1", "trigger_h": 18.0, "end_h": 19.0}

    assert fr._adaptive_v3_commitment_action_errors(
        {"washer_start_h": 19.0, "washer_skip": False},
        appliance_config=appliance_config,
        vpp_event=event,
        current_hod=18.0,
    ) == []
    errors = fr._adaptive_v3_commitment_action_errors(
        {"washer_start_h": 18.0, "washer_skip": False},
        appliance_config=appliance_config,
        vpp_event=event,
        current_hod=18.0,
    )
    assert any("VPP schedule conflict" in error for error in errors)
