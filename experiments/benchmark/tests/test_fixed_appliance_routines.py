from energybridge.simulation.appliance_sim import WaterHeater
from experiments.benchmark.family_runner import _fixed_appliance_routine_actions


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


def test_fixed_appliance_routines_fill_posthoc_diagnostic_context():
    actions = _fixed_appliance_routine_actions(
        {
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
    )

    assert actions["washer_start_h"] == 19.0
    assert actions["washer_skip"] is False
    assert actions["water_heater_preheat"] is True
    assert actions["water_heater_preheat_start_h"] == 18.0
    assert actions["water_heater_preheat_end_h"] == 20.0
