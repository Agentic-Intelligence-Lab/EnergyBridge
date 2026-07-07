from __future__ import annotations

from datetime import datetime

from experiments.benchmark.baselines.rule_milp import plan_rule_milp_action


class _FakePriceProfile:
    source = "fake"

    def price_at(self, local_time: datetime) -> float:
        cheap_hours = {20, 21, 22, 23, 0, 1}
        return 0.01 if local_time.hour in cheap_hours else 0.50


class _OvernightOnlyCheapProfile:
    source = "fake_overnight"

    def price_at(self, local_time: datetime) -> float:
        return 0.01 if local_time.hour in {0, 1, 2, 3} else 0.50


class _VppOnlyCheapProfile:
    source = "fake_vpp_only"

    def price_at(self, local_time: datetime) -> float:
        return 0.01 if local_time.hour == 18 else 0.50


def _state() -> dict:
    return {
        "sim_h": 0.1,
        "hod": 0.1,
        "day_idx": 0,
        "temp_c": 26.0,
        "current_setpoint_c": 25.5,
        "vpp_event": {"id": "vpp1", "trigger_h": 18.0, "end_h": 19.0, "day": 1},
        "vpp_active": False,
        "appliance_config": {
            "ac": {
                "present": True,
                "setpoint_preferred_min_c": 24.0,
                "setpoint_preferred_max_c": 26.0,
                "temp_tolerance_c": 1.0,
            },
            "washer": {
                "present": True,
                "earliest_h": 8.0,
                "latest_h": 22.0,
                "preferred_h": 18.0,
                "duration_h": 1.0,
                "power_kw": 1.5,
                "shiftable": True,
                "dr_adjustable": True,
            },
            "dishwasher": {"present": False},
            "dryer": {"present": False},
            "water_heater": {
                "present": True,
                "rated_kw": 2.0,
                "bath_required_h": 21.0,
                "dr_adjustable": True,
                "pre_heat_window_start_h": 15.0,
                "pre_heat_window_end_h": 18.0,
            },
            "ev": {"present": False},
        },
        "appliance_status_lines": [],
        "appliance_results": {},
        "history": {},
    }


def test_rule_milp_uses_low_price_window_and_avoids_vpp() -> None:
    action = plan_rule_milp_action(
        state=_state(),
        price_profile=_FakePriceProfile(),
        run_start_date=datetime(2025, 6, 1).date(),
    )

    appliances = action["appliances"]
    assert appliances["washer_skip"] is False
    assert appliances["washer_start_h"] in {20.0, 20.5, 21.0}
    assert appliances["water_heater_preheat"] is True
    assert appliances["water_heater_preheat_start_h"] != 18.0
    assert 22.0 <= action["setpoint"] <= 28.0
    assert action["objective_terms"]["version"] == "rule_milp_dynamic_cost_min_v2"
    assert action["objective_terms"]["diagnostics"]["hvac_setpoint"]["status"] == "regional_dynamic_model"
    assert action["objective_terms"]["diagnostics"]["solver"]["solver"] in {
        "pulp_cbc_milp",
        "exact_enumeration_fallback",
    }


def test_rule_milp_uses_berlin_dynamic_model_for_germany() -> None:
    state = _state()
    state["city"] = "Germany"
    state["outdoor_temp_c"] = 22.0

    action = plan_rule_milp_action(state=state)

    hvac = action["objective_terms"]["diagnostics"]["hvac_setpoint"]
    assert hvac["region"] == "berlin"
    assert hvac["cost_min_dynamic_setpoint_c"] == action["setpoint"]


def test_rule_milp_strictly_filters_vpp_candidates_even_when_vpp_is_cheapest() -> None:
    state = _state()
    state["appliance_config"]["ev"] = {
        "present": True,
        "charger_kw": 7.0,
        "efficiency": 0.92,
        "capacity_kwh": 60.0,
        "target_soc": 0.8,
        "daily_drive_kwh": 8.0,
        "arrival_h": 18.0,
        "departure_h": 23.0,
        "dr_adjustable": True,
    }

    action = plan_rule_milp_action(
        state=state,
        price_profile=_VppOnlyCheapProfile(),
        run_start_date=datetime(2025, 6, 1).date(),
    )

    appliances = action["appliances"]
    washer_start = appliances["washer_start_h"]
    assert max(washer_start, 18.0) >= min(washer_start + 1.0, 19.0)
    assert appliances["ev_charge_start_h"] >= 19.0
    for group in action["objective_terms"]["diagnostics"]["candidate_groups"].values():
        assert all(candidate["vpp_penalty"] == 0.0 for candidate in group)


def test_rule_milp_outputs_all_known_action_keys() -> None:
    action = plan_rule_milp_action(state=_state())

    assert set(action["appliances"]) == {
        "washer_start_h",
        "washer_skip",
        "dishwasher_start_h",
        "dishwasher_skip",
        "dryer_start_h",
        "dryer_skip",
        "water_heater_preheat_start_h",
        "water_heater_preheat_end_h",
        "water_heater_preheat_temp_c",
        "water_heater_preheat",
        "ev_mode",
        "ev_charge_start_h",
        "ev_charge_end_h",
    }


