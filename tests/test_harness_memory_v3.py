from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from energybridge.harness.memory import (
    build_event_context as build_event_context_v2,
    initialize_memory as initialize_memory_v2,
    update_memory as update_memory_v2,
)
from energybridge.harness.memory_v3 import (
    EVENT_CONTEXT_V3_VERSION,
    MEMORY_V3_VERSION,
    build_event_context_v3,
    compact_memory_context_v3,
    initialize_memory_v3,
    load_memory_v3,
    migrate_v2_memory,
    observe_belief_v3,
    record_episode_stage,
    retract_episode_stage,
    refresh_beliefs_v3,
    retrieve_relevant_episodes,
    save_memory_v3,
    update_memory_v3,
)


def _all_keys(value: object) -> list[str]:
    if isinstance(value, dict):
        keys = [str(key) for key in value]
        for item in value.values():
            keys.extend(_all_keys(item))
        return keys
    if isinstance(value, list):
        keys: list[str] = []
        for item in value:
            keys.extend(_all_keys(item))
        return keys
    return []


def _questionnaire() -> dict:
    return {
        "answers": [
            {
                "id": "appliance_shift_consent",
                "question": "How should flexible appliances be moved?",
                "answer": "Ask before changing a protected deadline.",
                "selected_option_ids": ["ask_first"],
            }
        ],
        "hidden_persona": {"private_fact": "SECRET PERSONA"},
        "evaluator_state": {"target_acceptance": 0.8},
        "base_model": "SECRET MODEL",
    }


def _context(
    event_id: str,
    *,
    hour: float = 18.0,
    occupied: bool = True,
    validated_setpoint: float = 27.0,
    executed_setpoint: float | None = 26.0,
    observed_at: str = "2026-08-20T10:00:00+00:00",
) -> dict:
    executed = (
        {"mode": "balanced", "setpoint_c": executed_setpoint, "actions": {"washer": "19:30"}}
        if executed_setpoint is not None
        else None
    )
    return build_event_context_v3(
        {
            "id": event_id,
            "type": "vpp_peak",
            "trigger_h": hour,
            "end_h": hour + 1,
            "price_level": "high" if hour >= 17 else "low",
            "persona_config": {"secret": "never"},
        },
        calendar={"occupied": occupied, "constraints": ["dinner"] if occupied else ["away"]},
        home_state={"indoor_temp_c": 25.0},
        raw_proposal={"mode": "balanced", "setpoint_c": 28.0, "actions": {"washer": "18:30"}},
        validated_plan={
            "mode": "balanced",
            "setpoint_c": validated_setpoint,
            "actions": {"washer": "19:00"},
        },
        proposed_plan={
            "mode": "balanced",
            "setpoint_c": validated_setpoint,
            "actions": {"washer": "19:00"},
        },
        executed_plan=executed,
        observed_at=observed_at,
        observations={
            "sensor_quality": "good",
            "llm_model": "SECRET MODEL",
            "controller_method": "SECRET METHOD",
            "nested": {"evaluator_state": "SECRET EVALUATOR"},
            "note": "api_key=sk-thisMustNeverPersist12345",
        },
    )


def test_v3_memory_and_context_exclude_identity_evaluator_and_credentials() -> None:
    memory = initialize_memory_v3(
        _questionnaire(),
        household_id="home-7",
        method="must be ignored",
        model="must be ignored",
    )
    context = _context("event-private")

    assert memory["version"] == MEMORY_V3_VERSION
    assert memory["owner"] == {"household_id": "home-7"}
    assert context["version"] == EVENT_CONTEXT_V3_VERSION
    keys = {_key.lower() for _key in _all_keys({"memory": memory, "context": context})}
    assert "method" not in keys
    assert "model" not in keys
    assert "base_model" not in keys
    assert "llm_model" not in keys
    assert "controller_method" not in keys
    assert "hidden_persona" not in keys
    assert "evaluator_state" not in keys
    serialized = json.dumps({"memory": memory, "context": context}, ensure_ascii=False)
    assert "SECRET PERSONA" not in serialized
    assert "SECRET MODEL" not in serialized
    assert "SECRET METHOD" not in serialized
    assert "sk-thisMustNeverPersist" not in serialized
    assert "[redacted credential]" in serialized

    structured = build_event_context_v3(
        {"id": "event-structured-private"},
        observations={
            "upstream_model": "AcmeZeta-X",
            "provider_name": "AcmeCloud",
            "auth_header": "Token arbitraryCredential12345",
            "description": (
                "provider AcmeCloud model CustomSolver endpoint https://private.example/v1 "
                "planner BazQux algorithm AlphaBeta api key ABCDEF123456XYZ "
                "key ABCDEF123456XYZ; the key routine is dinner at 18:30"
            ),
            "household_fact": "Dinner is at 18:30.",
        },
    )
    structured_text = json.dumps(structured, ensure_ascii=False)
    assert "AcmeZeta-X" not in structured_text
    assert "AcmeCloud" not in structured_text
    assert "arbitraryCredential" not in structured_text
    assert "private.example" not in structured_text
    assert "CustomSolver" not in structured_text
    assert "BazQux" not in structured_text
    assert "AlphaBeta" not in structured_text
    assert "ABCDEF123456XYZ" not in structured_text
    assert "the key routine is dinner at 18:30" in structured_text
    assert "Dinner is at 18:30" in structured_text


