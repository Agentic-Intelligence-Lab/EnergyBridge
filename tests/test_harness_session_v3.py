from __future__ import annotations

import json
import re
from copy import deepcopy

import pytest

from energybridge.harness import session_v3 as session_v3_module
from energybridge.harness.memory_v3 import MEMORY_V3_VERSION
from energybridge.harness.session_v3 import (
    HARNESS_RESOLUTION_VERSION,
    HARNESS_SESSION_VERSION,
    PREPARED_DECISION_VERSION,
    initialize_harness_session,
    prepare_harness_decision,
    record_harness_outcome,
    resolve_harness_plan,
)


NOW = "2026-08-27T08:00:00+00:00"
EVENT_TIME = "2026-08-27T10:00:00+00:00"
OUTCOME_TIME = "2026-08-27T11:00:00+00:00"


def _onboarding(**private: object) -> dict:
    return {
        "answers": [
            {
                "id": "thermostat_flexibility",
                "answer": "About one degree for a short event is fine.",
                "selected_option_ids": ["small_1c_short"],
            },
            {
                "id": "decision_information",
                "answer": "Tell me the concrete household benefit before I decide.",
                "selected_option_ids": [],
            },
        ],
        "hidden_persona": {"acceptance_target": 0.99},
        **private,
    }


def _session(**private: object) -> dict:
    return initialize_harness_session(
        _onboarding(**private),
        "home-7",
        calendar={"occupied": True, "constraints": ["dinner"]},
        devices={"ac": {"present": True, "setpoint_preferred_max_c": 27}},
        observed_at=NOW,
    )


def _event(**private: object) -> dict:
    return {
        "id": "event-1",
        "type": "peak_event",
        "trigger_h": 18,
        "end_h": 19,
        "price_level": "high",
        **private,
    }


def _state(**private: object) -> dict:
    return {
        "indoor_temp_c": 25.5,
        "occupied": True,
        "control_limits": {"setpoint": {"min": 22, "max": 29}},
        **private,
    }


def _prepared(*, advisors: list[dict] | None = None) -> dict:
    return prepare_harness_decision(
        _session(),
        _event(),
        _state(),
        advisor_candidates=advisors,
        observed_at=EVENT_TIME,
    )


def _response(setpoint: float, candidate_id: str = "household-fit") -> dict:
    return {
        "candidate_plans": [
            {
                "candidate_id": candidate_id,
                "plan": {"setpoint": setpoint, "appliances": {"washer_start_h": 20}},
                "objective_estimates": {
                    "comfort": {"value": 0.8, "direction": "max", "confidence": 0.6}
                },
                "evidence_citations": ["observable thermostat answer"],
            }
        ],
        "selected_candidate_id": candidate_id,
        "selection_reason": "This plan best matches the available household evidence.",
    }


def _episode(session: dict, episode_id: str = "event-1") -> dict:
    return next(item for item in session["memory"]["episodes"] if item["episode_id"] == episode_id)


def test_initialize_is_json_only_observable_and_does_not_mutate_inputs() -> None:
    onboarding = _onboarding(method_name="private-planner", api_key="sk-do-not-store-123456789")
    calendar = {"occupied": True, "model": "private-calendar-model"}
    devices = {"ac": {"present": True, "provider": "private-vendor"}}
    originals = deepcopy((onboarding, calendar, devices))

    session = initialize_harness_session(
        onboarding,
        "home-7",
        calendar=calendar,
        devices=devices,
        observed_at=NOW,
    )

    assert session["schema_version"] == HARNESS_SESSION_VERSION
    assert session["memory"]["version"] == MEMORY_V3_VERSION
    assert session["created_at"] == NOW
    serialized = json.dumps(session, ensure_ascii=False, allow_nan=False)
    assert "private-planner" not in serialized
    assert "private-calendar-model" not in serialized
    assert "private-vendor" not in serialized
    assert "sk-do-not-store" not in serialized
    assert (onboarding, calendar, devices) == originals


