from __future__ import annotations

import pytest

from experiments.benchmark.family_runner import (
    _annotate_event_demand_achievement,
    _schedule_no_dr_routine_appliances,
    _update_event_reference_shed_diagnostics,
)
from energybridge.simulation.appliance_sim import ApplianceSuite


def test_reference_pbase_minus_actual_is_diagnostic_not_actual_shed() -> None:
    event = {
        "trigger_h": 18.0,
        "end_h": 19.0,
        "demand_baseline_kwh": 8.853,
        "demand_target_shed_kwh": 1.0,
        "actual_kwh": 1.3298,
        "capacity_window_summary": {"recommended_bid_energy_kwh": 0.5453},
    }

    _update_event_reference_shed_diagnostics(event)
    _annotate_event_demand_achievement(event)

    assert event["reference_pbase_minus_actual_kwh"] == pytest.approx(7.5232)
    assert event["capacity_limited_reference_shed_kwh"] == pytest.approx(0.5453)
    assert event["actual_shed_kwh"] is None
    assert event["target_mode"] == "shed_requires_counterfactual"
    assert event["target_achieved"] is None
    assert event["demand_achievement_ratio"] is None


def test_no_dr_routine_schedules_shiftables_without_controller_policy() -> None:
    appliance_config = {
        "washer": {"present": True, "earliest_h": 8.0, "latest_h": 20.0, "duration_h": 2.0},
        "dishwasher": {"present": True, "earliest_h": 18.0, "latest_h": 23.0, "duration_h": 1.0},
        "dryer": {"present": False},
        "water_heater": {"present": True},
        "ev": {"present": True, "arrival_h": 18.0, "departure_h": 7.0},
    }
    suite = ApplianceSuite(appliance_config, sim_days=1, vpp_events=[], explicit_only=True)

    routine = _schedule_no_dr_routine_appliances(suite, appliance_config, 1, "persona|no_dr")

    actions = routine[0]["actions"]
    assert "washer_start_h" in actions
    assert "dishwasher_start_h" in actions
    assert "dryer_start_h" not in actions
    assert actions["water_heater_preheat_start_h"] == 17.0
    assert actions["water_heater_preheat_end_h"] == 21.0
    assert actions["ev_charge_start_h"] == 18.0
    assert actions["ev_charge_end_h"] == 7.0
    assert suite.all_results()["washer"][0]["scheduled_abs_h"] is not None
    assert suite.all_results()["dishwasher"][0]["scheduled_abs_h"] is not None