def test_v3_memory_removes_planner_identity_from_text_but_keeps_household_fact() -> None:
    memory = initialize_memory_v3(
        {
            "answers": [
                {
                    "id": "routine",
                    "answer": "EnergyBridge and MPC from OpenAI GPT-9 aside, dinner at 18:30 must be protected.",
                }
            ]
        },
        household_id="home-identity",
    )

    serialized = json.dumps(memory, ensure_ascii=False, sort_keys=True).lower()
    assert re.search(r"\bmpc\b", serialized) is None
    assert re.search(r"\bopenai\b", serialized) is None
    assert re.search(r"\bgpt-9\b", serialized) is None
    assert "EnergyBridge" not in json.dumps(memory, ensure_ascii=False, sort_keys=True)
    assert "dinner at 18:30" in serialized


def test_outcome_is_attributed_to_executed_not_proposed_plan() -> None:
    memory = initialize_memory_v3(_questionnaire(), household_id="home-7")
    updated = update_memory_v3(
        memory,
        _context("event-1", validated_setpoint=27.0, executed_setpoint=26.0),
        {
            "accepted": True,
            "overall_score": 5,
            "target_achieved": True,
            "feedback": "Comfortable and worked well.",
        },
    )

    episode = updated["episodes"][0]
    stages = episode["stages"]
    assert list(stage for stage in ("raw_proposal", "validated", "consented", "executed", "outcome") if stage in stages) == [
        "raw_proposal",
        "validated",
        "consented",
        "executed",
        "outcome",
    ]
    assert stages["validated"]["plan_fingerprint"] != stages["executed"]["plan_fingerprint"]
    attribution = episode["causal_attribution"]
    assert attribution["decision_exposure_fingerprint"] == stages["consented"]["plan_fingerprint"]
    assert attribution["executed_exposure_fingerprint"] == stages["executed"]["plan_fingerprint"]
    assert attribution["outcome_attribution"] == "observational_executed_plan"
    assert attribution["causal_claim"] == "none_single_episode_observational_only"
    assert set(attribution["validation_to_execution_changed_fields"]) == {"actions", "setpoint_c"}
    satisfaction = next(
        belief
        for belief in updated["contextual_beliefs"].values()
        if belief["key"] == "overall_satisfaction"
    )
    assert satisfaction["evidence"][0]["source"] == "executed_outcome"
    assert satisfaction["evidence"][0]["episode_id"] == "event-1"


def test_unexecuted_outcome_does_not_become_execution_performance_memory() -> None:
    memory = initialize_memory_v3(_questionnaire(), household_id="home-7")
    updated = update_memory_v3(
        memory,
        _context("event-rejected", executed_setpoint=None),
        {
            "accepted": False,
            "overall_score": 1,
            "target_achieved": False,
            "feedback": "I reject this proposal; it would disrupt dinner.",
        },
    )

    episode = updated["episodes"][0]
    assert "executed" not in episode["stages"]
    assert episode["causal_attribution"]["outcome_attribution"] == "unattributed_no_execution_evidence"
    assert "outcome_not_attributed_to_execution" in episode["integrity_flags"]
    contextual_keys = {belief["key"] for belief in updated["contextual_beliefs"].values()}
    assert contextual_keys == {"consent_response"}


def test_temporal_order_controls_outcome_attribution_and_late_valid_backfill() -> None:
    context = _context("event-temporal", executed_setpoint=None)
    base = initialize_memory_v3({}, household_id="home-temporal")

    invalid = record_episode_stage(
        base,
        "event-temporal",
        "outcome",
        {"overall_score": 5},
        event_context=context,
        observed_at="2026-08-20T01:00:00+00:00",
    )
    invalid = record_episode_stage(
        invalid,
        "event-temporal",
        "executed",
        {"plan": {"setpoint": 26.0}},
        event_context=context,
        observed_at="2026-08-20T02:00:00+00:00",
    )
    invalid_episode = invalid["episodes"][0]
    assert invalid_episode["causal_attribution"]["outcome_attribution"] == (
        "unattributed_temporal_order_invalid"
    )
    assert "outcome_precedes_executed_observation" in invalid_episode["integrity_flags"]
    assert not any(
        belief.get("key") == "overall_satisfaction"
        for belief in invalid["contextual_beliefs"].values()
    )

    valid = record_episode_stage(
        base,
        "event-temporal",
        "outcome",
        {"overall_score": 4},
        event_context=context,
        observed_at="2026-08-20T02:00:00+00:00",
    )
    valid = record_episode_stage(
        valid,
        "event-temporal",
        "executed",
        {"plan": {"setpoint": 26.0}},
        event_context=context,
        observed_at="2026-08-20T01:00:00+00:00",
    )
    valid_episode = valid["episodes"][0]
    assert valid_episode["causal_attribution"]["outcome_attribution"] == (
        "observational_executed_plan"
    )
    satisfaction = next(
        belief
        for belief in valid["contextual_beliefs"].values()
        if belief.get("key") == "overall_satisfaction"
    )
    assert satisfaction["value"] == 4


