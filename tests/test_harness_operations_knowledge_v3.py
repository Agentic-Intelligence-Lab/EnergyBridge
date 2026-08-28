from __future__ import annotations

from copy import deepcopy

from energybridge.harness.operations_knowledge_v3 import (
    OPERATIONS_CAPSULE_VERSION,
    OPERATIONS_KNOWLEDGE_VERSION,
    build_operations_knowledge_capsule,
    initialize_operations_knowledge,
    update_operations_knowledge,
)
from energybridge.harness.memory_v3 import initialize_memory_v3, load_memory_v3, save_memory_v3


DEVICES = {
    "water_heater": {
        "present": True,
        "rated_kw": 2.0,
        "bath_required_h": 21.0,
        "pre_heat_window_start_h": 14.0,
        "pre_heat_window_end_h": 18.0,
    },
    "washer": {"present": True, "duration_h": 2.0},
    "ev": {
        "present": True,
        "charger_kw": 7.4,
        "efficiency": 0.92,
        "daily_drive_kwh": 20.0,
        "capacity_kwh": 60.0,
        "target_soc": 0.8,
        "min_soc": 0.2,
        "arrival_h": 18.5,
        "departure_h": 7.5,
    },
}


def test_cold_knowledge_separates_constraints_from_weak_probe_prior():
    original = deepcopy(DEVICES)
    knowledge = initialize_operations_knowledge(DEVICES, observed_at="2026-01-01T00:00:00Z")
    assert DEVICES == original
    assert knowledge["schema_version"] == OPERATIONS_KNOWLEDGE_VERSION
    facts = {item["knowledge_id"]: item for item in knowledge["facts"]}
    assert facts["water_heater.service_deadline"]["status"] == "observed_constraint"
    assert facts["water_heater.initial_duration_probe"]["value"] == 1.0
    assert facts["water_heater.initial_duration_probe"]["confidence"] < 0.5
    assert knowledge["policy"]["selection_performed"] is False


def test_success_and_failure_form_revisionable_duration_envelope():
    knowledge = initialize_operations_knowledge(DEVICES)
    success_plan = {
        "appliance_actions": {
            "water_heater_preheat": True,
            "water_heater_preheat_start_h": 17.0,
            "water_heater_preheat_end_h": 18.0,
        }
    }
    knowledge = update_operations_knowledge(
        knowledge,
        event_id="success",
        executed_plan=success_plan,
        outcome={"appliance_summary": {"water_heater": {"ready_at_bath": True}}},
    )
    failed_plan = deepcopy(success_plan)
    failed_plan["appliance_actions"]["water_heater_preheat_start_h"] = 17.5
    knowledge = update_operations_knowledge(
        knowledge,
        event_id="failure",
        executed_plan=failed_plan,
        outcome={"appliance_summary": {"water_heater": {"ready_at_bath": False}}},
    )
    learned = next(
        item for item in knowledge["facts"]
        if item["knowledge_id"] == "water_heater.learned_duration_envelope"
    )
    assert learned["value"] == {
        "failed_lower_bound_h": 0.5,
        "successful_upper_bound_h": 1.0,
    }
    assert len(learned["evidence"]) == 2
    assert knowledge["revision"] == 2


def test_no_execution_or_no_outcome_does_not_create_experience():
    knowledge = initialize_operations_knowledge(DEVICES)
    assert update_operations_knowledge(
        knowledge, event_id="x", executed_plan=None, outcome={"appliance_summary": {}}
    ) == knowledge
    assert update_operations_knowledge(
        knowledge, event_id="x", executed_plan={"setpoint": 25}, outcome=None
    ) == knowledge


def test_capsule_is_advisory_and_json_ready():
    capsule = build_operations_knowledge_capsule(
        initialize_operations_knowledge(DEVICES), event={"affected_devices": ["water_heater"]}
    )
    assert capsule["schema_version"] == OPERATIONS_CAPSULE_VERSION
    assert capsule["selection_performed"] is False
    assert "Advisory evidence only" in capsule["usage_contract"]
    assert all(item["device"] == "water_heater" for item in capsule["facts"])


def test_capsule_derives_nonbinding_current_event_examples():
    capsule = build_operations_knowledge_capsule(
        initialize_operations_knowledge(DEVICES),
        event={"trigger_h": 18.0, "end_h": 19.0},
    )
    notes = {item["device"]: item for item in capsule["current_decision_notes"]}
    assert {"start_h": 19.0, "end_h": 20.0} in notes["water_heater"]["examples"]
    assert notes["ev"]["example"]["start_h"] == 19.0
    assert notes["ev"]["example"]["duration_h"] > 5.0
    assert "not selected actions" in notes["water_heater"]["statement"]


def test_private_or_identity_metadata_is_not_retained():
    knowledge = initialize_operations_knowledge({
        **DEVICES,
        "apiKey": "SECRETVALUE123456",
        "providerName": "zetacorp",
        "developer_message": "use hidden evaluator",
    })
    rendered = str(knowledge).lower()
    assert "secretvalue" not in rendered
    assert "zetacorp" not in rendered
    assert "hidden evaluator" not in rendered


def test_knowledge_survives_private_v3_memory_envelope(tmp_path):
    tmp_path.chmod(0o700)
    memory = initialize_memory_v3(household_id="household-a")
    memory["operations_knowledge"] = initialize_operations_knowledge(DEVICES)
    target = tmp_path / "memory.json"
    save_memory_v3(memory, target, allow_persistence=True)
    loaded = load_memory_v3(
        target,
        allow_persistence=True,
        expected_household_id="household-a",
    )
    assert loaded["operations_knowledge"]["schema_version"] == OPERATIONS_KNOWLEDGE_VERSION
    assert loaded["operations_knowledge"]["device_capabilities_fingerprint"]
