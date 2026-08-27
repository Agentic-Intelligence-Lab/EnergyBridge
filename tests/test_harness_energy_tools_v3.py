from __future__ import annotations

from copy import deepcopy
import json

from energybridge.harness.energy_tools_v3 import (
    DECISION_EPOCH_SNAPSHOT_VERSION,
    ENERGY_IMPACT_SCHEMA_VERSION,
    FLEXIBLE_LOAD_OPPORTUNITY_VERSION,
    build_flexible_load_opportunity_snapshot,
    build_decision_epoch_snapshot,
    build_hourly_tariff_snapshot,
    compact_flexible_load_opportunities_for_prompt,
    compact_portfolio_impacts_for_review,
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


def test_decision_epochs_expose_state_changes_without_choosing_replan_time() -> None:
    state = _state()
    state["time"] = {"simulation_hour": 16.5, "hour_of_day": 16.5}
    state["device_capabilities"]["washer"].update({
        "earliest_h": 8.0,
        "latest_h": 22.0,
        "duration_h": 2.0,
    })
    state["device_capabilities"]["water_heater"]["bath_required_h"] = 21.0
    state["device_capabilities"]["ev"].update({
        "arrival_h": 18.5,
        "departure_h": 7.5,
    })
    state["hourly_tariff"] = build_hourly_tariff_snapshot({
        hour: (1.0 if hour < 18 else 2.0)
        for hour in range(24)
    })

    snapshot = build_decision_epoch_snapshot(
        observable_state=state,
        event={"trigger_h": 18.0, "end_h": 19.0},
        ordinary_plan={
            "appliances": {"washer_start_h": 19.0, "washer_skip": False}
        },
    )

    assert snapshot["schema_version"] == DECISION_EPOCH_SNAPSHOT_VERSION
    assert snapshot["selection_performed"] is False
    assert snapshot["ranking_performed"] is False
    rows = {
        row[0]: row[2]
        for row in snapshot["epoch_rows"]
    }
    assert {signal["kind"] for signal in rows[18.0]} == {
        "tariff_interval_changes",
        "vpp_event_starts",
    }
    assert any(
        signal["kind"] == "device_becomes_available"
        and signal["device"] == "ev"
        for signal in rows[18.5]
    )
    assert {signal["kind"] for signal in rows[19.0]} >= {
        "ordinary_service_starts",
        "vpp_event_ends",
    }
    assert any(
        signal["kind"] == "ordinary_service_finishes"
        and signal["device"] == "washer"
        for signal in rows[21.0]
    )
    assert any(
        signal["kind"] == "service_deadline"
        and signal["device"] == "ev"
        for signal in rows[31.5]
    )
    serialized = json.dumps(snapshot)
    assert "selected_epoch" not in serialized
    assert "recommended" not in serialized


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


def test_flexible_load_opportunities_expose_cost_overlap_and_routine_tradeoffs_without_selecting() -> None:
    state = _state()
    state["device_capabilities"]["washer"]["preferred_h"] = 19
    tariff = build_hourly_tariff_snapshot({hour: (0.4 if hour in {8, 9} else 1.6) for hour in range(24)}, unit="normalized/kWh")
    ordinary = {
        "setpoint": 26,
        "appliances": {"washer_start_h": 19, "washer_skip": False},
    }

    result = build_flexible_load_opportunity_snapshot(
        observable_state=state,
        event={"trigger_h": 18, "end_h": 19},
        ordinary_plan=ordinary,
        tariff=tariff,
    )

    washer = result["devices"]["washer"]
    by_start = {item["start_hod"]: item for item in washer["options"]}
    assert result["schema_version"] == FLEXIBLE_LOAD_OPPORTUNITY_VERSION
    assert result["selection_performed"] is False
    assert result["ranking_performed"] is False
    assert by_start[8.0]["scheduled_cost"] == 1.2
    assert by_start[8.0]["cost_delta_vs_ordinary"] == -3.6
    assert by_start[8.0]["routine_shift_h"] == 11
    assert by_start[18.0]["event_overlap_h"] == 1
    assert by_start[19.0]["event_overlap_h"] == 0
    assert washer["dimension_extrema"] == {
        "minimum_scheduled_cost": 1.2,
        "minimum_event_overlap_h": 0.0,
        "minimum_routine_shift_h": 0.0,
        "interpretation": "extrema are separate dimensions, not a selected or recommended start",
    }


def test_flexible_load_opportunity_snapshot_preserves_distinct_base_model_choices() -> None:
    state = _state()
    state["device_capabilities"]["washer"]["preferred_h"] = 19
    opportunity = build_flexible_load_opportunity_snapshot(
        observable_state=state,
        event={"trigger_h": 18, "end_h": 19},
        ordinary_plan={"appliances": {"washer_start_h": 19, "washer_skip": False}},
        tariff=build_hourly_tariff_snapshot({hour: 1 + hour / 10 for hour in range(24)}),
    )

    starts = {item["start_hod"] for item in opportunity["devices"]["washer"]["options"]}
    assert 8.0 in starts and 19.0 in starts
    assert opportunity["devices"]["washer"]["selection_performed"] is False


def test_flexible_load_prompt_capsule_is_lossless_columnar_and_smaller() -> None:
    state = _state()
    state["device_capabilities"]["washer"]["preferred_h"] = 19
    snapshot = build_flexible_load_opportunity_snapshot(
        observable_state=state,
        event={"trigger_h": 18, "end_h": 19},
        ordinary_plan={"appliances": {"washer_start_h": 19, "washer_skip": False}},
        tariff=build_hourly_tariff_snapshot(
            {hour: 1 + hour / 10 for hour in range(24)}
        ),
    )

    capsule = compact_flexible_load_opportunities_for_prompt(snapshot)
    washer = capsule["devices"]["washer"]
    start_index = washer["option_columns"].index("start_hod")
    compact_starts = [row[start_index] for row in washer["option_rows"]]
    original_starts = [row["start_hod"] for row in snapshot["devices"]["washer"]["options"]]

    assert compact_starts == original_starts
    assert washer["option_count"] == len(original_starts)
    assert capsule["selection_performed"] is False
    assert capsule["ranking_performed"] is False
    assert len(json.dumps(capsule, sort_keys=True)) < 0.7 * len(
        json.dumps(snapshot, sort_keys=True)
    )


def test_flexible_load_opportunities_exclude_starts_before_decision_clock() -> None:
    state = _state()
    state["time"] = {"hour_of_day": 16.5}
    state["realtime_device_state"] = {
        "current_day_service_state": {
            "washer": {"completed": False, "scheduled_abs_h": 19.0},
        },
        "current_power_kw": {"washer": 0.0},
    }
    opportunity = build_flexible_load_opportunity_snapshot(
        observable_state=state,
        event={"trigger_h": 18, "end_h": 19},
        ordinary_plan={"appliances": {"washer_start_h": 19, "washer_skip": False}},
        tariff=_tariff(),
    )

    washer = opportunity["devices"]["washer"]
    starts = {item["start_hod"] for item in washer["options"]}
    assert washer["decision_hour_hod"] == 16.5
    assert washer["excluded_past_start_count"] > 0
    assert starts
    assert min(starts) >= 17.0
    assert 15.0 not in starts


def test_flexible_load_opportunities_do_not_reschedule_completed_service() -> None:
    state = _state()
    state["time"] = {"hour_of_day": 16.5}
    state["realtime_device_state"] = {
        "current_day_service_state": {
            "washer": {"completed": True, "scheduled_abs_h": 8.0},
        },
        "current_power_kw": {"washer": 0.0},
    }
    opportunity = build_flexible_load_opportunity_snapshot(
        observable_state=state,
        event={"trigger_h": 18, "end_h": 19},
        ordinary_plan={"appliances": {"washer_start_h": 19, "washer_skip": False}},
        tariff=_tariff(),
    )

    washer = opportunity["devices"]["washer"]
    assert washer["status"] == "service_already_completed"
    assert washer["service_state_locked_before_decision"] is True
    assert washer["options"] == []


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
    assert comparison["offer_materiality"] == "no_observable_physical_change"
    assert comparison["supported_benefit_claims"] == []
    assert comparison["benefit_claim_status"] == "no_offer_specific_claim_supported"
    assert result["findings"][-1]["code"] == "no_observable_offer_change"
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


def test_required_service_skip_is_reported_as_hard_risk() -> None:
    state = _state()
    state["device_capabilities"]["washer"]["service_required_today"] = True

    result = evaluate_candidate_impact(
        {"plan": {"setpoint": 25, "appliances": {"washer_skip": True}}},
        observable_state=state,
        event={"trigger_h": 18, "end_h": 19},
        ordinary_plan={
            "setpoint": 25,
            "appliances": {"washer_start_h": 19, "washer_skip": False},
        },
    )

    assert result["device_impacts"]["washer"]["service_required_today"] is True
    assert {
        (item["code"], item["device"], item["severity"])
        for item in result["findings"]
    } == {("required_daily_service_cancelled", "washer", "hard_service_risk")}


def test_offer_specific_paths_do_not_credit_inherited_ordinary_actions() -> None:
    ordinary = {"setpoint": 25, "appliances": {"washer_start_h": 19, "washer_skip": False}}
    result = evaluate_candidate_impact(
        {"plan": {"setpoint": 26, "appliances": {"washer_start_h": 19, "washer_skip": False}}},
        observable_state=_state(),
        event={"trigger_h": 18, "end_h": 19},
        ordinary_plan=ordinary,
    )
    assert result["offer_specific_changed_paths"] == ["/setpoint"]


def test_exact_fixed_load_tariff_delta_becomes_a_supported_offer_claim() -> None:
    state = _state()
    tariff = build_hourly_tariff_snapshot({hour: (0.4 if hour in {8, 9} else 1.6) for hour in range(24)}, unit="normalized/kWh")
    result = evaluate_candidate_impact(
        {"plan": {"setpoint": 25, "appliances": {"washer_start_h": 8, "washer_skip": False}}},
        observable_state=state,
        event={"trigger_h": 18, "end_h": 19},
        ordinary_plan={"setpoint": 25, "appliances": {"washer_start_h": 19, "washer_skip": False}},
        tariff=tariff,
    )

    comparison = result["offer_specific_comparison"]
    assert comparison["offer_materiality"] == "observable_physical_change"
    assert comparison["supported_benefit_claims"] == [{
        "kind": "normalized_fixed_load_cost_reduction",
        "amount": 3.6,
        "unit": "normalized/kWh",
        "estimate_class": "exact_fixed_power_tariff_integration",
        "evidence_paths": [
            "/offer_specific_comparison/candidate_minus_ordinary/fixed_load_scheduled_cost_delta_vs_ordinary"
        ],
    }]


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


def test_impact_review_capsule_keeps_every_candidate_and_checked_tradeoff() -> None:
    state = _state()
    state["professional_hvac_rollout"] = {
        "horizon_h": 2,
        "candidate_setpoints": [
            {
                "setpoint_c": 25,
                "hvac_energy_kwh": 2.4,
                "vpp_hvac_energy_kwh": 0.8,
                "predicted_final_temp_c": 25.1,
                "comfort_violation_c": 0,
            },
            {
                "setpoint_c": 27,
                "hvac_energy_kwh": 1.4,
                "vpp_hvac_energy_kwh": 0.25,
                "predicted_final_temp_c": 26.4,
                "comfort_violation_c": 0.4,
            },
        ],
    }
    full = evaluate_portfolio_impacts(
        [
            {
                "candidate_id": "service_first",
                "plan": {
                    "setpoint": 25,
                    "appliances": {"washer_start_h": 18, "washer_skip": False},
                },
            },
            {
                "candidate_id": "event_first",
                "plan": {
                    "setpoint": 27,
                    "appliances": {"washer_start_h": 19, "washer_skip": False},
                },
            },
        ],
        observable_state=state,
        event={"trigger_h": 18, "end_h": 19},
        ordinary_plan={
            "setpoint": 25,
            "appliances": {"washer_start_h": 18, "washer_skip": False},
        },
        tariff=_tariff(),
    )

    capsule = compact_portfolio_impacts_for_review(full)

    assert capsule["schema_version"] == "energybridge.impact_review_capsule.v1"
    assert capsule["source_schema_version"] == ENERGY_IMPACT_SCHEMA_VERSION
    assert capsule["candidate_count"] == 2
    assert [item["candidate_id"] for item in capsule["candidate_impacts"]] == [
        "service_first",
        "event_first",
    ]
    assert capsule["ranking_performed"] is False
    assert capsule["selected_candidate_id"] is None
    first, second = capsule["candidate_impacts"]
    device_columns = capsule["device_impact_columns"]
    hvac_columns = capsule["hvac_impact_columns"]
    first_devices = {
        row[0]: dict(zip(device_columns[1:], row[1:]))
        for row in first["device_impact_rows"]
    }
    second_devices = {
        row[0]: dict(zip(device_columns[1:], row[1:]))
        for row in second["device_impact_rows"]
    }
    second_hvac = dict(zip(hvac_columns, second["hvac_impact_values"]))
    assert first_devices["washer"]["vpp_overlap_h"] == 1
    assert second_devices["washer"]["vpp_overlap_h"] == 0
    assert second_hvac["energy_delta_vs_ordinary_kwh"] == -1
    assert second_hvac["comfort_violation_c"] == 0.4
    assert second["offer_specific_comparison"]["changed_path_count"] > 0
    compact_text = json.dumps(capsule, sort_keys=True)
    full_text = json.dumps(full, sort_keys=True)
    assert "evidence_paths" not in compact_text
    assert len(compact_text) < 0.75 * len(full_text)


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