def test_integrity_flags_are_recomputed_from_all_current_stages() -> None:
    context = _context("event-global-flags", executed_setpoint=None)
    memory = initialize_memory_v3({}, household_id="home-global-flags")
    memory = record_episode_stage(
        memory,
        "event-global-flags",
        "outcome",
        {"overall_score": 3},
        event_context=context,
        observed_at="2026-08-20T03:00:00+00:00",
    )
    memory = record_episode_stage(
        memory,
        "event-global-flags",
        "validated",
        {"plan": {"setpoint_c": 27.0}},
        event_context=context,
        observed_at="2026-08-20T01:00:00+00:00",
    )

    episode = memory["episodes"][0]
    assert set(episode["stages"]) == {"validated", "outcome"}
    assert "missing_observation:executed" in episode["integrity_flags"]
    assert "missing_observation:consented" in episode["integrity_flags"]
    assert "outcome_not_attributed_to_execution" in episode["integrity_flags"]


def test_update_with_none_outcome_does_not_invent_outcome_stage() -> None:
    memory = initialize_memory_v3({}, household_id="home-no-outcome")
    updated = update_memory_v3(memory, _context("event-no-outcome"), None)

    episode = updated["episodes"][0]
    assert "raw_proposal" in episode["stages"]
    assert "validated" in episode["stages"]
    assert "executed" in episode["stages"]
    assert "outcome" not in episode["stages"]
    assert episode["causal_attribution"]["outcome_attribution"] == "not_observed"
    assert not any(
        belief.get("key") == "overall_satisfaction"
        for belief in updated["contextual_beliefs"].values()
    )


def test_execution_corrections_revoke_and_idempotently_replay_outcome_beliefs() -> None:
    context = _context("event-execution-correction", executed_setpoint=None)
    memory = initialize_memory_v3({}, household_id="home-execution-correction")
    memory = record_episode_stage(
        memory,
        "event-execution-correction",
        "executed",
        {"plan": {"setpoint_c": 26.0}},
        event_context=context,
        observed_at="2026-08-20T01:00:00+00:00",
    )
    memory = record_episode_stage(
        memory,
        "event-execution-correction",
        "outcome",
        {"overall_score": 5, "comfort_score": 4},
        event_context=context,
        observed_at="2026-08-20T02:00:00+00:00",
    )
    assert any(
        belief.get("key") == "overall_satisfaction"
        for belief in memory["contextual_beliefs"].values()
    )

    # A correction whose timestamp cannot be ordered invalidates the current
    # executed-outcome projection and must retract it.
    memory = record_episode_stage(
        memory,
        "event-execution-correction",
        "executed",
        {"plan": {"setpoint_c": 26.5}},
        event_context=context,
        observed_at="timestamp-unavailable",
    )
    episode = memory["episodes"][0]
    assert episode["causal_attribution"]["outcome_attribution"] == (
        "unattributed_temporal_order_unknown"
    )
    assert not any(
        evidence.get("source") == "executed_outcome"
        for belief in memory["contextual_beliefs"].values()
        for evidence in belief.get("evidence", [])
    )

    # A temporally valid correction may replay the same outcome once. A later
    # unrelated stage correction must not duplicate that evidence.
    memory = record_episode_stage(
        memory,
        "event-execution-correction",
        "executed",
        {"plan": {"setpoint_c": 25.5}},
        event_context=context,
        observed_at="2026-08-20T01:30:00+00:00",
    )
    memory = record_episode_stage(
        memory,
        "event-execution-correction",
        "validated",
        {"plan": {"setpoint_c": 27.0}},
        event_context=context,
        observed_at="2026-08-20T00:30:00+00:00",
    )
    satisfaction = next(
        belief for belief in memory["contextual_beliefs"].values()
        if belief.get("key") == "overall_satisfaction"
    )
    assert satisfaction["value"] == 5
    assert satisfaction["evidence_count"] == 1
    assert sum(
        evidence.get("source") == "executed_outcome"
        for evidence in satisfaction["evidence"]
    ) == 1