def test_prepare_is_method_blind_but_keeps_evidence_and_open_planning() -> None:
    first_session = _session(method_name="planner-alpha", model_name="foundation-alpha")
    second_session = _session(method_name="planner-beta", model_name="foundation-beta")
    event_a = _event(controller_method="planner-alpha")
    event_b = _event(controller_method="planner-beta")
    state_a = _state(
        llm_model="foundation-alpha",
        upstream_model="AcmeZeta-X",
        provider_name="AcmeCloud",
        auth_header="Token arbitraryCredential12345",
    )
    state_b = _state(
        llm_model="foundation-beta",
        upstream_model="OtherModel-X",
        provider_name="OtherCloud",
        auth_header="Token anotherCredential12345",
    )
    original = deepcopy((first_session, event_a, state_a))
    advisor_a = [{"method": "planner-alpha", "source": "alpha", "plan": {"setpoint": 28}}]
    advisor_b = [{"method": "planner-beta", "source": "beta", "plan": {"setpoint": 28}}]

    first = prepare_harness_decision(
        first_session,
        event_a,
        state_a,
        advisor_candidates=advisor_a,
        observed_at=EVENT_TIME,
    )
    second = prepare_harness_decision(
        second_session,
        event_b,
        state_b,
        advisor_candidates=advisor_b,
        observed_at=EVENT_TIME,
    )

    assert first["schema_version"] == PREPARED_DECISION_VERSION
    assert first["planning"] == second["planning"]
    assert first["profile_capsule"] == second["profile_capsule"]
    assert first["memory_capsule"] == second["memory_capsule"]
    prompt = first["planning"]["system_prompt"] + first["planning"]["user_prompt"]
    assert "planner-alpha" not in prompt
    assert "foundation-alpha" not in prompt
    assert "AcmeZeta-X" not in prompt
    assert "AcmeCloud" not in prompt
    assert "arbitraryCredential" not in prompt
    assert "choose how many are useful" in prompt
    assert "acceptance probability" in prompt
    assert first["profile_capsule"]["evidence_refs"]
    assert first["memory_capsule"]["privacy_scope"] == "controller_observable_only"
    assert (first_session, event_a, state_a) == original
    json.dumps(first, ensure_ascii=False, allow_nan=False)


def test_session_removes_planner_identity_from_text_but_keeps_household_fact() -> None:
    session = initialize_harness_session(
        {
            "answers": [
                {
                    "id": "refresh_token",
                    "answer": "Token abcdefghijklmnopqrstuvwxyz",
                },
                {
                    "id": "provider_name",
                    "answer": "AcmeCloud",
                },
                {
                    "id": "routine",
                    "answer": "EnergyBridge and HEMA on OpenAI GPT-9 aside, preserve dinner at 18:30.",
                }
            ]
        },
        "household-identity",
        observed_at=NOW,
    )
    prepared = prepare_harness_decision(
        session,
        _event(),
        _state(note="PPO and Qwen must not identify this controller"),
        observed_at=NOW,
    )

    rendered = json.dumps(prepared, ensure_ascii=False, sort_keys=True).lower()
    for identity in ("hema", "openai", "gpt-9", "ppo", "qwen"):
        assert re.search(rf"\b{re.escape(identity)}\b", rendered) is None
    assert "EnergyBridge" not in json.dumps(prepared, ensure_ascii=False, sort_keys=True)
    assert "dinner at 18:30" in rendered
    assert "refresh_token" not in rendered
    assert "abcdefghijklmnopqrstuvwxyz" not in rendered
    assert "acmecloud" not in rendered


