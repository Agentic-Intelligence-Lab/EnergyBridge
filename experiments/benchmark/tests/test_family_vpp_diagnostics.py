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
        "appliance_summary": {
            "washer": {"present": True, "ran_during_vpp": False},
            "water_heater": {"present": True, "ran_during_vpp": False},
            "ev": {"present": False, "ran_during_vpp": False},
        },
    }

    _update_event_reference_shed_diagnostics(event)
    _annotate_event_demand_achievement(event)

    assert event["reference_pbase_minus_actual_kwh"] == pytest.approx(7.5232)
    assert event["capacity_limited_reference_shed_kwh"] == pytest.approx(0.5453)
    assert event["actual_shed_kwh"] is None
    assert event["target_mode"] == "non_ac_appliance_avoidance"
    assert event["target_achieved"] is True
    assert event["demand_achievement_ratio"] == 1.0


def test_vpp_success_depends_on_non_ac_appliance_avoidance_not_shed_target() -> None:
    event = {
        "demand_target_shed_kwh": 99.0,
        "actual_shed_kwh": 0.0,
        "actual_kwh": 50.0,
        "appliance_summary": {
            "washer": {"present": True, "ran_during_vpp": False},
            "dishwasher": {"present": True, "ran_during_vpp": False},
            "dryer": {"present": True, "ran_during_vpp": False},
            "water_heater": {"present": True, "ran_during_vpp": False},
            "ev": {"present": True, "ran_during_vpp": False},
        },
    }

    _annotate_event_demand_achievement(event)

    assert event["target_achieved"] is True
    assert event["vpp_non_ac_appliances_during_event"] == []


def test_vpp_fails_when_non_ac_appliance_runs_during_window() -> None:
    event = {
        "demand_target_shed_kwh": 1.0,
        "actual_shed_kwh": 10.0,
        "appliance_summary": {
            "washer": {"present": True, "ran_during_vpp": False},
            "water_heater": {"present": True, "ran_during_vpp": True},
        },
    }

    _annotate_event_demand_achievement(event)

    assert event["target_achieved"] is False
    assert event["demand_achievement_ratio"] == 0.0
    assert event["vpp_non_ac_appliances_during_event"] == ["water_heater"]


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