def test_retract_execution_preserves_audit_and_revokes_physical_outcome_attribution() -> None:
    memory = initialize_memory_v3({}, household_id="home-execution-retraction")
    memory = update_memory_v3(
        memory,
        _context("event-execution-retraction", executed_setpoint=26.0),
        {
            "accepted": True,
            "overall_score": 5,
            "feedback": "Comfortable and worked well.",
        },
    )
    original = json.loads(json.dumps(memory))

    retracted = retract_episode_stage(
        memory,
        "event-execution-retraction",
        "executed",
        reason="actuator log correction: command was not applied",
        observed_at="2026-08-20T03:00:00+00:00",
    )

    assert memory == original
    episode = retracted["episodes"][0]
    assert "executed" not in episode["stages"]
    assert "outcome" in episode["stages"]
    assert episode["causal_attribution"]["executed_exposure_fingerprint"] is None
    assert episode["causal_attribution"]["outcome_attribution"] == (
        "unattributed_no_execution_evidence"
    )
    assert "outcome_not_attributed_to_execution" in episode["integrity_flags"]
    assert episode["stage_history"][-1]["record_type"] == "retraction"
    assert episode["stage_history"][-1]["retracted_plan_fingerprint"]
    assert any(
        item.get("stage") == "executed" and item.get("plan_fingerprint")
        for item in episode["stage_history"][:-1]
    )
    assert not any(
        evidence.get("source") == "executed_outcome"
        for belief in retracted["contextual_beliefs"].values()
        for evidence in belief.get("evidence", [])
    )
    assert not any(
        belief.get("key") == "overall_satisfaction"
        for belief in retracted["contextual_beliefs"].values()
    )
    assert retract_episode_stage(
        retracted,
        "event-execution-retraction",
        "executed",
        observed_at="2026-08-20T04:00:00+00:00",
    ) == retracted


def test_beliefs_support_contradiction_idempotence_and_time_decay() -> None:
    memory = initialize_memory_v3({}, household_id="home-7")
    first = observe_belief_v3(
        memory,
        key="confirmation_required",
        value=True,
        source="user_statement",
        evidence_id="statement-1",
        reliability=0.9,
        observed_at="2026-01-01T00:00:00+00:00",
        half_life_days=10,
    )
    duplicate = observe_belief_v3(
        first,
        key="confirmation_required",
        value=True,
        source="user_statement",
        evidence_id="statement-1",
        reliability=0.9,
        observed_at="2026-01-01T00:00:00+00:00",
    )
    contradicted = observe_belief_v3(
        duplicate,
        key="confirmation_required",
        value=False,
        source="user_correction",
        evidence_id="correction-1",
        reliability=0.9,
        observed_at="2026-01-02T00:00:00+00:00",
    )
    belief = contradicted["stable_beliefs"]["confirmation_required"]

    assert duplicate == first
    assert belief["evidence_count"] == 2
    assert belief["contradiction_count"] == 1
    assert belief["status"] == "conflicted"
    refreshed = refresh_beliefs_v3(contradicted, "2026-03-15T00:00:00+00:00")
    decayed = refreshed["stable_beliefs"]["confirmation_required"]
    assert decayed["confidence"] < belief["confidence"]
    assert decayed["status"] == "stale"


def test_corrected_consent_and_outcome_supersede_old_belief_projection() -> None:
    context = _context("event-correction")
    memory = initialize_memory_v3({}, household_id="home-correction")
    memory = record_episode_stage(
        memory,
        "event-correction",
        "executed",
        {"plan": {"setpoint": 26.0}},
        event_context=context,
        observed_at="2026-08-20T01:00:00+00:00",
    )
    memory = record_episode_stage(
        memory,
        "event-correction",
        "consented",
        {"decision": False, "plan": {"setpoint": 26.0}},
        event_context=context,
        observed_at="2026-08-20T00:30:00+00:00",
    )
    memory = record_episode_stage(
        memory,
        "event-correction",
        "outcome",
        {"overall_score": 1},
        event_context=context,
        observed_at="2026-08-20T02:00:00+00:00",
    )
    memory = record_episode_stage(
        memory,
        "event-correction",
        "consented",
        {"decision": True, "plan": {"setpoint": 26.0}},
        event_context=context,
        observed_at="2026-08-20T00:40:00+00:00",
    )
    memory = record_episode_stage(
        memory,
        "event-correction",
        "outcome",
        {"overall_score": 5},
        event_context=context,
        observed_at="2026-08-20T02:10:00+00:00",
    )

    consent = next(
        belief for belief in memory["contextual_beliefs"].values()
        if belief.get("key") == "consent_response"
    )
    satisfaction = next(
        belief for belief in memory["contextual_beliefs"].values()
        if belief.get("key") == "overall_satisfaction"
    )
    assert consent["value"] is True
    assert consent["evidence_count"] == 1
    assert consent["contradiction_count"] == 0
    assert satisfaction["value"] == 5
    assert satisfaction["evidence_count"] == 1
    assert satisfaction["contradiction_count"] == 0
    episode = memory["episodes"][0]
    assert len([item for item in episode["stage_history"] if item["stage"] == "consented"]) == 2
    assert len([item for item in episode["stage_history"] if item["stage"] == "outcome"]) == 2
    assert any(
        item.get("status") == "superseded_stage_correction"
        for item in memory["belief_revision_ledger"]
    )


