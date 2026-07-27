"""Synchronous Gymnasium wrapper around the three-day family EnergyPlus model."""

from __future__ import annotations

import json
import os
import queue
import shutil
import sys
import threading
import uuid
from pathlib import Path
from typing import Any

import gymnasium as gym
import numpy as np
from gymnasium import spaces

PROJECT_ROOT = Path(__file__).resolve().parents[2]
EPLUS_ROOT = Path(os.environ.get("EPLUS_ROOT", "/opt/EnergyPlus-24-1-0"))
for path in (PROJECT_ROOT, EPLUS_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from energybridge.quantification import assess_suite_vpp_request
from energybridge.simulation.appliance_sim import ApplianceSuite
from experiments.benchmark.family_runner import (
    DEFAULT_FAMILY_EPW,
    DEFAULT_FAMILY_IDF,
    HTG_SP,
    VPP_EVENTS,
    _FamilyLoop,
    _compute_pmv,
)


class EnergyPlusFamilyEnv(gym.Env):
    """One episode is the exact three-day EnergyPlus family benchmark."""

    metadata = {"render_modes": []}
    OBSERVATION_NAMES = (
        "hour_sin",
        "hour_cos",
        "remaining_sim_fraction",
        "zone_temperature_c_scaled",
        "outdoor_temperature_c_scaled",
        "vpp_active",
        "vpp_target_kwh_scaled",
        "capacity_committable_kw_scaled",
        "capacity_recommended_bid_kw_scaled",
        "capacity_ev_constrained",
        "capacity_water_heater_constrained",
        "capacity_washer_constrained",
        "capacity_dishwasher_constrained",
        "capacity_dryer_constrained",
        "washer_present",
        "washer_state_scaled",
        "washer_scheduled_hour_scaled",
        "washer_earliest_hour_scaled",
        "washer_latest_hour_scaled",
        "dishwasher_present",
        "dishwasher_state_scaled",
        "dishwasher_scheduled_hour_scaled",
        "dishwasher_earliest_hour_scaled",
        "dishwasher_latest_hour_scaled",
        "dryer_present",
        "dryer_state_scaled",
        "dryer_scheduled_hour_scaled",
        "dryer_earliest_hour_scaled",
        "dryer_latest_hour_scaled",
        "water_heater_present",
        "water_heater_preheat_requested",
        "water_heater_preheat_start_hour_scaled",
        "water_heater_preheat_end_hour_scaled",
        "water_heater_bath_required_hour_scaled",
        "ev_present",
        "ev_soc",
        "ev_target_soc",
        "ev_at_home",
        "ev_mode_scaled",
        "ev_charge_start_hour_scaled",
        "ev_charge_end_hour_scaled",
        "ev_arrival_hour_scaled",
        "refrigerator_present",
        "refrigerator_power_kw_scaled",
    )

    def __init__(self, output_root: str | Path = "/tmp/energybridge_rl_eplus",
                 persona_id: str = "atom_comfort_sensitive") -> None:
        super().__init__()
        persona_path = PROJECT_ROOT / "energybridge" / "roleplay" / "personas" / f"{persona_id}.json"
        self.persona = json.loads(persona_path.read_text(encoding="utf-8"))
        self.output_root = Path(output_root)
        self.output_root.mkdir(parents=True, exist_ok=True)
        # PPO policies are centered near zero. Keep actions normalized so the
        # initial policy does not get clipped permanently to physical minima.
        self.action_space = spaces.Box(low=-1.0, high=1.0, shape=(3,), dtype=np.float32)
        self.observation_space = spaces.Box(
            low=-np.inf, high=np.inf, shape=(len(self.OBSERVATION_NAMES),), dtype=np.float32
        )
        self._action_queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=1)
        self._packet_queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._episode_dir: Path | None = None
        self.rows: list[dict[str, Any]] = []
        self.final_appliance_results: dict[str, Any] = {}

    def reset(self, *, seed: int | None = None, options: dict | None = None):
        super().reset(seed=seed)
        self.close()
        self._stop.clear()
        self.rows = []
        self._action_queue = queue.Queue(maxsize=1)
        self._packet_queue = queue.Queue(maxsize=1)
        self._episode_dir = self.output_root / f"episode_{uuid.uuid4().hex[:10]}"
        self._thread = threading.Thread(target=self._run_energyplus, daemon=True)
        self._thread.start()
        packet = self._packet_queue.get(timeout=120)
        if packet.get("error"):
            raise RuntimeError(packet["error"])
        return packet["observation"], packet["info"]

    def step(self, action: np.ndarray):
        self._action_queue.put(np.asarray(action, dtype=np.float32), timeout=30)
        packet = self._packet_queue.get(timeout=120)
        if packet.get("error"):
            raise RuntimeError(packet["error"])
        return (
            packet["observation"], packet["reward"], packet["terminated"],
            False, packet["info"],
        )

    def close(self) -> None:
        self._stop.set()
        try:
            self._action_queue.put_nowait(np.array([26.0, 0.0, 0.0], dtype=np.float32))
        except queue.Full:
            pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        self._thread = None
        if self._episode_dir and self._episode_dir.exists():
            shutil.rmtree(self._episode_dir, ignore_errors=True)

    def _run_energyplus(self) -> None:
        try:
            from pyenergyplus.api import EnergyPlusAPI

            api = EnergyPlusAPI()
            state = api.state_manager.new_state()
            api.runtime.set_console_output_status(state, False)
            ex = api.exchange
            loop = _FamilyLoop()
            loop.appliance_suite = ApplianceSuite(
                self.persona.get("appliances", {}), sim_days=3, vpp_events=VPP_EVENTS
            )
            # RL owns task/preheat initiation; disable ApplianceSuite defaults.
            washer = loop.appliance_suite._shiftable["washer"]
            for record in washer._days.values():
                record.scheduled_abs_h = float("inf")
            water_heater = loop.appliance_suite._water_heater
            for day_state in water_heater._days.values():
                day_state.update({
                    "preheat_requested": True,
                    "preheat_start_h": 0.0,
                    "preheat_end_h": 0.0,
                    "ready_at_bath": False,
                })
            ex.request_variable(state, "Zone Mean Air Temperature", "living_unit1")
            ex.request_variable(state, "Facility Total Electricity Demand Rate", "Whole Building")
            ex.request_variable(state, "Site Outdoor Air Drybulb Temperature", "Environment")
            last_sim_h: float | None = None
            last_action = np.array([26.0, 0.0, 0.0], dtype=np.float32)
            first_packet_sent = False

            def callback(s) -> None:
                nonlocal last_sim_h, last_action, first_packet_sent
                if self._stop.is_set() or not loop.init(ex, s):
                    return
                if loop.h_out == -1:
                    loop.h_out = ex.get_variable_handle(
                        s, "Site Outdoor Air Drybulb Temperature", "Environment"
                    )
                if ex.warmup_flag(s):
                    return
                day = ex.day_of_year(s)
                if loop.start_day is None:
                    loop.start_day = day
                sim_h = (day - loop.start_day) * 24.0 + ex.current_time(s)
                if sim_h >= 72.0:
                    return
                dt = float(ex.zone_time_step(s))
                temp = float(ex.get_variable_value(s, loop.h_temp))
                facility_kw = max(0.0, float(ex.get_variable_value(s, loop.h_fac)) / 1000.0)
                outdoor = float(ex.get_variable_value(s, loop.h_out)) if loop.h_out != -1 else 30.0
                vpp_active = any(event["trigger_h"] <= sim_h < event["end_h"] for event in VPP_EVENTS)

                if last_sim_h is not None:
                    comfort_violation = max(0.0, 23.0 - temp, temp - 26.0)
                    persona_violation = max(0.0, 24.5 - temp, temp - 25.5)
                    energy_kwh = facility_kw * dt
                    occupied = 8.0 <= sim_h % 24.0 < 22.0
                    reward = -(
                        energy_kwh
                        + (5.0 * comfort_violation + 2.0 * persona_violation if occupied else 0.0)
                        + (5.0 * energy_kwh if vpp_active else 0.0)
                    )
                else:
                    comfort_violation = 0.0
                    persona_violation = 0.0
                    energy_kwh = 0.0
                    reward = 0.0

                capacity = assess_suite_vpp_request(loop.appliance_suite, sim_h, 2.0, 60.0)
                assessment = capacity["assessment"]
                observation = self._observation(
                    sim_h, temp, outdoor, vpp_active, assessment, loop
                )
                row = {
                    "sim_hour": sim_h, "indoor_temperature_c": temp, "outdoor_temperature_c": outdoor,
                    "facility_power_kw": facility_kw, "energy_kwh": energy_kwh,
                    "cooling_setpoint_c": float(last_action[0]), "vpp_active": int(vpp_active),
                    "pmv": _compute_pmv(temp), "comfort_violation_c": comfort_violation,
                    "persona_comfort_violation_c": persona_violation,
                    "capacity_committable_kw": assessment["committable_kw"],
                    "capacity_recommended_bid_kw": assessment["recommended_bid_kw"],
                    "capacity_success_probability": assessment["success_probability"],
                    "washer_start_request": float(last_action[1]),
                    "water_heater_preheat_request": float(last_action[2]),
                    "washer_power_kw": float(loop.appliance_suite._last_powers.get("washer", 0.0)),
                    "water_heater_power_kw": float(loop.appliance_suite._last_powers.get("water_heater", 0.0)),
                    "reward": reward,
                }
                if last_sim_h is not None:
                    self.rows.append(row)
                info = {"energyplus_family": row}
                self._packet_queue.put({
                    "observation": observation, "reward": reward,
                    "terminated": False, "info": info,
                })
                first_packet_sent = True
                try:
                    action = self._action_queue.get(timeout=120)
                except queue.Empty:
                    self._stop.set()
                    return
                decoded_action = self._decode_action(action)
                last_action = decoded_action
                loop.sp = float(decoded_action[0])
                day_idx = min(2, int(sim_h // 24))
                if float(decoded_action[1]) >= 0.5:
                    loop.appliance_suite.shift_appliance("washer", day_idx, sim_h)
                if float(decoded_action[2]) >= 0.5 and not vpp_active:
                    loop.appliance_suite.set_ewh_preheat_schedule(
                        day_idx, start_h=sim_h % 24, end_h=min(18.0, sim_h % 24 + 3.0), temp_c=65.0
                    )
                powers = loop.appliance_suite.step(sim_h, dt)
                self._write_actuators(ex, s, loop, powers, sim_h)
                last_sim_h = sim_h

            api.runtime.callback_end_system_timestep_after_hvac_reporting(state, callback)
            assert self._episode_dir is not None
            self._episode_dir.mkdir(parents=True, exist_ok=True)
            exit_code = api.runtime.run_energyplus(
                state,
                ["-w", str(DEFAULT_FAMILY_EPW), "-d", str(self._episode_dir), str(DEFAULT_FAMILY_IDF)],
            )
            self.final_appliance_results = loop.appliance_suite.all_results()
            api.state_manager.delete_state(state)
            if first_packet_sent:
                final_obs = np.zeros(self.observation_space.shape, dtype=np.float32)
                self._packet_queue.put({
                    "observation": final_obs, "reward": self._terminal_reward(loop),
                    "terminated": True, "info": {"energyplus_family": {"exit_code": exit_code}},
                })
        except Exception as exc:
            self._packet_queue.put({"error": repr(exc)})

    @staticmethod
    def _decode_action(action: np.ndarray) -> np.ndarray:
        normalized = np.clip(np.asarray(action, dtype=np.float32), -1.0, 1.0)
        return np.array([
            25.0 + 3.0 * normalized[0],
            (normalized[1] + 1.0) / 2.0,
            (normalized[2] + 1.0) / 2.0,
        ], dtype=np.float32)

    def _observation(self, sim_h: float, temp: float, outdoor: float,
                     vpp_active: bool, assessment: dict, loop: _FamilyLoop) -> np.ndarray:
        """Build a numeric equivalent of the context sent to the Agent LLM.

        Facility power, current setpoint, capacity success probability, and
        engineered comfort distance are intentionally excluded because the
        Agent prompt does not expose them.
        """
        hour = sim_h % 24.0
        day_idx = min(2, int(sim_h // 24))
        suite = loop.appliance_suite
        vpp_target_kwh = max(0.1, 2.0 - float(assessment["recommended_bid_kw"])) if vpp_active else 0.0
        capacity_values = [
            float(assessment["committable_kw"]) / 2.0,
            float(assessment["recommended_bid_kw"]) / 2.0,
            *self._capacity_constraint_flags(assessment),
        ] if vpp_active else [0.0] * 7
        values = [
            np.sin(2 * np.pi * hour / 24.0), np.cos(2 * np.pi * hour / 24.0),
            max(0.0, 72.0 - sim_h) / 72.0, temp / 40.0, outdoor / 45.0,
            float(vpp_active), vpp_target_kwh / 2.0,
            *capacity_values,
        ]
        for name in ("washer", "dishwasher", "dryer"):
            values.extend(self._shiftable_observation(suite._shiftable[name], day_idx))
        values.extend(self._water_heater_observation(suite._water_heater, day_idx))
        values.extend(self._ev_observation(suite._ev, day_idx, hour))
        values.extend([
            float(suite._refrigerator.present),
            float(suite._refrigerator.power_kw) / 2.0,
        ])
        observation = np.asarray(values, dtype=np.float32)
        if observation.shape != self.observation_space.shape:
            raise RuntimeError(
                f"Observation schema mismatch: {observation.shape} != {self.observation_space.shape}"
            )
        return observation

    @staticmethod
    def _capacity_constraint_flags(assessment: dict) -> list[float]:
        constraints = [str(item) for item in assessment.get("main_constraints", [])]
        return [
            float(any(item.startswith(f"{device}:") for item in constraints))
            for device in ("ev", "water_heater", "washer", "dishwasher", "dryer")
        ]

    @staticmethod
    def _shiftable_observation(appliance: Any, day_idx: int) -> list[float]:
        record = appliance._days.get(day_idx)
        skipped = appliance._day_skipped.get(day_idx, False)
        if not appliance.present or skipped:
            state = 0.0
        elif record is not None and record.completed:
            state = 3.0
        elif record is not None and record.run_start_abs_h is not None:
            state = 2.0
        else:
            state = 1.0
        scheduled_hour = (
            record.scheduled_abs_h % 24.0
            if record is not None and np.isfinite(record.scheduled_abs_h)
            else -24.0
        )
        return [
            float(appliance.present),
            state / 3.0,
            scheduled_hour / 24.0,
            float(appliance.earliest_h) / 24.0,
            float(appliance.latest_h) / 24.0,
        ]

    @staticmethod
    def _water_heater_observation(water_heater: Any, day_idx: int) -> list[float]:
        state = water_heater._days.get(day_idx, {})
        # Match WaterHeater.status_str(), which shows defaults for unset/zero values.
        start = state.get("preheat_start_h") or water_heater.pre_heat_window_start_h
        end = state.get("preheat_end_h") or water_heater.pre_heat_window_end_h
        return [
            float(water_heater.present),
            float(state.get("preheat_requested", False)),
            float(start) / 24.0,
            float(end) / 24.0,
            float(water_heater.bath_required_h) / 24.0,
        ]

    @staticmethod
    def _ev_observation(ev: Any, day_idx: int, hour: float) -> list[float]:
        mode_code = {"smart": 0.0, "delay": 0.5, "normal": 1.0}.get(
            ev._day_mode.get(day_idx, "smart"), 0.0
        )
        start = ev._day_charge_start.get(day_idx)
        end = ev._day_charge_end.get(day_idx)
        return [
            float(ev.present),
            float(ev._soc),
            float(ev.target_soc),
            float(ev._is_home(hour)),
            mode_code,
            -1.0 if start is None else float(start) / 24.0,
            -1.0 if end is None else float(end) / 24.0,
            float(ev.arrival_h) / 24.0,
        ]

    @staticmethod
    def _terminal_reward(loop: _FamilyLoop) -> float:
        results = loop.appliance_suite.all_results()
        washer_ok = sum(1 for day in results["washer"] if day.get("completed")) / 3.0
        wh_ok = sum(
            1 for day in results["water_heater"]
            if day.get("preheat_used") and float(day.get("energy_kwh", 0.0)) > 0.0
        ) / 3.0
        return 50.0 * washer_ok + 20.0 * wh_ok

    @staticmethod
    def _write_actuators(ex, state, loop: _FamilyLoop, powers: dict, sim_h: float) -> None:
        ex.set_actuator_value(state, loop.h_cool, loop.sp)
        ex.set_actuator_value(state, loop.h_heat, HTG_SP)
        for name, handle, design_kw in (
            ("washer", loop.h_washer, 2.0), ("dishwasher", loop.h_dishwasher, 1.5),
            ("dryer", loop.h_dryer, 3.0), ("refrigerator", loop.h_refrigerator, 0.2),
            ("ev", loop.h_ev, 7.0),
        ):
            if handle != -1:
                ex.set_actuator_value(state, handle, min(1.0, float(powers.get(name, 0.0)) / design_kw))
        if loop.h_ewh_sp != -1:
            ex.set_actuator_value(
                state, loop.h_ewh_sp, 65.0 if float(powers.get("water_heater", 0.0)) > 0 else 40.0
            )
