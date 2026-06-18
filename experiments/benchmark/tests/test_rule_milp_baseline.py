from __future__ import annotations

from datetime import datetime

from experiments.benchmark.baselines.rule_milp import plan_rule_milp_action


class _FakePriceProfile:
    source = "fake"

    def price_at(self, local_time: datetime) -> float:
        cheap_hours = {20, 21, 22, 23, 0, 1}
        return 0.01 if local_time.hour in cheap_hours else 0.50


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
    assert appliances["washer_start_h"] == 20.0
    assert appliances["water_heater_preheat"] is True
    assert appliances["water_heater_preheat_start_h"] != 18.0
    assert 22.0 <= action["setpoint"] <= 28.0
    assert action["objective_terms"]["version"] == "rule_milp_cost_min_v1"
    assert action["objective_terms"]["diagnostics"]["solver"]["solver"] in {
        "pulp_cbc_milp",
        "exact_enumeration_fallback",
    }


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