def test_rule_milp_oracle_moves_fixed_water_heater_away_from_vpp() -> None:
    state = _state()
    state["appliance_config"]["washer"]["present"] = False
    state["appliance_config"]["water_heater"].update(
        {
            "bath_required_h": 21.0,
            "dr_adjustable": False,
            "pre_heat_window_start_h": 18.0,
            "pre_heat_window_end_h": 20.0,
        }
    )

    action = plan_rule_milp_action(
        state=state,
        price_profile=_FakePriceProfile(),
        run_start_date=datetime(2025, 6, 1).date(),
    )

    appliances = action["appliances"]
    assert appliances["water_heater_preheat"] is True
    start = appliances["water_heater_preheat_start_h"]
    end = appliances["water_heater_preheat_end_h"]
    assert max(start, 18.0) >= min(end, 19.0)
    assert end <= 21.0


def test_rule_milp_ev_command_uses_charge_window_without_required_mode() -> None:
    state = _state()
    state["appliance_config"]["ev"] = {
        "present": True,
        "charger_kw": 7.0,
        "efficiency": 0.92,
        "capacity_kwh": 60.0,
        "target_soc": 0.8,
        "daily_drive_kwh": 8.0,
        "arrival_h": 18.0,
        "departure_h": 7.5,
        "dr_adjustable": True,
    }

    action = plan_rule_milp_action(
        state=state,
        price_profile=_FakePriceProfile(),
        run_start_date=datetime(2025, 6, 1).date(),
    )

    assert action["appliances"]["ev_mode"] is None
    assert action["appliances"]["ev_charge_start_h"] is not None
    assert action["appliances"]["ev_charge_end_h"] is not None


def test_rule_milp_ev_avoids_unexecutable_next_morning_only_window() -> None:
    state = _state()
    state["sim_days"] = 7
    state["run_end_abs_h"] = 168.0
    state["appliance_config"]["washer"]["present"] = False
    state["appliance_config"]["water_heater"]["present"] = False
    state["appliance_config"]["ev"] = {
        "present": True,
        "charger_kw": 7.4,
        "efficiency": 0.92,
        "capacity_kwh": 60.0,
        "target_soc": 0.8,
        "daily_drive_kwh": 20.0,
        "arrival_h": 18.5,
        "departure_h": 7.5,
        "dr_adjustable": True,
    }

    action = plan_rule_milp_action(
        state=state,
        price_profile=_OvernightOnlyCheapProfile(),
        run_start_date=datetime(2025, 6, 1).date(),
    )

    appliances = action["appliances"]
    ev_start = appliances["ev_charge_start_h"]
    ev_end = appliances["ev_charge_end_h"]
    ev_abs_end = ev_end if ev_end > ev_start else ev_end + 24.0
    assert ev_start >= 18.5
    assert ev_abs_end <= 24.0
    assert ev_abs_end - ev_start >= 3.0


def test_rule_milp_shiftable_overnight_window_completes_before_midnight() -> None:
    state = _state()
    state["sim_days"] = 7
    state["run_end_abs_h"] = 168.0
    state["appliance_config"]["washer"]["present"] = False
    state["appliance_config"]["water_heater"]["present"] = False
    state["appliance_config"]["dishwasher"] = {
        "present": True,
        "earliest_h": 19.0,
        "latest_h": 7.0,
        "preferred_h": 1.5,
        "duration_h": 1.5,
        "power_kw": 1.2,
        "shiftable": True,
        "dr_adjustable": True,
    }

    action = plan_rule_milp_action(
        state=state,
        price_profile=_OvernightOnlyCheapProfile(),
        run_start_date=datetime(2025, 6, 1).date(),
    )

    start_h = action["appliances"]["dishwasher_start_h"]
    assert start_h >= 19.0
    assert start_h + 1.5 <= 23.5


def test_rule_milp_keeps_last_day_overnight_services_inside_run() -> None:
    state = _state()
    state["sim_days"] = 1
    state["run_end_abs_h"] = 24.0
    state["appliance_config"]["washer"]["present"] = False
    state["appliance_config"]["dishwasher"] = {
        "present": True,
        "earliest_h": 19.0,
        "latest_h": 7.0,
        "preferred_h": 21.5,
        "duration_h": 1.5,
        "power_kw": 1.2,
        "shiftable": True,
        "dr_adjustable": True,
    }
    state["appliance_config"]["ev"] = {
        "present": True,
        "charger_kw": 7.4,
        "efficiency": 0.92,
        "capacity_kwh": 60.0,
        "target_soc": 0.8,
        "daily_drive_kwh": 20.0,
        "arrival_h": 18.5,
        "departure_h": 7.5,
        "dr_adjustable": True,
    }

    action = plan_rule_milp_action(
        state=state,
        price_profile=_FakePriceProfile(),
        run_start_date=datetime(2025, 6, 1).date(),
    )
    appliances = action["appliances"]

    assert appliances["dishwasher_start_h"] + 1.5 <= 23.5
    assert appliances["ev_charge_start_h"] >= 18.5
    ev_start = appliances["ev_charge_start_h"]
    ev_end = appliances["ev_charge_end_h"]
    ev_abs_end = ev_end if ev_end > ev_start else ev_end + 24.0
    assert ev_abs_end <= 23.5