def test_privacy_boundary_removes_developer_endpoint_acceptance_and_override_fields() -> None:
    onboarding = _onboarding(
        developer_context="DO_NOT_EXPOSE_DEVELOPER_CONTEXT",
        private="DO_NOT_EXPOSE_PRIVATE_CONTEXT",
        api_endpoint="https://private.invalid/v1",
        acceptance_probability=0.97,
        override_probability=0.88,
        apiKey="SECRETVALUE123456",
        refreshToken="TOKENVALUE123456",
        endpointUrl="internalbox:8443/v1",
        providerName="zetacorp",
        **{
            "note_sk-ABCDEFGHIJKLMNO": "ordinary",
            "https://key.private.example/v1": "ordinary",
        },
    )
    onboarding["answers"][0]["answer"] += (
        " provider AcmeCloud model CustomSolver planner BazQux algorithm AlphaBeta"
        " endpoint https://private.example/v1"
        " api key ABCDEF123456XYZ key ABCDEF123456XYZ provider zetacorp model fooxyz"
        " planner bazqux controller gizmo42 algorithm alphabeta"
        " endpoint internalbox:8443/v1 endpoint [2001:db8::1]:8443/v1;"
        " the key routine is dinner"
    )
    event = _event(
        developer_message="DO_NOT_EXPOSE_EVENT_DEVELOPER",
        vpp_override_prob=0.75,
    )
    state = _state(
        api={"endpoint": "https://state-private.invalid/v1"},
        private_data={"note": "DO_NOT_EXPOSE_STATE_PRIVATE"},
        acceptance_target=0.8,
    )
    original = deepcopy((onboarding, event, state))

    session = initialize_harness_session(onboarding, "privacy-home", observed_at=NOW)
    prepared = prepare_harness_decision(
        session,
        event,
        state,
        advisor_candidates=[{
            "provider_endpoint": "https://advisor-private.invalid/v1",
            "override_prob": 0.66,
            "plan": {"setpoint": 26},
        }],
        observed_at=EVENT_TIME,
    )

    rendered = json.dumps(prepared, ensure_ascii=False, sort_keys=True)
    for sentinel in (
        "DO_NOT_EXPOSE_DEVELOPER_CONTEXT",
        "DO_NOT_EXPOSE_PRIVATE_CONTEXT",
        "private.invalid",
        "DO_NOT_EXPOSE_EVENT_DEVELOPER",
        "state-private.invalid",
        "DO_NOT_EXPOSE_STATE_PRIVATE",
        "advisor-private.invalid",
        "private.example",
        "CustomSolver",
        "BazQux",
        "AlphaBeta",
        "ABCDEF123456XYZ",
        "SECRETVALUE123456",
        "TOKENVALUE123456",
        "internalbox",
        "2001:db8",
        "zetacorp",
        "fooxyz",
        "bazqux",
        "gizmo42",
        "alphabeta",
        "note_sk-ABCDEFGHIJKLMNO",
        "key.private.example",
    ):
        assert sentinel not in rendered
    for forbidden_key in (
        '"developer_context"',
        '"private"',
        '"api_endpoint"',
        '"acceptance_probability"',
        '"override_probability"',
        '"vpp_override_prob"',
    ):
        assert forbidden_key not in rendered
    assert "the key routine is dinner" in rendered
    assert (onboarding, event, state) == original


def test_session_endpoint_redaction_uses_json_pointers_and_keeps_time_windows() -> None:
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
    safe = session_v3_module._observable_copy({
        "provenance": [*dotted_text, *evidence_pointers],
        "description": (
            "Evidence applies at 18:30-19:00 and 07:45. "
            "Bare endpoints private.service.academy/v1 and "
            "tenant.backend.solutions:8443/api are private. "
            "host=event.window.end_h is an endpoint in this labelled context."
        ),
    })

    assert safe["provenance"][-3:] == evidence_pointers
    assert safe["provenance"][:-3] == ["[private endpoint]"] * len(dotted_text)
    description = safe["description"]
    assert "18:30-19:00" in description
    assert "07:45" in description
    assert "private.service.academy" not in description
    assert "tenant.backend.solutions" not in description
    assert "host=event.window.end_h" not in description
    assert description.count("[private endpoint]") == 3