def test_retrieval_and_budgeted_capsule_keep_causal_evidence() -> None:
    memory = initialize_memory_v3(_questionnaire(), household_id="home-7")
    memory = update_memory_v3(
        memory,
        _context("similar-negative", observed_at="2026-08-18T10:00:00+00:00"),
        {"accepted": True, "overall_score": 2, "feedback": "Too hot and disruptive. " * 30},
    )
    memory = update_memory_v3(
        memory,
        _context(
            "different-negative",
            hour=8,
            occupied=False,
            observed_at="2026-08-19T10:00:00+00:00",
        ),
        {"accepted": True, "overall_score": 1, "feedback": "Too cold and disruptive."},
    )
    current = _context("current", observed_at="2026-08-20T10:00:00+00:00")

    relevant = retrieve_relevant_episodes(memory, current, k=2)
    assert relevant[0]["episode_id"] == "similar-negative"
    assert relevant[0]["retrieval"]["context_similarity"] > relevant[1]["retrieval"]["context_similarity"]
    capsule = compact_memory_context_v3(memory, current, k=2, max_chars=2400)
    serialized = json.dumps(capsule, ensure_ascii=False, sort_keys=True)
    assert len(serialized) <= 2400
    assert capsule["serialized_chars"] == len(serialized)
    if capsule["relevant_episodes"]:
        episode = capsule["relevant_episodes"][0]
        assert episode["attribution"]["outcome_attribution"] == "observational_executed_plan"
        assert episode["evidence_refs"]


def test_compact_memory_retains_bounded_multi_dispatch_execution_evidence() -> None:
    memory = initialize_memory_v3(_questionnaire(), household_id="home-7")
    exposures = [
        {
            "simulation_hour": 8.0,
            "plan": {"setpoint": 24.5, "appliance_actions": {"washer_start_h": 20.0}},
            "fingerprint": "early",
        },
        {
            "simulation_hour": 18.0,
            "plan": {"setpoint": 26.0, "appliance_actions": {"washer_start_h": 20.0}},
            "fingerprint": "event",
        },
    ]
    context = build_event_context_v3(
        {"id": "multi", "trigger_h": 18.0, "end_h": 19.0},
        calendar={"occupied": True},
        executed_plan={
            "setpoint": 26.0,
            "appliance_actions": {"washer_start_h": 20.0},
            "execution_exposures": exposures,
        },
        observations={"execution_exposure_count": 2},
        observed_at="2026-08-20T10:00:00+00:00",
    )
    memory = update_memory_v3(
        memory,
        context,
        {"overall_score": 4, "feedback": "The sequence worked well."},
    )

    capsule = compact_memory_context_v3(memory, context, k=1, max_chars=5000)
    executed = capsule["relevant_episodes"][0]["executed_plan"]
    assert executed["execution_exposure_count"] == 2
    assert [item["simulation_hour"] for item in executed["execution_exposures"]] == [
        8.0,
        18.0,
    ]


def test_compact_memory_links_professional_planning_evidence_to_outcome() -> None:
    memory = initialize_memory_v3(_questionnaire(), household_id="home-7")
    context = _context("evidence-linked")
    planning_evidence = {
        "schema_version": "energybridge.candidate_impact.v3",
        "candidate_id": "household_choice",
        "offer_specific_changed_paths": ["/setpoint", "/appliances/washer_start_h"],
        "hvac_impact": {
            "expected_demand_direction": "lower_cooling_demand",
            "energy_kwh_estimate": None,
        },
        "aggregate": {
            "fixed_load_vpp_overlap_energy_kwh": 0.0,
            "whole_home_energy_claimed": False,
        },
        "findings": [],
        "limitations": ["HVAC energy is directional until a thermal model supplies a trace"],
        "provider": "must not survive",
    }
    memory = update_memory_v3(
        memory,
        context,
        {
            "overall_score": 4,
            "feedback": "This worked well.",
            "planning_evidence": planning_evidence,
        },
    )

    capsule = compact_memory_context_v3(memory, context, k=1, max_chars=6000)
    evidence = capsule["relevant_episodes"][0]["outcome"]["planning_evidence"]
    assert evidence["candidate_id"] == "household_choice"
    assert evidence["hvac_impact"]["energy_kwh_estimate"] is None
    assert evidence["aggregate"]["whole_home_energy_claimed"] is False
    assert "provider" not in evidence


