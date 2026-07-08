from __future__ import annotations

import inspect
import math

from experiments.benchmark.family_runner import (
    _apply_appliance_actions,
    _compute_posthoc_decision_objective,
    _filter_vpp_event_replan_actions,
    _missing_explicit_appliance_actions,
)
from experiments.benchmark.baselines.mpc import plan_mpc_action
from energybridge.simulation.appliance_sim import ApplianceSuite


def _sample_state() -> dict:
    return {
        "sim_h": 18.0,
        "hod": 18.0,
        "day_idx": 0,
        "temp_c": 26.2,
        "outdoor_temp_c": 31.0,
        "current_setpoint_c": 26.0,
        "vpp_event": {"id": "vpp1", "trigger_h": 18.0, "end_h": 19.0, "day": 1},
        "vpp_active": True,
        "vpp_target_kwh": 2.0,
        "appliance_config": {
            "ac": {
                "present": True,
                "setpoint_preferred_min_c": 24.0,
                "setpoint_preferred_max_c": 26.0,
                "temp_tolerance_c": 1.5,
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
            "water_heater": {"present": False},
            "ev": {"present": False},
        },
        "appliance_status_lines": [],
        "appliance_results": {},
        "appliance_vpp_summary": {},
        "history": {},
    }


class _FakeSuite:
    def __init__(self) -> None:
        self.skipped = []
        self.shifted = []

    def skip_appliance(self, name: str, day_idx: int) -> bool:
        self.skipped.append((name, day_idx))
        return True

    def shift_appliance(self, name: str, day_idx: int, new_abs_h: float) -> bool:
        self.shifted.append((name, day_idx, new_abs_h))
        return True


class _FakeLoop:
    sp = 26.0
    vpp_event_log = []
    appliance_suite = None


def test_agent_none_start_without_explicit_skip_is_noop() -> None:
    suite = _FakeSuite()

    _apply_appliance_actions(
        suite,
        {"washer_start_h": None, "washer_skip": None},
        sim_h=18.0,
    )

    assert suite.skipped == []
    assert suite.shifted == []


def test_agent_explicit_skip_counts_as_skip_command() -> None:
    suite = _FakeSuite()

    _apply_appliance_actions(
        suite,
        {"washer_start_h": None, "washer_skip": True},
        sim_h=18.0,
    )

    assert suite.skipped == [("washer", 0)]
    assert suite.shifted == []


def test_agent_posthoc_objective_computes_without_mutating_action() -> None:
    loop = _FakeLoop()
    action = {
        "setpoint": 26.5,
        "next_check_hour": None,
        "appliance_actions": {"washer_start_h": None},
    }
    before = dict(action["appliance_actions"])

    terms = _compute_posthoc_decision_objective(
        loop,
        action_result=action,
        sim_h=18.0,
        hod=18.0,
        temp=26.2,
        out_t=31.0,
        vpp_event={"id": "vpp1", "trigger_h": 18.0, "end_h": 19.0, "day": 1},
        vpp_target_kwh=2.0,
        appliance_config=_sample_state()["appliance_config"],
    )

    assert action["appliance_actions"] == before
    assert set(terms) == {
        "version",
        "total",
        "cost",
        "user",
        "grid",
        "slack",
        "weights",
        "proxy_status",
        "active_terms",
        "inactive_or_missing_terms",
        "diagnostics",
    }
    assert terms["version"] == "home_objective_v15_pdf"
    assert terms["slack"]["task"]["skip_count"] == 0.0


def test_plan_mpc_action_returns_llm_compatible_schema_and_terms() -> None:
    action = plan_mpc_action(state=_sample_state())

    assert set(action) == {
        "setpoint",
        "next_check_hour",
        "reason",
        "appliances",
        "objective_terms",
    }
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
    assert action["appliances"]["washer_skip"] is not True
    assert action["setpoint"] >= 26.5
    assert math.isfinite(float(action["objective_terms"]["total"]))
    assert set(action["objective_terms"]) == {
        "version",
        "total",
        "cost",
        "user",
        "grid",
        "slack",
        "weights",
        "proxy_status",
        "active_terms",
        "inactive_or_missing_terms",
        "diagnostics",
    }
    assert action["objective_terms"]["version"] == "home_objective_v15_pdf"
    assert "grid_target" in action["objective_terms"]["proxy_status"]
    assert action["reason"].startswith("mpc_pdf_v15 total=")


def test_plan_mpc_action_uses_berlin_dynamic_model_for_germany() -> None:
    state = _sample_state()
    state["city"] = "Germany"
    state["weather_label"] = "Germany"
    state["outdoor_temp_c"] = 22.0

    action = plan_mpc_action(state=state)

    diagnostics = action["objective_terms"]["diagnostics"]["dynamic_model_prediction"]
    assert diagnostics["model"] == "regional_5r3c_hvac_solar_dynamic_model_v2"
    assert diagnostics["region"] == "berlin"
    assert "regional_5r3c/berlin" in diagnostics["thermal_parameters_path"]


def test_plan_mpc_action_is_pdf_v15_only_api() -> None:
    assert "objective_version" not in inspect.signature(plan_mpc_action).parameters


def test_ev_policy_completion_requires_charge_window_not_mode() -> None:
    cfg = {
        "ev": {"present": True},
        "washer": {"present": False},
        "dishwasher": {"present": False},
        "dryer": {"present": False},
        "water_heater": {"present": False},
    }

    assert _missing_explicit_appliance_actions({"ev_mode": "smart"}, cfg) == [
        "ev_charge_start_h",
        "ev_charge_end_h",
    ]
    assert _missing_explicit_appliance_actions(
        {"ev_mode": None, "ev_charge_start_h": 20.0, "ev_charge_end_h": 7.0},
        cfg,
    ) == []


def test_completed_shiftable_task_gets_no_new_start_or_skip() -> None:
    state = _sample_state()
    state["sim_h"] = 19.0
    state["hod"] = 19.0
    state["vpp_event"] = None
    state["vpp_active"] = False
    state["appliance_results"] = {
        "washer": [
            {
                "present": True,
                "completed": True,
                "skipped": False,
                "scheduled_abs_h": 14.0,
            }
        ]
    }

    action = plan_mpc_action(state=state)

    assert action["appliances"]["washer_start_h"] is None
    assert action["appliances"]["washer_skip"] is None


def test_mpc_schedules_shiftable_when_preferred_start_would_finish_too_late() -> None:
    state = _sample_state()
    state["sim_h"] = 0.0
    state["hod"] = 0.0
    state["vpp_active"] = False
    state["vpp_event"] = None
    state["appliance_config"]["washer"] = {"present": False}
    state["appliance_config"]["dishwasher"] = {
        "present": True,
        "earliest_h": 9.0,
        "latest_h": 23.0,
        "preferred_h": 22.0,
        "duration_h": 1.5,
        "power_kw": 1.2,
        "shiftable": True,
        "dr_adjustable": True,
    }

    action = plan_mpc_action(state=state)

    assert action["appliances"]["dishwasher_start_h"] is not None
    assert action["appliances"]["dishwasher_start_h"] + 1.5 <= 23.0
    assert action["appliances"]["dishwasher_skip"] is False


def test_water_heater_vpp_boundary_uses_half_open_window() -> None:
    config = {
        "washer": {"present": False},
        "dishwasher": {"present": False},
        "dryer": {"present": False},
        "water_heater": {
            "present": True,
            "dr_adjustable": True,
            "pre_heat_window_start_h": 14.0,
            "pre_heat_window_end_h": 18.0,
            "rated_kw": 2.0,
        },
        "ev": {"present": False},
        "refrigerator": {"present": False},
    }
    events = [{"id": "vpp1", "trigger_h": 18.0, "end_h": 19.0, "day": 1}]

    avoided = ApplianceSuite(config, sim_days=1, vpp_events=events)
    avoided.set_ewh_preheat_schedule(0, start_h=14.0, end_h=18.0, temp_c=65.0)
    assert avoided.step(18.0, 1.0 / 6.0)["water_heater"] == 0.0
    assert avoided.vpp_day_summary(0)["water_heater"]["ran_during_vpp"] is False

    overlapping = ApplianceSuite(config, sim_days=1, vpp_events=events)
    overlapping.set_ewh_preheat_schedule(0, start_h=17.0, end_h=19.0, temp_c=65.0)
    assert overlapping.step(18.0, 1.0 / 6.0)["water_heater"] > 0.0
    assert overlapping.vpp_day_summary(0)["water_heater"]["ran_during_vpp"] is True


def test_vpp_replan_guard_only_moves_existing_vpp_overlap_after_event() -> None:
    config = {
        "washer": {
            "present": True,
            "earliest_h": 8.0,
            "latest_h": 22.0,
            "preferred_h": 14.0,
            "duration_h": 1.0,
            "power_kw": 1.5,
            "shiftable": True,
            "dr_adjustable": True,
        },
        "dishwasher": {"present": False},
        "dryer": {"present": False},
        "water_heater": {"present": False},
        "ev": {"present": False},
        "refrigerator": {"present": False},
    }
    event = {"id": "vpp1", "trigger_h": 18.0, "end_h": 19.0, "day": 1}

    not_conflicting = ApplianceSuite(config, sim_days=1, vpp_events=[event], explicit_only=True)
    assert not_conflicting.shift_appliance("washer", 0, 14.0)
    filtered, guard = _filter_vpp_event_replan_actions(
        actions={"washer_start_h": 20.0, "washer_skip": False},
        suite=not_conflicting,
        appliance_config=config,
        event=event,
        sim_h=18.0,
    )
    assert filtered == {}
    assert guard["dropped"] == ["washer:not_planned_in_vpp"]

    conflicting = ApplianceSuite(config, sim_days=1, vpp_events=[event], explicit_only=True)
    assert conflicting.shift_appliance("washer", 0, 18.0)
    filtered, guard = _filter_vpp_event_replan_actions(
        actions={"washer_start_h": 19.0, "washer_skip": False},
        suite=conflicting,
        appliance_config=config,
        event=event,
        sim_h=18.0,
    )
    assert filtered == {"washer_start_h": 19.0, "washer_skip": False}
    assert guard["kept"] == ["washer"]


def test_vpp_replan_guard_rejects_replan_before_event_end() -> None:
    config = {
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
        "water_heater": {"present": False},
        "ev": {"present": False},
        "refrigerator": {"present": False},
    }
    event = {"id": "vpp1", "trigger_h": 18.0, "end_h": 19.0, "day": 1}
    suite = ApplianceSuite(config, sim_days=1, vpp_events=[event], explicit_only=True)
    assert suite.shift_appliance("washer", 0, 18.0)

    filtered, guard = _filter_vpp_event_replan_actions(
        actions={"washer_start_h": 18.5, "washer_skip": False},
        suite=suite,
        appliance_config=config,
        event=event,
        sim_h=18.0,
    )

    assert filtered == {}
    assert guard["dropped"] == ["washer:not_after_vpp"]