def test_two_legal_base_model_plans_remain_distinct_and_inputs_are_immutable() -> None:
    prepared = _prepared()
    original = deepcopy(prepared)

    first = resolve_harness_plan(prepared, _response(25.0), observed_at=EVENT_TIME)
    second = resolve_harness_plan(prepared, _response(27.0), observed_at=EVENT_TIME)

    assert first["schema_version"] == HARNESS_RESOLUTION_VERSION
    assert first["selection_status"] == second["selection_status"] == "selected"
    assert first["selected_executable_plan"]["setpoint"] == 25.0
    assert second["selected_executable_plan"]["setpoint"] == 27.0
    assert first["selected_executable_plan"] != second["selected_executable_plan"]
    assert prepared == original


def test_resolve_records_raw_and_validated_as_separate_auditable_stages() -> None:
    prepared = _prepared()
    response = {
        "candidate_plans": [
            {"candidate_id": "repairable", "plan": {"setpoint_c": 31.0}}
        ],
        "selected_candidate_id": "repairable",
    }

    resolution = resolve_harness_plan(prepared, response, observed_at=EVENT_TIME)
    stages = _episode(resolution["session"])["stages"]

    assert resolution["selected_executable_plan"] == {"setpoint": 29}
    assert set(stages) == {"raw_proposal", "validated"}
    assert stages["raw_proposal"]["plan"] == {"setpoint_c": 31.0}
    assert stages["validated"]["plan"] == {"setpoint": 29}
    assert stages["raw_proposal"]["plan_fingerprint"] != stages["validated"]["plan_fingerprint"]
    assert stages["validated"]["patches"]


def test_an_advisor_reference_can_never_replace_the_base_model_selection() -> None:
    prepared = _prepared(advisors=[{"method": "external", "plan": {"setpoint": 24}}])
    response = {
        "candidate_plans": [{"candidate_id": "own-plan", "plan": {"setpoint": 26}}],
        "selected_candidate_id": "advisor_01",
        "selection_reason": "Use the external suggestion.",
    }

    resolution = resolve_harness_plan(prepared, response, observed_at=EVENT_TIME)

    assert resolution["selection_status"] == "replan_required"
    assert resolution["selected_candidate_id"] is None
    assert resolution["selected_executable_plan"] is None
    assert resolution["session"]["memory"]["episodes"] == []
    audit = resolution["planning_evaluation"]["portfolio_audit"]["model_selection"]
    assert audit["advisor_override_allowed"] is False


def test_outcome_tracks_consent_execution_and_feedback_without_plan_conflation() -> None:
    resolution = resolve_harness_plan(_prepared(), _response(27.0), observed_at=EVENT_TIME)
    original_session = deepcopy(resolution["session"])

    updated = record_harness_outcome(
        resolution["session"],
        "event-1",
        consented={"decision": True, "feedback": "The explanation is specific enough."},
        executed={
            "plan": {"setpoint": 26.0, "appliances": {"washer_start_h": 20.5}},
            "execution_window": {"start_h": 18, "end_h": 19},
        },
        outcome={
            "accepted": True,
            "overall_score": 5,
            "target_achieved": True,
            "feedback": "Comfortable and the washer still finished on time.",
        },
        observed_at=OUTCOME_TIME,
    )
    episode = _episode(updated)
    stages = episode["stages"]

    assert list(stages) == ["raw_proposal", "validated", "consented", "executed", "outcome"]
    assert stages["consented"]["plan"] == stages["validated"]["plan"]
    assert stages["executed"]["plan"]["setpoint"] == 26.0
    assert stages["executed"]["plan_fingerprint"] != stages["validated"]["plan_fingerprint"]
    assert episode["causal_attribution"]["outcome_attribution"] == "observational_executed_plan"
    assert episode["causal_attribution"]["executed_exposure_fingerprint"] == stages["executed"]["plan_fingerprint"]
    assert updated["household_model"]["revision"] > original_session["household_model"]["revision"]
    assert resolution["session"] == original_session
    contextual_keys = {item["key"] for item in updated["memory"]["contextual_beliefs"].values()}
    assert {"consent_response", "overall_satisfaction"} <= contextual_keys


