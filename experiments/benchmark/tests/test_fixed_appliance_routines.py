from energybridge.simulation.appliance_sim import ShiftableAppliance, WaterHeater
from experiments.benchmark.family_runner import (
    _explicit_appliance_requirement_text,
    _fixed_appliance_constraint_text,
    _missing_explicit_appliance_actions,
    _present_agent_controlled_appliances,
)


def test_fixed_water_heater_runs_configured_routine_but_rejects_agent_reschedule():
    heater = WaterHeater(
        {
            "present": True,
            "dr_adjustable": False,
            "rated_kw": 2.0,
            "pre_heat_window_start_h": 16.0,
            "pre_heat_window_end_h": 19.0,
        },
        sim_days=1,
    )

    assert not heater.set_preheat_schedule(0, start_h=14.0, end_h=17.0)
    assert heater.request_preheat(0)
    assert heater.step(16.0, 1.0, vpp_active=False) == 2.0
    assert heater.step(19.0, 1.0, vpp_active=False) == 0.0


def test_explicit_only_accepts_policy_commands_for_routine_locked_devices():
    washer = ShiftableAppliance(
        "washer",
        {
            "present": True,
            "shiftable": False,
            "dr_adjustable": False,
            "earliest_h": 8.0,
            "latest_h": 22.0,
            "preferred_h": 19.0,
            "duration_h": 2.0,
            "power_kw": 1.5,
        },
        sim_days=1,
        explicit_only=True,
    )
    heater = WaterHeater(
        {
            "present": True,
            "dr_adjustable": False,
            "rated_kw": 2.0,
            "pre_heat_window_start_h": 16.0,
            "pre_heat_window_end_h": 19.0,
        },
        sim_days=1,
        explicit_only=True,
    )

    assert washer.shift(0, 19.0)
    assert heater.set_preheat_schedule(0, start_h=16.0, end_h=19.0, temp_c=65.0)
    assert washer.step(19.0, 1.0, vpp_active=False) == 1.5
    assert heater.step(16.0, 1.0, vpp_active=False) == 2.0


def test_explicit_only_still_has_no_hidden_default_for_routine_locked_devices():
    washer = ShiftableAppliance(
        "washer",
        {
            "present": True,
            "shiftable": False,
            "dr_adjustable": False,
            "preferred_h": 19.0,
        },
        sim_days=1,
        explicit_only=True,
    )
    heater = WaterHeater(
        {
            "present": True,
            "dr_adjustable": False,
            "pre_heat_window_start_h": 16.0,
            "pre_heat_window_end_h": 19.0,
        },
        sim_days=1,
        explicit_only=True,
    )

    assert washer.step(19.0, 1.0, vpp_active=False) == 0.0
    assert heater.step(16.0, 1.0, vpp_active=False) == 0.0


def test_overnight_shiftable_finishes_across_calendar_boundary():
    dishwasher = ShiftableAppliance(
        "dishwasher",
        {
            "present": True,
            "earliest_h": 19.0,
            "latest_h": 7.0,
            "duration_h": 1.5,
            "power_kw": 1.2,
        },
        sim_days=2,
        explicit_only=True,
    )

    assert dishwasher.shift(0, 23.5)
    for quarter_hour in range(94, 102):
        dishwasher.step(quarter_hour / 4.0, 0.25, vpp_active=False)

    assert dishwasher.day_result(0)["completed"] is True


def test_routine_locked_appliances_are_required_explicit_policy_outputs():
    appliance_config = {
        "washer": {
            "present": True,
            "shiftable": False,
            "dr_adjustable": False,
            "preferred_h": 19.0,
        },
        "water_heater": {
            "present": True,
            "dr_adjustable": False,
            "pre_heat_window_start_h": 18.0,
            "pre_heat_window_end_h": 20.0,
        },
    }

    assert _present_agent_controlled_appliances(appliance_config) == ["washer", "water_heater"]
    missing = _missing_explicit_appliance_actions({}, appliance_config)
    assert "washer_start_h" in missing
    assert "washer_skip" in missing
    assert "water_heater_preheat_start_h" in missing
    assert "water_heater_preheat_end_h" in missing
    assert "water_heater_preheat_temp_c" in missing
    assert "water_heater_preheat" in missing

    fixed_text = _fixed_appliance_constraint_text(appliance_config)
    requirement_text = _explicit_appliance_requirement_text(appliance_config)
    assert "routine-preserving commands" in fixed_text
    assert "fixed or non-DR-adjustable routine appliances" in requirement_text