def test_compact_memory_calibrates_only_attributed_executed_outcomes() -> None:
    memory = initialize_memory_v3(_questionnaire(), household_id="home-calibration")
    context = _context("calibrated-event")
    calibration = {
        "schema_version": "energybridge.outcome_calibration.v1",
        "candidate_id": "household_choice",
        "plan_fingerprint": "forecast-1",
        "observations": [
            {
                "signal": "non_ac_event_overlap",
                "forecast": {"overlapping_devices": []},
                "measurement": {"overlapping_devices": []},
                "agreement": True,
                "evidence_paths": [
                    "/planning_evidence/device_impacts",
                    "/outcome/appliance_summary",
                ],
            },
            {
                "signal": "service_completion",
                "device": "washer",
                "forecast": True,
                "measurement": False,
                "agreement": False,
                "evidence_paths": [
                    "/planning_evidence/device_impacts/washer",
                    "/outcome/appliance_summary/washer",
                ],
            },
        ],
        "observation_count": 2,
        "policy_update_performed": False,
        "ranking_performed": False,
    }
    memory = update_memory_v3(
        memory,
        context,
        {"overall_score": 3, "planning_calibration": calibration},
    )

    capsule = compact_memory_context_v3(memory, context, k=1, max_chars=6000)
    aggregate = capsule["professional_calibration"]
    assert aggregate["signal_summaries"]["non_ac_event_overlap"]["agreement_count"] == 1
    assert aggregate["signal_summaries"]["service_completion"]["disagreement_count"] == 1
    assert aggregate["policy_update_performed"] is False
    assert aggregate["ranking_performed"] is False
    assert capsule["relevant_episodes"][0]["outcome"]["planning_calibration"][
        "plan_fingerprint"
    ] == "forecast-1"
    serialized = json.dumps(capsule, sort_keys=True)
    assert "action_recommendation" not in serialized
    assert "policy_weight" not in serialized


def test_compact_memory_does_not_calibrate_unexecuted_outcome() -> None:
    memory = initialize_memory_v3(_questionnaire(), household_id="home-no-execution")
    context = _context("not-executed", executed_setpoint=None)
    memory = update_memory_v3(
        memory,
        context,
        {
            "accepted": False,
            "planning_calibration": {
                "schema_version": "energybridge.outcome_calibration.v1",
                "observations": [{"signal": "service_completion", "agreement": False}],
            },
        },
    )

    capsule = compact_memory_context_v3(memory, context, k=1, max_chars=6000)
    assert capsule["professional_calibration"]["source_record_count"] == 0
    assert capsule["professional_calibration"]["signal_summaries"] == {}


def test_v2_migration_rebuilds_contextual_attribution_and_drops_method() -> None:
    v2 = initialize_memory_v2(_questionnaire(), persona_id="private-namespace", method="secret-method")
    context = build_event_context_v2(
        {"id": "legacy-rejected", "type": "vpp_peak", "trigger_h": 18, "end_h": 19},
        proposed_plan={"mode": "balanced", "setpoint_c": 27},
        observed_at="2026-08-20T10:00:00+00:00",
    )
    v2 = update_memory_v2(
        v2,
        context,
        {"accepted": False, "overall_score": 1, "feedback": "I reject this proposal."},
    )

    migrated = migrate_v2_memory(v2)
    assert migrated["version"] == MEMORY_V3_VERSION
    assert migrated["owner"]["household_id"].startswith("migrated-household-")
    assert set(migrated["owner"]) == {"household_id"}
    assert all("method" not in key.lower().split("_") for key in _all_keys(migrated))
    episode = migrated["episodes"][0]
    assert "executed" not in episode["stages"]
    assert episode["causal_attribution"]["outcome_attribution"] == "unattributed_no_execution_evidence"
    contextual_keys = {belief["key"] for belief in migrated["contextual_beliefs"].values()}
    assert "consent_response" in contextual_keys
    assert "overall_satisfaction" not in contextual_keys


def test_persistence_is_opt_in_atomic_private_and_integrity_checked(tmp_path) -> None:
    memory = initialize_memory_v3(_questionnaire(), household_id="home-7")
    target = tmp_path / "memory.json"

    with pytest.raises(PermissionError, match="allow_persistence"):
        save_memory_v3(memory, target)
    save_memory_v3(memory, target, allow_persistence=True)
    assert os.stat(target).st_mode & 0o777 == 0o600
    assert load_memory_v3(
        target,
        allow_persistence=True,
        expected_household_id="home-7",
    ) == memory
    with pytest.raises(ValueError, match="household identity"):
        load_memory_v3(
            target,
            allow_persistence=True,
            expected_household_id="another-home",
        )

    envelope = json.loads(target.read_text(encoding="utf-8"))
    envelope["memory"]["revision"] = 999
    target.write_text(json.dumps(envelope), encoding="utf-8")
    os.chmod(target, 0o600)
    with pytest.raises(ValueError, match="integrity check"):
        load_memory_v3(target, allow_persistence=True)