def test_unexecuted_outcome_stays_unattributed_and_execution_is_never_inferred() -> None:
    resolution = resolve_harness_plan(_prepared(), _response(27.0), observed_at=EVENT_TIME)
    updated = record_harness_outcome(
        resolution["session"],
        "event-1",
        consented=False,
        executed=False,
        outcome={
            "accepted": False,
            "overall_score": 1,
            "feedback": "I declined because this overlaps dinner.",
        },
        observed_at=OUTCOME_TIME,
    )
    episode = _episode(updated)

    assert "executed" not in episode["stages"]
    assert episode["causal_attribution"]["outcome_attribution"] == "unattributed_no_execution_evidence"
    assert "outcome_not_attributed_to_execution" in episode["integrity_flags"]
    contextual_keys = {item["key"] for item in updated["memory"]["contextual_beliefs"].values()}
    assert contextual_keys == {"consent_response"}
    experience = updated["household_model"]["contextual_preferences"][0]["experience"]
    assert experience["distribution"]["positive"] <= experience["distribution"]["negative"]
    assert not any(
        "overall_score" in str(item.get("fact", ""))
        for item in updated["household_model"]["evidence_index"]
    )

    with pytest.raises(ValueError, match="actuator-observed plan"):
        record_harness_outcome(
            resolution["session"],
            "event-1",
            consented=True,
            executed=True,
            observed_at=OUTCOME_TIME,
        )


def test_contradictory_consent_fields_are_rejected_without_mutating_session() -> None:
    resolution = resolve_harness_plan(_prepared(), _response(27.0), observed_at=EVENT_TIME)
    original = deepcopy(resolution["session"])

    with pytest.raises(ValueError, match="decision/accepted/approved disagree"):
        record_harness_outcome(
            resolution["session"],
            "event-1",
            consented={"decision": True, "accepted": False},
            observed_at=OUTCOME_TIME,
        )

    assert resolution["session"] == original


@pytest.mark.parametrize(
    "executed",
    [
        {"status": "cancelled", "plan": {"setpoint": 26}},
        {"executed": False, "setpoint": 26},
        {"status": "not_executed", "actions": [{"device": "ac", "setpoint": 26}]},
        {"status": "failed", "plan": {"setpoint": 26}},
        {"status": "rejected", "plan": {"setpoint": 26}},
        {"status": "aborted", "plan": {"setpoint": 26}},
        {"status": "skipped", "plan": {"setpoint": 26}},
        {"status": "not_applied", "plan": {"setpoint": 26}},
        {"status": "queued", "plan": {"setpoint": 26}},
        {"status": "pending", "plan": {"setpoint": 26}},
    ],
)
def test_non_execution_status_cannot_carry_an_executed_plan(executed: dict) -> None:
    resolution = resolve_harness_plan(_prepared(), _response(27.0), observed_at=EVENT_TIME)

    with pytest.raises(ValueError, match="non-execution status conflicts"):
        record_harness_outcome(
            resolution["session"],
            "event-1",
            consented=True,
            executed=executed,
            observed_at=OUTCOME_TIME,
        )


@pytest.mark.parametrize("status", ["executed", "applied", "succeeded", "completed"])
def test_affirmative_execution_status_with_explicit_plan_is_recorded(status: str) -> None:
    resolution = resolve_harness_plan(_prepared(), _response(27.0), observed_at=EVENT_TIME)

    updated = record_harness_outcome(
        resolution["session"],
        "event-1",
        consented=True,
        executed={"status": status, "plan": {"setpoint": 26}},
        observed_at=OUTCOME_TIME,
    )

    assert _episode(updated)["stages"]["executed"]["plan"] == {"setpoint": 26}


