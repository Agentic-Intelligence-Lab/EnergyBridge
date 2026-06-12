"""EnergyPlus horizon evaluator for the MPC baseline.

This is intentionally opt-in because it starts EnergyPlus subprocess work for
candidate scoring. It uses the same IDF/EPW plant and the same appliance
write-back path as the benchmark runner, then returns objective-ready states.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from ..state_adapter import build_mpc_state


PROJECT_ROOT = Path(__file__).resolve().parents[4]
EPLUS_ROOT = Path(os.environ.get("EPLUS_ROOT", "/home/hku_user/EnergyPlus-24-1-0"))
for path in (PROJECT_ROOT, EPLUS_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


class EnergyPlusHorizonScorer:
    """Run short EnergyPlus rollouts and expose H-step objective states."""

    def __init__(self, horizon_steps: int = 18) -> None:
        self.horizon_steps = max(1, int(horizon_steps))

    def predict_objective_trajectory(self, state: dict, action: dict) -> tuple[list[dict[str, Any]], dict]:
        rows = self._run_rollout(state, action)
        if not rows:
            raise RuntimeError("EnergyPlus predictor produced no horizon rows")
        diagnostics = {
            "model": "energyplus_horizon_predictor_v1",
            "horizon_steps": len(rows),
            "horizon_minutes": round(len(rows) * float(rows[0].get("dt_h", 1.0 / 6.0)) * 60.0, 3),
            "predicted_temp_c": rows[-1].get("temp_c"),
            "predicted_hvac_power_kw": rows[-1].get("hvac_power_kw"),
            "predicted_total_power_kw": rows[-1].get("facility_power_kw"),
            "stage_total_power_kw": [round(float(row.get("facility_power_kw", 0.0)), 6) for row in rows],
        }
        for row in rows:
            row["dynamic_model_prediction"] = diagnostics
        return rows, diagnostics

    def _run_rollout(self, decision_state: dict, action: dict) -> list[dict[str, Any]]:
        from pyenergyplus.api import EnergyPlusAPI
        from energybridge.simulation.appliance_sim import ApplianceSuite
        from experiments.benchmark.family_runner import (
            DEFAULT_FAMILY_EPW,
            DEFAULT_FAMILY_IDF,
            HTG_SP,
            VPP_EVENTS,
            _FamilyLoop,
            _apply_appliance_actions,
            _write_appliance_actuators,
        )

        sim_h0 = float(decision_state.get("sim_h") or 0.0)
        idf_path = Path(decision_state.get("idf_path") or DEFAULT_FAMILY_IDF)
        epw_path = Path(decision_state.get("epw_path") or DEFAULT_FAMILY_EPW)
        output_root = Path(decision_state.get("mpc_ep_output_dir") or "/tmp/energybridge-mpc-ep")
        action_sig = hashlib.sha1(
            json.dumps(
                {"sim_h": sim_h0, "action": action},
                sort_keys=True,
                default=str,
            ).encode("utf-8")
        ).hexdigest()[:12]
        output_dir = output_root / f"candidate_{action_sig}"
        if output_dir.exists():
            shutil.rmtree(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        api = EnergyPlusAPI()
        ep_state = api.state_manager.new_state()
        api.runtime.set_console_output_status(ep_state, False)
        ex = api.exchange
        loop = _FamilyLoop()
        loop.appliance_suite = ApplianceSuite(
            decision_state.get("appliance_config") or {},
            sim_days=3,
            vpp_events=VPP_EVENTS,
        )
        ex.request_variable(ep_state, "Zone Mean Air Temperature", "living_unit1")
        ex.request_variable(ep_state, "Facility Total Electricity Demand Rate", "Whole Building")
        ex.request_variable(ep_state, "Site Outdoor Air Drybulb Temperature", "Environment")

        rows: list[dict[str, Any]] = []
        applied_decisions: set[float] = set()
        history = sorted(
            list(decision_state.get("mpc_decision_history") or []),
            key=lambda item: float(item.get("h", -1.0)),
        )

        def action_for_time(sim_h: float) -> dict | None:
            selected = None
            for item in history:
                if float(item.get("h", -1.0)) <= sim_h:
                    selected = {
                        "setpoint": item.get("sp"),
                        "appliances": dict(item.get("raw_appliance_actions") or item.get("actions") or {}),
                    }
            if sim_h >= sim_h0:
                selected = action
            return selected

        def maybe_apply_schedule(sim_h: float) -> None:
            for item in history:
                h = float(item.get("h", -1.0))
                if h <= sim_h and h not in applied_decisions:
                    _apply_appliance_actions(
                        loop.appliance_suite,
                        dict(item.get("raw_appliance_actions") or item.get("actions") or {}),
                        h,
                    )
                    applied_decisions.add(h)
            if sim_h >= sim_h0 and sim_h0 not in applied_decisions:
                _apply_appliance_actions(loop.appliance_suite, dict(action.get("appliances") or {}), sim_h0)
                applied_decisions.add(sim_h0)

        def callback(s) -> None:
            if not loop.init(ex, s):
                return
            if loop.h_out == -1:
                loop.h_out = ex.get_variable_handle(s, "Site Outdoor Air Drybulb Temperature", "Environment")
            if ex.warmup_flag(s):
                return
            day = ex.day_of_year(s)
            if loop.start_day is None:
                loop.start_day = day
            sim_h = (day - loop.start_day) * 24.0 + ex.current_time(s)
            if sim_h > sim_h0 + self.horizon_steps / 6.0 + 1.0:
                api.runtime.stop_simulation(s)
                return
            dt_h = float(ex.zone_time_step(s))
            current_action = action_for_time(sim_h)
            if current_action is not None:
                loop.sp = float(current_action.get("setpoint") or loop.sp)
            maybe_apply_schedule(sim_h)
            if loop.h_cool != -1:
                ex.set_actuator_value(s, loop.h_cool, loop.sp)
            if loop.h_heat != -1:
                ex.set_actuator_value(s, loop.h_heat, HTG_SP)
            powers = loop.appliance_suite.step(sim_h, dt_h)
            _write_appliance_actuators(ex, s, loop, powers, sim_h)

            if sim_h < sim_h0 or len(rows) >= self.horizon_steps:
                if len(rows) >= self.horizon_steps:
                    api.runtime.stop_simulation(s)
                return
            temp = float(ex.get_variable_value(s, loop.h_temp)) if loop.h_temp != -1 else decision_state.get("temp_c")
            outdoor = float(ex.get_variable_value(s, loop.h_out)) if loop.h_out != -1 else decision_state.get("outdoor_temp_c")
            facility_kw = max(0.0, float(ex.get_variable_value(s, loop.h_fac)) / 1000.0) if loop.h_fac != -1 else 0.0
            appliance_kw = sum(float(v) for v in powers.values())
            hvac_kw = max(0.0, facility_kw - appliance_kw)
            event = _event_at(sim_h, decision_state.get("vpp_event"))
            objective_state = build_mpc_state(
                sim_h=sim_h,
                hod=sim_h % 24.0,
                day_idx=int(sim_h // 24),
                temp_c=temp,
                outdoor_temp_c=outdoor,
                current_setpoint_c=loop.sp,
                vpp_event=event,
                vpp_target_kwh=decision_state.get("vpp_target_kwh") if event else None,
                appliance_config=decision_state.get("appliance_config") or {},
                appliance_suite=loop.appliance_suite,
                history=dict(decision_state.get("history") or {}),
            )
            objective_state.update({
                "dt_h": dt_h,
                "hvac_power_kw": hvac_kw,
                "base_load_kw": 0.0,
                "facility_power_kw": facility_kw,
            })
            rows.append(objective_state)

        try:
            api.runtime.callback_end_system_timestep_after_hvac_reporting(ep_state, callback)
            with contextlib.redirect_stdout(io.StringIO()):
                api.runtime.run_energyplus(ep_state, ["-w", str(epw_path), "-d", str(output_dir), str(idf_path)])
            api.state_manager.delete_state(ep_state)
        finally:
            shutil.rmtree(output_dir, ignore_errors=True)
        return rows[: self.horizon_steps]


def _event_at(sim_h: float, event: Any) -> dict | None:
    if not isinstance(event, dict):
        return None
    start = event.get("trigger_h")
    end = event.get("end_h")
    try:
        if float(start) <= sim_h < float(end):
            return dict(event)
    except (TypeError, ValueError):
        return None
    return None
