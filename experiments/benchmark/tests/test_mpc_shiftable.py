from __future__ import annotations

import inspect
import math

from experiments.benchmark.family_runner import (
    _apply_appliance_actions,
    _compute_posthoc_decision_objective,
)
from experiments.benchmark.baselines.mpc_shiftable import plan_mpc_action


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


def test_plan_mpc_action_is_pdf_v15_only_api() -> None:
    assert "objective_version" not in inspect.signature(plan_mpc_action).parameters


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