@pytest.mark.parametrize("status", ["failed", "rejected", "aborted", "skipped", "not_applied", "queued", "pending"])
def test_explicit_non_execution_without_plan_does_not_create_execution(status: str) -> None:
    resolution = resolve_harness_plan(_prepared(), _response(27.0), observed_at=EVENT_TIME)

    updated = record_harness_outcome(
        resolution["session"],
        "event-1",
        consented=True,
        executed={"status": status},
        observed_at=OUTCOME_TIME,
    )

    assert "executed" not in _episode(updated)["stages"]


@pytest.mark.parametrize(
    "executed",
    [
        {"status": "unknown"},
        {"status": "unknown", "plan": {"setpoint": 26}},
        {"status": "teleported"},
        {"status": "teleported", "plan": {"setpoint": 26}},
    ],
)
def test_unknown_execution_status_is_rejected_with_or_without_plan(executed: dict) -> None:
    resolution = resolve_harness_plan(_prepared(), _response(27.0), observed_at=EVENT_TIME)

    with pytest.raises(ValueError, match="execution status"):
        record_harness_outcome(
            resolution["session"],
            "event-1",
            executed=executed,
            observed_at=OUTCOME_TIME,
        )


@pytest.mark.parametrize(
    ("decision", "expected"),
    [
        ("accept", True),
        ("approved", True),
        ("yes", True),
        ("true", True),
        ("reject", False),
        ("declined", False),
        ("no", False),
        ("false", False),
    ],
)
def test_consent_string_aliases_are_normalized_to_boolean(decision: str, expected: bool) -> None:
    resolution = resolve_harness_plan(_prepared(), _response(27.0), observed_at=EVENT_TIME)

    updated = record_harness_outcome(
        resolution["session"],
        "event-1",
        consented={"decision": decision, "approved": expected},
        observed_at=OUTCOME_TIME,
    )

    consent_stage = _episode(updated)["stages"]["consented"]
    assert consent_stage["decision"] is expected


@pytest.mark.parametrize("consented", [{"decision": "maybe"}, {"feedback": "Looks fine"}])
def test_ambiguous_consent_mapping_is_rejected(consented: dict) -> None:
    resolution = resolve_harness_plan(_prepared(), _response(27.0), observed_at=EVENT_TIME)

    with pytest.raises(ValueError, match="consent"):
        record_harness_outcome(
            resolution["session"],
            "event-1",
            consented=consented,
            observed_at=OUTCOME_TIME,
        )


def test_executed_false_correction_retracts_execution_and_outcome_beliefs() -> None:
    resolution = resolve_harness_plan(_prepared(), _response(27.0), observed_at=EVENT_TIME)
    completed = record_harness_outcome(
        resolution["session"],
        "event-1",
        consented=True,
        executed={"status": "executed", "plan": {"setpoint": 26}},
        outcome={"overall_score": 5, "feedback": "Comfortable and worked well."},
        observed_at=OUTCOME_TIME,
    )
    original = deepcopy(completed)

    corrected = record_harness_outcome(
        completed,
        "event-1",
        executed=False,
        observed_at="2026-08-27T11:10:00+00:00",
    )

    assert completed == original
    episode = _episode(corrected)
    assert "executed" not in episode["stages"]
    assert "outcome" in episode["stages"]
    assert episode["causal_attribution"]["outcome_attribution"] == (
        "unattributed_no_execution_evidence"
    )
    assert episode["causal_attribution"]["executed_exposure_fingerprint"] is None
    assert episode["stage_history"][-1]["record_type"] == "retraction"
    decision = next(
        item for item in corrected["decisions"] if item["episode_id"] == "event-1"
    )
    assert decision["event_context"]["executed_plan"] == {}
    assert not any(
        belief.get("key") == "overall_satisfaction"
        for belief in corrected["memory"]["contextual_beliefs"].values()
    )


