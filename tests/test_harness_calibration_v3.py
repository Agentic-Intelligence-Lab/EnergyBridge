from __future__ import annotations

from energybridge.harness.calibration_v3 import (
    CALIBRATION_CAPSULE_VERSION,
    OUTCOME_CALIBRATION_VERSION,
    build_calibration_capsule,
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