def test_persistence_rejects_contaminated_memory_and_symlink(tmp_path) -> None:
    memory = initialize_memory_v3({}, household_id="home-7")
    contaminated = dict(memory)
    contaminated["base_model"] = "must not persist"
    with pytest.raises(ValueError, match="privacy boundary"):
        save_memory_v3(contaminated, tmp_path / "bad.json", allow_persistence=True)

    real = tmp_path / "real.json"
    save_memory_v3(memory, real, allow_persistence=True)
    link = tmp_path / "link.json"
    link.symlink_to(real)
    with pytest.raises(ValueError, match="symlink"):
        load_memory_v3(link, allow_persistence=True)


def test_public_persistence_rejects_writable_parent_and_allows_private_parent(tmp_path) -> None:
    memory = initialize_memory_v3({}, household_id="home-parent-permissions")
    private_parent = tmp_path / "private-memory"
    private_parent.mkdir()
    os.chmod(private_parent, 0o700)
    private_target = private_parent / "memory.json"

    save_memory_v3(memory, private_target, allow_persistence=True)
    assert load_memory_v3(
        private_target,
        allow_persistence=True,
        expected_household_id="home-parent-permissions",
    ) == memory

    writable_parent = tmp_path / "shared-memory"
    writable_parent.mkdir()
    os.chmod(writable_parent, 0o777)
    writable_target = writable_parent / "memory.json"
    with pytest.raises(PermissionError, match="group/world writable"):
        save_memory_v3(memory, writable_target, allow_persistence=True)

    # Place an otherwise valid private file there without going through the
    # public save API, then verify the public load API independently rejects it.
    os.replace(private_target, writable_target)
    assert os.stat(writable_target).st_mode & 0o777 == 0o600
    with pytest.raises(PermissionError, match="group/world writable"):
        load_memory_v3(writable_target, allow_persistence=True)


def test_adversarial_private_numeric_fields_never_enter_memory() -> None:
    forbidden_numbers = [0.812345, 0.723456, 0.634567, 0.545678, 0.456789]
    context = build_event_context_v3(
        {"id": "event-adversarial-private", "type": "vpp_peak"},
        proposed_plan={
            "mode": "balanced",
            "uncertainty": {
                "developer_weight": forbidden_numbers[0],
                "private_score": forbidden_numbers[1],
                "api_endpoint": "https://private.invalid/v1",
                "acceptance_probability": forbidden_numbers[2],
                "override_probability": forbidden_numbers[3],
                "observable_sensor_quality": "good",
            },
        },
        observations={
            "developer_instruction": {"weight": forbidden_numbers[0]},
            "private_probability": forbidden_numbers[1],
            "api_endpoint": "https://private.invalid/v1",
            "baseline_acceptance": forbidden_numbers[2],
            "user_override_rate": forbidden_numbers[3],
            "semantic_updates": [
                {"key": "acceptance_probability", "value": forbidden_numbers[4]},
                {"key": "routine_priority", "value": "protect dinner"},
            ],
        },
        executed_plan={"mode": "balanced", "setpoint_c": 26.0},
        observed_at="2026-08-20T01:00:00+00:00",
    )
    memory = initialize_memory_v3({}, household_id="home-adversarial-private")
    memory = update_memory_v3(
        memory,
        context,
        {
            "accepted": True,
            "preference_observations": [
                {"key": "override_probability", "value": forbidden_numbers[3]},
                {"key": "routine_priority", "value": "protect dinner"},
            ],
            "user_feedback": {
                "belief_updates": {
                    "acceptance_probability": forbidden_numbers[2],
                    "routine_priority": "protect dinner",
                }
            },
        },
    )

    serialized = json.dumps(memory, ensure_ascii=False, sort_keys=True)
    keys = {_key.lower() for _key in _all_keys(memory)}
    for token in ("developer", "private", "api", "endpoint", "acceptance", "override"):
        assert all(token not in key.split("_") for key in keys)
    assert "private.invalid" not in serialized
    for number in forbidden_numbers:
        assert str(number) not in serialized
    assert memory["stable_beliefs"]["routine_priority"]["value"] == "protect dinner"


def test_persistence_rejects_symlink_in_ancestor_directory(tmp_path) -> None:
    memory = initialize_memory_v3({}, household_id="home-7")
    outside = tmp_path / "outside"
    outside.mkdir()
    os.chmod(outside, 0o700)
    (outside / "nested").mkdir()
    os.chmod(outside / "nested", 0o700)
    linked_root = tmp_path / "linked-root"
    linked_root.symlink_to(outside, target_is_directory=True)
    through_link = linked_root / "nested" / "memory.json"

    with pytest.raises(ValueError, match="symlink component"):
        save_memory_v3(memory, through_link, allow_persistence=True)

    real = outside / "nested" / "memory.json"
    save_memory_v3(memory, real, allow_persistence=True)
    with pytest.raises(ValueError, match="symlink component"):
        load_memory_v3(through_link, allow_persistence=True)


