from __future__ import annotations

from copy import deepcopy
import json

from energybridge.harness.energy_tools_v3 import (
    ENERGY_IMPACT_SCHEMA_VERSION,
    build_hourly_tariff_snapshot,
    evaluate_candidate_impact,
    evaluate_portfolio_impacts,
)


def _state() -> dict:
    return {
        "device_capabilities": {
            "ac": {"present": True, "mode": "cooling"},
            "washer": {
                "present": True,
                "earliest_h": 8,
                "latest_h": 22,
                "duration_h": 2,
                "power_kw": 1.5,
            },
            "water_heater": {
                "present": True,
                "rated_kw": 2,
                "bath_required_h": 21,
            },
            "ev": {
                "present": True,
                "charger_kw": 7,
                "efficiency": 0.9,
                "daily_drive_kwh": 12,
            },
        },
        "realtime_device_state": {"ev_soc": 0.5},
    }


def _tariff() -> dict:
    return build_hourly_tariff_snapshot({hour: 1.0 + hour / 10.0 for hour in range(24)}, unit="normalized/kWh")


def test_tariff_snapshot_is_compact_complete_and_provider_neutral() -> None:
    result = build_hourly_tariff_snapshot([1 + hour / 100 for hour in range(24)], unit="EUR/kWh")
    assert result["complete_day"] is True
    assert result["coverage_hours"] == 24
    assert result["hours"][18] == {"hour": 18, "price": 1.18}
    assert "source" not in result


def test_half_open_event_boundary_does_not_count_action_at_end() -> None:
    result = evaluate_candidate_impact(
        {"candidate_id": "c1", "plan": {"setpoint": 26, "appliances": {"washer_start_h": 19, "washer_skip": False}}},
        observable_state=_state(),
        event={"trigger_h": 18, "end_h": 19},
        ordinary_plan={"setpoint": 25, "appliances": {}},
        tariff=_tariff(),
    )
    washer = result["device_impacts"]["washer"]
    assert washer["vpp_overlap_h"] == 0
    assert washer["vpp_overlap_energy_kwh"] == 0
    assert result["event_window"]["interval_semantics"] == "half_open_[start,end)"


def test_water_heater_overlap_is_bound_not_fake_exact_energy() -> None:
    result = evaluate_candidate_impact(
        {"plan": {"setpoint": 25, "appliances": {
            "water_heater_preheat": True,
            "water_heater_preheat_start_h": 17,
            "water_heater_preheat_end_h": 20,
            "water_heater_preheat_temp_c": 60,
        }}},
        observable_state=_state(),
        event={"trigger_hod": 18, "end_hod": 19},
        ordinary_plan={"setpoint": 25, "appliances": {}},
        tariff=_tariff(),
    )
    wh = result["device_impacts"]["water_heater"]
    assert wh["vpp_overlap_h"] == 1
    assert wh["vpp_overlap_energy_upper_bound_kwh"] == 2
    assert "scheduled_energy_kwh" not in wh
    assert result["findings"][0]["code"] == "scheduled_load_overlaps_event"


def test_fixed_load_cost_integrates_across_hourly_prices() -> None:
    tariff = build_hourly_tariff_snapshot({18: 2, 19: 4, 20: 10})
    result = evaluate_candidate_impact(
        {"plan": {"appliances": {"washer_start_h": 18.5, "washer_skip": False}}},
        observable_state=_state(),
        event={"trigger_h": 18, "end_h": 19},
        tariff=tariff,
    )
    # 0.5h*1.5kW*2 + 1h*1.5kW*4 + 0.5h*1.5kW*10 = 15
    assert result["device_impacts"]["washer"]["scheduled_cost"] == 15
    assert result["aggregate"]["fixed_load_scheduled_cost"] == 15


def test_cooling_direction_is_physical_and_never_invents_kwh() -> None:
    lower = evaluate_candidate_impact(
        {"plan": {"setpoint": 24, "appliances": {}}},
        observable_state=_state(),
        event={},
        ordinary_plan={"setpoint": 25, "appliances": {}},
    )
    higher = evaluate_candidate_impact(
        {"plan": {"setpoint": 26, "appliances": {}}},
        observable_state=_state(),
        event={},
        ordinary_plan={"setpoint": 25, "appliances": {}},
    )
    assert lower["hvac_impact"]["expected_demand_direction"] == "higher_cooling_demand"
    assert higher["hvac_impact"]["expected_demand_direction"] == "lower_cooling_demand"
    assert lower["hvac_impact"]["energy_kwh_estimate"] is None


def test_thermal_rollout_quantifies_hvac_delta_without_selecting_plan() -> None:
    state = _state()
    state["professional_hvac_rollout"] = {
        "horizon_h": 2.5,
        "start_hod": 16.5,
        "candidate_setpoints": [
            {
                "setpoint_c": 25,
                "hvac_energy_kwh": 2.4,
                "vpp_hvac_energy_kwh": 0.8,
                "predicted_final_temp_c": 25.1,
                "comfort_violation_c": 0,
            },
            {
                "setpoint_c": 26,
                "hvac_energy_kwh": 1.7,
                "vpp_hvac_energy_kwh": 0.35,
                "predicted_final_temp_c": 25.7,
                "comfort_violation_c": 0,
            },
        ],
    }
    result = evaluate_candidate_impact(
        {"plan": {"setpoint": 26, "appliances": {}}},
        observable_state=state,
        event={"trigger_h": 18, "end_h": 19},
        ordinary_plan={"setpoint": 25, "appliances": {}},
        tariff=build_hourly_tariff_snapshot({16: 2.0, 17: 2.0, 18: 2.0}),
    )

    hvac = result["hvac_impact"]
    assert hvac["estimate_class"] == "observable_regional_thermal_rollout"
    assert hvac["energy_kwh_estimate"] == 1.7
    assert hvac["energy_delta_vs_ordinary_kwh"] == -0.7
    assert hvac["vpp_energy_delta_vs_ordinary_kwh"] == -0.45
    assert hvac["horizon_cost_delta_vs_ordinary"] == -1.4
    assert result["offer_specific_comparison"]["hvac_energy_delta_vs_ordinary_kwh"] == -0.7
    assert result["ranking_performed"] is False