def test_split_stage_calls_retain_execution_context_and_profile_corrections_are_idempotent() -> None:
    resolution = resolve_harness_plan(_prepared(), _response(27.0), observed_at=EVENT_TIME)
    consented = record_harness_outcome(
        resolution["session"],
        "event-1",
        consented=True,
        observed_at="2026-08-27T10:10:00+00:00",
    )
    executed = record_harness_outcome(
        consented,
        "event-1",
        executed={"plan": {"setpoint": 26, "appliances": {"washer_start_h": 20.5}}},
        observed_at="2026-08-27T10:20:00+00:00",
    )
    negative = record_harness_outcome(
        executed,
        "event-1",
        outcome={"overall_score": 1, "feedback": "Too hot and disruptive."},
        observed_at="2026-08-27T11:00:00+00:00",
    )
    corrected = record_harness_outcome(
        negative,
        "event-1",
        outcome={"overall_score": 5, "feedback": "Comfortable and worked well."},
        observed_at="2026-08-27T11:05:00+00:00",
    )

    episode = _episode(corrected)
    assert episode["stages"]["executed"]["plan"]["setpoint"] == 26
    assert episode["context"]["features"]["action_tokens"] == ["thermostat", "washer_start_h"]
    assert episode["causal_attribution"]["outcome_attribution"] == "observational_executed_plan"
    decision = next(item for item in corrected["decisions"] if item["episode_id"] == "event-1")
    assert decision["event_context"]["executed_plan"]["setpoint"] == 26

    contextual = corrected["household_model"]["contextual_preferences"]
    assert len(contextual) == 1
    experience = contextual[0]["experience"]
    assert experience["evidence_count"] == 1
    assert experience["contradiction_count"] == 0
    assert experience["distribution"]["positive"] > experience["distribution"]["negative"]
    rendered_profile = json.dumps(corrected["household_model"], ensure_ascii=False)
    assert "Too hot and disruptive" not in rendered_profile

    repeated = record_harness_outcome(
        corrected,
        "event-1",
        outcome={"overall_score": 5, "feedback": "Comfortable and worked well."},
        observed_at="2026-08-27T11:05:00+00:00",
    )
    assert repeated["household_model"] == corrected["household_model"]


def test_v3_prior_memory_cannot_cross_household_boundaries() -> None:
    prior = _session()["memory"]
    assert prior["version"] == MEMORY_V3_VERSION

    with pytest.raises(ValueError, match="different household"):
        initialize_harness_session(
            _onboarding(),
            "home-8",
            prior_memory=prior,
            observed_at=NOW,
        )


def test_unicode_household_ids_keep_distinct_memory_namespaces() -> None:
    first = initialize_harness_session(
        _onboarding(), "家庭甲", observed_at=NOW
    )
    second = initialize_harness_session(
        _onboarding(), "家庭乙", observed_at=NOW
    )

    first_id = first["household_id"]
    second_id = second["household_id"]
    assert first_id != second_id
    assert first_id.startswith("household-")
    assert second_id.startswith("household-")
    with pytest.raises(ValueError, match="different household"):
        initialize_harness_session(
            _onboarding(),
            "家庭乙",
            prior_memory=first["memory"],
            observed_at=NOW,
        )


def test_non_execution_after_invalid_planning_can_record_unattributed_outcome() -> None:
    session = _session()
    prepared = prepare_harness_decision(
        session,
        {"id": "invalid-event", "trigger_h": 18.0, "end_h": 19.0},
        {"indoor_temp_c": 25.0},
        observed_at=NOW,
    )
    unresolved = resolve_harness_plan(
        prepared,
        {"candidate_plans": [], "selected_candidate_id": None},
        observed_at="2026-08-27T10:01:00+00:00",
    )
    assert unresolved["selection_status"] == "replan_required"
    assert unresolved["session"]["memory"]["episodes"] == []

    recorded = record_harness_outcome(
        unresolved["session"],
        "invalid-event",
        executed=False,
        outcome={"overall_score": 2, "feedback": "No plan was executed."},
        observed_at="2026-08-27T10:05:00+00:00",
    )

    episode = recorded["memory"]["episodes"][-1]
    assert "executed" not in episode["stages"]
    assert episode["causal_attribution"]["outcome_attribution"] == (
        "unattributed_no_execution_evidence"
    )