def test_low_level_executed_stage_requires_affirmative_physical_evidence() -> None:
    context = _context("event-execution-status", executed_setpoint=None)
    base = initialize_memory_v3({}, household_id="home-execution-status")

    for status in (
        "failed", "rejected", "aborted", "skipped", "not_applied",
        "queued", "pending", "unknown_status",
    ):
        with pytest.raises(ValueError, match="confirm physical execution"):
            record_episode_stage(
                base,
                "event-execution-status",
                "executed",
                {"status": status, "plan": {"setpoint": 26.0}},
                event_context=context,
                observed_at="2026-08-20T01:00:00+00:00",
            )

    with pytest.raises(ValueError, match="actuator-observed plan"):
        record_episode_stage(
            base,
            "event-execution-status",
            "executed",
            {"status": "executed"},
            event_context=context,
            observed_at="2026-08-20T01:00:00+00:00",
        )

    executed = record_episode_stage(
        base,
        "event-execution-status",
        "executed",
        {"status": "applied", "plan": {"setpoint": 26.0}},
        event_context=context,
        observed_at="2026-08-20T01:00:00+00:00",
    )
    executed = record_episode_stage(
        executed,
        "event-execution-status",
        "outcome",
        {"overall_score": 4},
        event_context=context,
        observed_at="2026-08-20T02:00:00+00:00",
    )
    assert executed["episodes"][0]["causal_attribution"]["outcome_attribution"] == (
        "observational_executed_plan"
    )
    assert any(
        belief.get("key") == "overall_satisfaction"
        for belief in executed["contextual_beliefs"].values()
    )


def test_privacy_projection_handles_camelcase_lowercase_labels_endpoints_and_paths() -> None:
    context = build_event_context_v3(
        {"id": "event-privacy-variants"},
        observations={
            "apiKey": "SECRETVALUE123456",
            "refreshToken": "TOKENVALUE123456",
            "endpointUrl": "internalbox:8443/v1",
            "providerName": "zetacorp",
            "note_sk-ABCDEFGHIJKLMNO": "ordinary",
            "https://key.private.example/v1": "ordinary",
            "description": (
                "provider zetacorp model fooxyz planner bazqux controller gizmo42 "
                "algorithm alphabeta endpoint internalbox:8443/v1 "
                "endpoint [2001:db8::1]:8443/v1; the key routine is dinner"
            ),
            "path_value": Path("/tmp/sk-ABCDEFGHIJKLMNO"),
        },
    )
    rendered = json.dumps(context, ensure_ascii=False, sort_keys=True)
    for forbidden in (
        "SECRETVALUE123456", "TOKENVALUE123456", "internalbox", "2001:db8",
        "zetacorp", "fooxyz", "bazqux", "gizmo42", "alphabeta",
        "sk-ABCDEFGHIJKLMNO",
        "key.private.example",
    ):
        assert forbidden.lower() not in rendered.lower()
    assert "the key routine is dinner" in rendered


def test_endpoint_redaction_uses_json_pointers_for_provenance_and_keeps_time_windows() -> None:
    dotted_text = [
        "memory.feedback.id",
        "profile.capabilities.ev",
        "device_capabilities.washer.earliest_h",
        "profile.traits.comfort",
        "event.window.end_h",
        "calendar.schedule.start",
        "memory.private.academy",
        "profile.backend.solutions",
    ]
    evidence_pointers = [
        "/memory/feedback/id",
        "/profile/capabilities/ev",
        "/device_capabilities/washer/earliest_h",
    ]
    context = build_event_context_v3(
        {"id": "event-dotted-provenance"},
        observations={
            "provenance": [*dotted_text, *evidence_pointers],
            "description": (
                "Evidence applies at 18:30-19:00 and 07:45. "
                "Bare endpoints private.service.academy/v1 and "
                "tenant.backend.solutions:8443/api are private. "
                "endpoint profile.traits.comfort is also an endpoint here."
            ),
        },
    )

    observations = context["observations"]
    assert observations["provenance"][-3:] == evidence_pointers
    assert observations["provenance"][:-3] == ["[private endpoint]"] * len(dotted_text)
    description = observations["description"]
    assert "18:30-19:00" in description
    assert "07:45" in description
    assert "private.service.academy" not in description
    assert "tenant.backend.solutions" not in description
    assert "endpoint profile.traits.comfort" not in description
    assert description.count("[private endpoint]") == 3
