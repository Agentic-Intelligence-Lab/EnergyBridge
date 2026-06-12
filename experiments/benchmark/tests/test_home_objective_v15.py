from __future__ import annotations

import math

from experiments.benchmark.baselines.weights import (
    PDF_V15_WEIGHTS_DEFAULT,
    PDF_V15_WEIGHTS_DR,
)
from experiments.benchmark.baselines.home_objective_v15 import (
    compute_home_objective_v15,
)


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


def _action(appliances: dict | None = None, *, setpoint: float = 26.0) -> dict:
    return {
        "setpoint": setpoint,
        "next_check_hour": None,
        "reason": "",
        "appliances": appliances or {},
    }


def test_home_objective_v15_returns_expected_top_level_structure() -> None:
    terms = compute_home_objective_v15(
        state=_sample_state(),
        action=_action({"washer_start_h": 17.0, "washer_skip": False}),
    )

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
    assert math.isfinite(float(terms["total"]))
    assert {"total", "raw", "normalized", "available", "missing", "notes"} <= set(
        terms["cost"]
    )
    assert {"temp", "pref", "time", "ev"} <= set(terms["user"])
    assert {"peak", "dr_contribution"} <= set(terms["grid"])
    assert {"hvac", "task", "ev", "grid"} <= set(terms["slack"])
    assert isinstance(terms["active_terms"], list)
    assert isinstance(terms["inactive_or_missing_terms"], list)


def test_pdf_v15_default_and_dr_weights_have_main_weights_summing_to_one() -> None:
    for weights in (PDF_V15_WEIGHTS_DEFAULT, PDF_V15_WEIGHTS_DR):
        assert (
            weights["alpha_cost"] + weights["alpha_user"] + weights["alpha_grid"]
        ) == 1.0
        assert weights["lambda_slack"] == 100.0
        assert weights["q_time"] == 2.0


def test_missing_ev_soc_marks_ev_inactive_without_crashing() -> None:
    state = _sample_state()
    state["appliance_config"]["ev"] = {
        "present": True,
        "arrival_h": 18.0,
        "departure_h": 7.5,
    }

    terms = compute_home_objective_v15(state=state, action=_action({"ev_mode": "smart"}))

    assert math.isfinite(float(terms["total"]))
    assert terms["user"]["ev"]["active"] is False
    assert terms["slack"]["ev"]["active"] is False
    assert "ev_current_soc" in terms["user"]["ev"]["missing"]
    assert "ev_required_soc" in terms["user"]["ev"]["missing"]
    assert terms["proxy_status"]["ev_soc"] == "unavailable"
    assert "user.ev" in terms["inactive_or_missing_terms"]


def test_missing_dr_baseline_marks_dr_contribution_inactive() -> None:
    terms = compute_home_objective_v15(
        state=_sample_state(),
        action=_action({"washer_start_h": 19.0, "washer_skip": False}),
    )

    assert terms["grid"]["dr_contribution"]["active"] is False
    assert terms["grid"]["dr_contribution"]["dr_baseline_available"] is False
    assert terms["proxy_status"]["dr_baseline"] == "unavailable"
    assert "grid.dr_contribution" in terms["inactive_or_missing_terms"]


def test_explicit_task_skip_increases_slack_or_task_violation() -> None:
    state = _sample_state()
    scheduled = compute_home_objective_v15(
        state=state,
        action=_action({"washer_start_h": 17.0, "washer_skip": False}),
    )
    skipped = compute_home_objective_v15(
        state=state,
        action=_action({"washer_start_h": None, "washer_skip": True}),
    )

    assert skipped["slack"]["task"]["skip_count"] == 1.0
    assert skipped["slack"]["task"]["total"] > scheduled["slack"]["task"]["total"]
    assert skipped["total"] > scheduled["total"]


def test_lower_energy_and_lower_peak_candidate_scores_lower_when_other_terms_equal() -> None:
    state = _sample_state()
    state["vpp_event"] = None
    state["vpp_active"] = False
    state["vpp_target_kwh"] = None
    state["grid_power_target_kw"] = 1.0
    state["base_load_forecast_kw"] = 0.0
    state["appliance_config"]["washer"]["preferred_h"] = 17.0

    low = compute_home_objective_v15(
        state=state,
        action=_action({"washer_start_h": 17.0, "washer_skip": False}, setpoint=27.0),
    )
    high = compute_home_objective_v15(
        state=state,
        action=_action({"washer_start_h": 17.0, "washer_skip": False}, setpoint=24.0),
    )

    assert high["cost"]["total"] > low["cost"]["total"]
    assert high["grid"]["peak"]["total"] > low["grid"]["peak"]["total"]
    assert high["total"] > low["total"]


def test_water_heater_schedule_contributes_to_shiftable_cost_and_peak() -> None:
    state = _sample_state()
    state["vpp_event"] = None
    state["vpp_active"] = False
    state["vpp_target_kwh"] = None
    state["grid_power_target_kw"] = 1.0
    state["base_load_forecast_kw"] = 0.0
    state["appliance_config"]["washer"]["present"] = False
    state["appliance_config"]["water_heater"] = {
        "present": True,
        "rated_kw": 2.0,
        "bath_required_h": 21.0,
        "dr_adjustable": True,
        "pre_heat_window_start_h": 15.0,
        "pre_heat_window_end_h": 18.0,
    }

    terms = compute_home_objective_v15(
        state=state,
        action=_action(
            {
                "water_heater_preheat": True,
                "water_heater_preheat_start_h": 16.0,
                "water_heater_preheat_end_h": 17.0,
                "water_heater_preheat_temp_c": 65.0,
            }
        ),
    )

    assert terms["diagnostics"]["water_heater_included"] is True
    assert terms["diagnostics"]["water_heater"]["wh_model"] == "shiftable_load_proxy"
    assert terms["cost"]["raw"]["shift_power_kw"] == 2.0
    assert terms["cost"]["raw"]["water_heater"]["power_kw"] == 2.0
    assert terms["grid"]["peak"]["raw"]["shift_power_kw"] == 2.0
    assert "cost.water_heater" in terms["active_terms"]


def test_water_heater_missing_or_failed_service_increases_slack_without_tank_model() -> None:
    state = _sample_state()
    state["appliance_config"]["washer"]["present"] = False
    state["appliance_config"]["water_heater"] = {
        "present": True,
        "rated_kw": 2.0,
        "bath_required_h": 21.0,
        "dr_adjustable": True,
        "pre_heat_window_start_h": 15.0,
        "pre_heat_window_end_h": 18.0,
    }
    state["appliance_vpp_summary"] = {"water_heater": {"ready_at_bath": False}}

    scheduled = compute_home_objective_v15(
        state=state,
        action=_action(
            {
                "water_heater_preheat": True,
                "water_heater_preheat_start_h": 16.0,
                "water_heater_preheat_end_h": 17.0,
            }
        ),
    )
    skipped = compute_home_objective_v15(
        state=state,
        action=_action({"water_heater_preheat": False}),
    )

    assert scheduled["slack"]["task"]["raw"]["water_heater"]["ready_at_bath"] is False
    assert scheduled["slack"]["task"]["raw"]["water_heater"]["total"] > 0.0
    assert skipped["slack"]["task"]["raw"]["water_heater"]["skip"] is True
    assert skipped["slack"]["task"]["total"] > scheduled["slack"]["task"]["total"]
    assert scheduled["diagnostics"]["water_heater"]["wh_tank_model_available"] is False
    assert "Add tank-temperature or readiness forecast later" in scheduled["diagnostics"]["water_heater"]["todo"]
