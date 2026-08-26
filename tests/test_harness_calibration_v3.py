from __future__ import annotations

from energybridge.harness.calibration_v3 import (
    CALIBRATION_CAPSULE_VERSION,
    OUTCOME_CALIBRATION_VERSION,
    build_calibration_capsule,
    build_consensus_outcome_calibration_record,
    build_outcome_calibration_record,
)


def _planning_evidence() -> dict:
    return {
        "candidate_id": "household_plan",
        "plan_fingerprint": "abc123",
        "device_impacts": {
            "washer": {
                "task_completed": True,
                "vpp_overlap_h": 0.0,
                "vpp_overlap_energy_kwh": 0.0,
            },
            "water_heater": {
                "ready_by_declared_deadline": True,
                "vpp_overlap_h": 0.0,
                "vpp_overlap_energy_upper_bound_kwh": 0.0,
            },
        },
        "hvac_impact": {"comfort_violation_c": 0.0},
        "offer_specific_comparison": {
            "supported_benefit_claims": [
                {
                    "kind": "normalized_fixed_load_cost_reduction",
                    "amount": 2.7,
                    "unit": "normalized TOU cost/kWh",
                }
            ]
        },
    }


def test_outcome_calibration_compares_forecast_with_physical_measurement() -> None:
    result = build_outcome_calibration_record(
        _planning_evidence(),
        {
            "appliance_summary": {
                "washer": {"present": True, "completed": True, "ran_during_vpp": False},
                "water_heater": {
                    "present": True,
                    "ready_at_bath": True,
                    "ran_during_vpp": False,
                },
            },
            "comfort_violation_minutes": 0.0,
        },
    )

    assert result["schema_version"] == OUTCOME_CALIBRATION_VERSION
    assert result["observation_count"] == 4
    assert all(item["agreement"] for item in result["observations"])
    assert result["supported_claims"][0]["amount"] == 2.7
    assert result["claim_measurement_status"] == (
        "not_comparable_without_same-scope_measurement"
    )
    assert result["policy_update_performed"] is False
    assert result["ranking_performed"] is False


def test_calibration_surfaces_disagreement_without_reweighting_policy() -> None:
    record = build_outcome_calibration_record(
        _planning_evidence(),
        {
            "appliance_summary": {
                "washer": {"present": True, "completed": False, "ran_during_vpp": True},
                "water_heater": {
                    "present": True,
                    "ready_at_bath": False,
                    "ran_during_vpp": False,
                },
            },
            "comfort_violation_minutes": 15.0,
        },
    )
    capsule = build_calibration_capsule([record])

    assert capsule["schema_version"] == CALIBRATION_CAPSULE_VERSION
    overlap = capsule["signal_summaries"]["non_ac_event_overlap"]
    assert overlap["observation_count"] == 1
    assert overlap["agreement_count"] == 0
    assert overlap["disagreement_count"] == 1
    assert overlap["descriptive_agreement_rate"] == 0.0
    assert overlap["wilson_95_interval"] == [0.0, 0.793451]
    service = capsule["signal_summaries"]["service_completion"]
    assert service["observation_count"] == 2
    assert service["agreement_count"] == 0
    assert service["disagreement_count"] == 2
    assert service["descriptive_agreement_rate"] == 0.0
    assert service["wilson_95_interval"] == [0.0, 0.65762]
    assert len(capsule["recent_disagreements"]) == 4
    assert capsule["policy_update_performed"] is False
    assert capsule["ranking_performed"] is False


def test_missing_measurements_do_not_create_fake_residuals() -> None:
    record = build_outcome_calibration_record(_planning_evidence(), {})
    capsule = build_calibration_capsule([record])

    assert record["observations"] == []
    assert record["observation_count"] == 0
    assert capsule["signal_summaries"] == {}


def test_consensus_calibrates_only_signals_shared_by_every_execution_forecast() -> None:
    first = _planning_evidence()
    second = _planning_evidence()
    second["plan_fingerprint"] = "different-plan"
    # Service completion remains identical, while event-overlap prediction
    # changes for one dispatch and therefore must remain ambiguous.
    second["device_impacts"]["washer"]["vpp_overlap_h"] = 1.0
    outcome = {
        "appliance_summary": {
            "washer": {"present": True, "completed": True, "ran_during_vpp": False},
            "water_heater": {
                "present": True,
                "ready_at_bath": True,
                "ran_during_vpp": False,
            },
        },
        "comfort_violation_minutes": 0.0,
    }

    record = build_consensus_outcome_calibration_record([first, second], outcome)

    assert record["calibration_basis"] == "signal_consensus_across_execution_sequence"
    assert record["source_forecast_count"] == 2
    assert set(record["source_plan_fingerprints"]) == {"abc123", "different-plan"}
    assert {(item["signal"], item.get("device")) for item in record["observations"]} == {
        ("service_completion", "washer"),
        ("service_completion", "water_heater"),
        ("comfort_violation", None),
    }
    assert all(item["source_forecast_count"] == 2 for item in record["observations"])
    assert record["ambiguous_signals"] == [
        {
            "signal": "non_ac_event_overlap",
            "device": None,
            "reason": "bound_forecasts_disagree",
            "source_forecast_count": 2,
            "execution_forecast_count": 2,
        }
    ]
    assert record["policy_update_performed"] is False
    assert record["ranking_performed"] is False


def test_consensus_does_not_fill_a_forecast_missing_from_one_dispatch() -> None:
    first = _planning_evidence()
    second = _planning_evidence()
    second["plan_fingerprint"] = "missing-hvac"
    second["hvac_impact"] = {}

    record = build_consensus_outcome_calibration_record(
        [first, second],
        {
            "appliance_summary": {
                "washer": {"present": True, "completed": True, "ran_during_vpp": False},
                "water_heater": {"present": True, "ready_at_bath": True, "ran_during_vpp": False},
            },
            "comfort_violation_minutes": 0.0,
        },
    )

    assert all(item["signal"] != "comfort_violation" for item in record["observations"])
    assert any(
        item["signal"] == "comfort_violation"
        and item["reason"] == "forecast_missing_from_part_of_execution_sequence"
        for item in record["ambiguous_signals"]
    )