def test_offer_specific_comparison_reports_zero_for_unchanged_schedule() -> None:
    ordinary = {
        "setpoint": 25,
        "appliances": {"washer_start_h": 19, "washer_skip": False},
    }
    result = evaluate_candidate_impact(
        {"plan": deepcopy(ordinary)},
        observable_state=_state(),
        event={"trigger_h": 18, "end_h": 19},
        ordinary_plan=ordinary,
        tariff=_tariff(),
    )
    comparison = result["offer_specific_comparison"]
    assert comparison["changed_path_count"] == 0
    assert comparison["candidate_minus_ordinary"]["fixed_load_scheduled_cost_delta_vs_ordinary"] == 0
    assert comparison["candidate_minus_ordinary"]["fixed_load_vpp_overlap_energy_kwh_delta_vs_ordinary"] == 0


def test_completed_service_cannot_receive_incremental_savings_credit() -> None:
    state = _state()
    state["realtime_device_state"]["current_day_service_state"] = {
        "washer": {
            "completed": True,
            "skipped": False,
            "actual_start_h": 8.0,
            "ran_during_vpp": False,
        }
    }
    ordinary = {
        "setpoint": 25,
        "appliances": {"washer_start_h": 8, "washer_skip": False},
    }
    result = evaluate_candidate_impact(
        {"plan": {"setpoint": 25, "appliances": {"washer_skip": True}}},
        observable_state=state,
        event={"trigger_h": 18, "end_h": 19},
        ordinary_plan=ordinary,
        tariff=_tariff(),
    )

    washer = result["device_impacts"]["washer"]
    assert washer["service_state_locked_before_proposal"] is True
    assert washer["observed_service_status"] == "completed"
    assert washer["task_completed"] is True
    assert washer["incremental_scheduled_energy_kwh"] == 0
    assert washer["scheduled_cost"] == 0
    comparison = result["offer_specific_comparison"]["candidate_minus_ordinary"]
    assert comparison["fixed_load_scheduled_energy_kwh_delta_vs_ordinary"] == 0
    assert comparison["fixed_load_scheduled_cost_delta_vs_ordinary"] == 0


def test_offer_specific_paths_do_not_credit_inherited_ordinary_actions() -> None:
    ordinary = {"setpoint": 25, "appliances": {"washer_start_h": 19, "washer_skip": False}}
    result = evaluate_candidate_impact(
        {"plan": {"setpoint": 26, "appliances": {"washer_start_h": 19, "washer_skip": False}}},
        observable_state=_state(),
        event={"trigger_h": 18, "end_h": 19},
        ordinary_plan=ordinary,
    )
    assert result["offer_specific_changed_paths"] == ["/setpoint"]


def test_ev_feasibility_uses_only_observable_requirement_and_capacity() -> None:
    result = evaluate_candidate_impact(
        {"plan": {"appliances": {
            "ev_mode": "smart",
            "ev_charge_start_h": 0,
            "ev_charge_end_h": 1,
        }}},
        observable_state=_state(),
        event={"trigger_h": 18, "end_h": 19},
    )
    ev = result["device_impacts"]["ev"]
    assert ev["deliverable_energy_upper_bound_kwh"] == 6.3
    assert ev["required_battery_energy_kwh"] == 12
    assert ev["energy_requirement_feasible"] is False


def test_portfolio_preserves_candidates_and_never_selects_or_scores() -> None:
    candidates = [
        {"candidate_id": "warmer", "plan": {"setpoint": 27, "appliances": {}}},
        {"candidate_id": "cooler", "plan": {"setpoint": 25, "appliances": {}}},
    ]
    before = deepcopy(candidates)
    result = evaluate_portfolio_impacts(
        candidates,
        observable_state=_state(),
        event={"trigger_h": 18, "end_h": 19},
        ordinary_plan={"setpoint": 26, "appliances": {}},
    )
    assert candidates == before
    assert [card["candidate_id"] for card in result["candidate_impacts"]] == ["warmer", "cooler"]
    assert result["selected_candidate_id"] is None
    assert result["ranking_performed"] is False
    assert all(card["aggregate"]["scalar_utility_score"] is None for card in result["candidate_impacts"])
    assert json.loads(json.dumps(result))["schema_version"] == ENERGY_IMPACT_SCHEMA_VERSION


def test_method_and_model_metadata_do_not_enter_evidence_card() -> None:
    first = evaluate_candidate_impact(
        {"candidate_id": "same", "method": "one", "provider": "vendor-a", "plan": {"setpoint": 26, "appliances": {}}},
        observable_state=_state(),
        event={},
        ordinary_plan={"setpoint": 25, "appliances": {}},
    )
    second = evaluate_candidate_impact(
        {"candidate_id": "same", "method": "two", "provider": "vendor-b", "plan": {"setpoint": 26, "appliances": {}}},
        observable_state=_state(),
        event={},
        ordinary_plan={"setpoint": 25, "appliances": {}},
    )
    assert first == second
    serialized = json.dumps(first)
    assert "vendor-a" not in serialized and "vendor-b" not in serialized
