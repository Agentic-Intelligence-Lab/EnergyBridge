"""v2 RL environment (dynamic-model backend): expanded action, price-aware,
preference-aware, metric-aligned reward. Uses the MPC dynamic model
(experiments/benchmark/baselines/mpc/dynamic_model) for state transition
instead of EnergyPlus, giving ~10x faster training.

Same action / observation / reward interface as EnergyPlusFamilyEnvV2 so
PPO checkpoints are compatible for warm-start / fine-tune workflows.

Design:
- No threading (dynamic model is synchronous Python)
- Reuse ApplianceSuite, reward, observation, price/preference proxy from
  environment_pref_v2.py (imported directly)
- outdoor temperature: for now use a fixed daily curve; can be upgraded to
  EPW-driven forecast later
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import gymnasium as gym
import numpy as np
from gymnasium import spaces

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from energybridge.simulation.appliance_sim import ApplianceSuite
from energybridge.data.day_ahead import maybe_load_price_profile
from experiments.benchmark.family_runner import _make_vpp_events

from .environment_pref_v2 import (
    ACTION_DIM_V2,
    OBS_DIM_V2,
    OBSERVATION_NAMES_V2,
    REWARD_WEIGHTS_V2,
    EnergyPlusFamilyEnvV2,
    _PriceProfileAdapter,
    _build_user_preference_proxy,
    _compute_reward_v2,
    _decode_action_v2,
    _price_features,
    _terminal_reward_v2,
)

from experiments.benchmark.baselines.mpc.dynamic_model import DynamicModelScorer, dynamic_model_region_for_state


class _MockLoop:
    """Lightweight stand-in for _FamilyLoop; only exposes attributes that
    EnergyPlusFamilyEnvV2._observation reads (appliance_suite + sp)."""
    def __init__(self):
        self.appliance_suite: ApplianceSuite | None = None
        self.sp: float = 25.0


class DynamicFamilyEnvV2(gym.Env):
    """Dynamic-model backed twin of EnergyPlusFamilyEnvV2.

    Same (obs, action, reward) contract, no EnergyPlus, no threading.
    """

    metadata = {"render_modes": []}
    OBSERVATION_NAMES = OBSERVATION_NAMES_V2

    # Alias the shiftable/water-heater observation helpers so
    # EnergyPlusFamilyEnvV2._observation (invoked below) can find them on `self`.
    _shiftable_obs = staticmethod(EnergyPlusFamilyEnvV2._shiftable_obs)
    _wh_obs = staticmethod(EnergyPlusFamilyEnvV2._wh_obs)

    def __init__(self, output_root: str | Path = "/tmp/energybridge_rl_dyn_v2",
                 persona_id: str = "basic_role_a_commuter_price_cooperative",
                 city: str = "Tianjin",
                 start_date: str = "",
                 price_csv: str = "",
                 price_profile: Any = None,
                 reward_mode: str = "default") -> None:
        super().__init__()
        self.reward_mode = reward_mode
        persona_path = PROJECT_ROOT / "energybridge" / "roleplay" / "personas" / f"{persona_id}.json"
        self.persona = json.loads(persona_path.read_text(encoding="utf-8"))
        self.pref_proxy = _build_user_preference_proxy(self.persona)
        appliance_cfg = self.persona.get("appliances", {})
        self._comfort_min = float(appliance_cfg.get("ac", {}).get("setpoint_preferred_min_c", 24.0))
        self._comfort_max = float(appliance_cfg.get("ac", {}).get("setpoint_preferred_max_c", 26.0))
        self.output_root = Path(output_root)
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.city = city
        self.start_date = start_date
        self.price_csv = price_csv

        # Price profile (same wrapping as EP env)
        if price_profile is not None:
            raw_profile = price_profile
        elif price_csv:
            try:
                raw_profile = maybe_load_price_profile(Path(price_csv))
            except Exception:
                raw_profile = None
        else:
            raw_profile = None
        self.price_profile = (
            _PriceProfileAdapter(raw_profile, start_date)
            if raw_profile is not None else None
        )

        self._sim_days = 7 if city.lower() == "germany" else 3
        self._dt_h = 10.0 / 60.0  # 10-minute step (matches EP and dynamic model)
        self._max_sim_h = float(self._sim_days) * 24.0

        self.action_space = spaces.Box(low=-1.0, high=1.0, shape=(ACTION_DIM_V2,), dtype=np.float32)
        self.observation_space = spaces.Box(
            low=-np.inf, high=np.inf, shape=(OBS_DIM_V2,), dtype=np.float32
        )

        # Dynamic model scorer (single-step for RL env)
        region_key = dynamic_model_region_for_state({"city": city})
        self._scorer = DynamicModelScorer(horizon_steps=1, region_key=region_key)

        # Episode state (populated in reset)
        self.rows: list[dict[str, Any]] = []
        self.final_appliance_results: dict[str, Any] = {}
        self.vpp_event_energy: dict[str, float] = {}
        self.terminal_bonus: float = 0.0
        self._step_count: int = 0  # integer step counter to avoid float drift
        self._sim_h: float = 0.0
        self._temp: float = 26.0
        self._loop = _MockLoop()
        self._daily_appliance_set: dict[int, set] = {}
        self._last_action = np.array(
            [25.0, 15.5, 16.0, 12.0, 60.0, 21.0, 7.0, 15.5], dtype=np.float32
        )
        self._vpp_events: list[dict[str, Any]] = []

    def _outdoor_temp_c(self, sim_h: float) -> float:
        """Simple daily temperature curve until we wire in EPW forecast.

        Tianjin June: ~22-32°C sinusoidal; Germany June: ~14-24°C.
        """
        hod = sim_h % 24.0
        if self.city.lower() == "germany":
            base, amp = 19.0, 5.0
        else:  # Tianjin default
            base, amp = 27.0, 5.0
        return base + amp * float(np.sin(2 * np.pi * (hod - 8.0) / 24.0))

    def reset(self, *, seed: int | None = None, options: dict | None = None):
        super().reset(seed=seed)
        self.rows = []
        self.vpp_event_energy = {}
        self._step_count = 0
        self._sim_h = 0.0
        self._temp = 26.0
        self._daily_appliance_set = {d: set() for d in range(self._sim_days)}
        self._last_action = np.array(
            [25.0, 15.5, 16.0, 12.0, 60.0, 21.0, 7.0, 15.5], dtype=np.float32
        )
        self._vpp_events = _make_vpp_events(self._sim_days, start_h=18.0, duration_h=1.0)

        self._loop = _MockLoop()
        self._loop.appliance_suite = ApplianceSuite(
            self.persona.get("appliances", {}),
            sim_days=self._sim_days,
            vpp_events=self._vpp_events,
            explicit_only=True,
        )
        self._loop.sp = 25.0

        obs = self._compute_observation()
        return obs, {}

    def step(self, action: np.ndarray):
        decoded = _decode_action_v2(action)
        self._last_action = decoded
        self._loop.sp = float(np.clip(decoded[0], 22.0, 28.0))

        sim_h_before = self._sim_h
        day_idx = min(self._sim_days - 1, int(sim_h_before // 24))
        hod = sim_h_before % 24.0
        vpp_active_before = any(
            ev["trigger_h"] <= sim_h_before < ev["end_h"] for ev in self._vpp_events
        )

        # Apply appliance actions (once per day per device), mirror EP callback
        self._apply_appliance_actions(decoded, day_idx, vpp_active_before)

        # Update appliance suite state (updates SOC / task progress / etc.)
        powers = self._loop.appliance_suite.step(sim_h_before, self._dt_h)

        # Call dynamic model to get next temp / total power
        outdoor = self._outdoor_temp_c(sim_h_before)
        pfeat = _price_features(self.price_profile, sim_h_before)
        state_dict = {
            "sim_h": sim_h_before,
            "day_idx": day_idx,
            "hod": hod,
            "temp_c": self._temp,
            "outdoor_temp_c": outdoor,
            "price": float(pfeat.get("current", 1.0)),
            "base_load_kw": float(sum(powers.get(k, 0.0) for k in
                ("refrigerator",))),
            "vpp_active": vpp_active_before,
            "ev_current_soc": float(self._loop.appliance_suite._ev._soc)
                if self._loop.appliance_suite._ev is not None else 0.5,
            "current_setpoint_c": self._loop.sp,
        }
        action_dict = {
            "setpoint": self._loop.sp,
            "appliances": {
                "washer_start_h": float(decoded[1]),
                "dishwasher_start_h": float(decoded[2]),
                "dryer_start_h": float(decoded[7]),
                "water_heater_preheat_start_h": float(np.clip(decoded[3], 7.0, 17.0)),
                "water_heater_preheat_end_h": float(min(18.0, float(decoded[3]) + 3.0)),
                "water_heater_preheat_temp_c": float(np.clip(decoded[4], 45.0, 75.0)),
                "ev_charge_start_h": float(decoded[5]),
                "ev_charge_end_h": float(decoded[6]),
            },
        }
        _, diag = self._scorer.predict_objective_trajectory(state_dict, action_dict)
        # Dynamic model outputs: predicted_temp_c, predicted_total_power_kw
        next_temp = float(diag["predicted_temp_c"])
        total_power_kw = float(diag["predicted_total_power_kw"])
        energy_kwh = total_power_kw * self._dt_h

        # Advance sim time (integer step counter to avoid float drift breaking
        # dynamic_model._initial_state's `minute = round((hod % 1) * 60)` bound)
        self._step_count += 1
        self._sim_h = self._step_count / 6.0
        self._temp = next_temp

        # Accumulate VPP event energy at the *new* sim_h window
        for ev in self._vpp_events:
            if ev["trigger_h"] <= self._sim_h < ev["end_h"]:
                eid = ev["id"]
                self.vpp_event_energy[eid] = (
                    self.vpp_event_energy.get(eid, 0.0) + total_power_kw * self._dt_h
                )
        vpp_active_now = any(
            ev["trigger_h"] <= self._sim_h < ev["end_h"] for ev in self._vpp_events
        )

        # Compute reward
        reward = _compute_reward_v2(
            energy_kwh=energy_kwh, temp_c=next_temp, sim_h=self._sim_h,
            vpp_active=vpp_active_now,
            vpp_window_kwh_accum=sum(self.vpp_event_energy.values()),
            comfort_min=self._comfort_min, comfort_max=self._comfort_max,
            pref_weight=self.pref_proxy["comfort_weight"],
            comfort_weight=self.pref_proxy["comfort_weight"],
            price_sensitivity=self.pref_proxy["price_sensitivity"],
            vpp_cooperation=self.pref_proxy["vpp_cooperation"],
            price_current=float(pfeat.get("current", 0.0)),
            appliance_results=None,
            vpp_event_energy=self.vpp_event_energy,
        )

        # Row for CSV logging
        row = {
            "sim_hour": self._sim_h,
            "indoor_temperature_c": next_temp,
            "outdoor_temperature_c": outdoor,
            "facility_power_kw": total_power_kw,
            "energy_kwh": energy_kwh,
            "cooling_setpoint_c": float(decoded[0]),
            "vpp_active": int(vpp_active_now),
            "pmv": _pmv_placeholder(next_temp),
            "washer_start_request": float(decoded[1]),
            "dishwasher_start_request": float(decoded[2]),
            "water_heater_preheat_request": float(decoded[3]),
            "dryer_start_request": float(decoded[7]),
            "reward": reward,
        }
        self.rows.append(row)

        terminated = self._sim_h >= self._max_sim_h - 1e-9
        obs = self._compute_observation()
        info: dict[str, Any] = {"energyplus_family": row}

        if terminated:
            terminal_bonus = _terminal_reward_v2(
                self._loop, self.vpp_event_energy, self.price_profile
            )
            self.terminal_bonus = float(terminal_bonus)
            reward = float(reward) + terminal_bonus
            self.final_appliance_results = self._loop.appliance_suite.all_results()

        return obs, float(reward), terminated, False, info

    def _apply_appliance_actions(self, decoded: np.ndarray, day_idx: int, vpp_active: bool) -> None:
        done_today = self._daily_appliance_set.setdefault(day_idx, set())
        suite = self._loop.appliance_suite
        if "washer" not in done_today and float(decoded[1]) >= 8.0 and not vpp_active:
            suite.shift_appliance("washer", day_idx, day_idx * 24.0 + float(decoded[1]))
            done_today.add("washer")
        if "dishwasher" not in done_today and float(decoded[2]) >= 9.0 and not vpp_active:
            suite.shift_appliance("dishwasher", day_idx, day_idx * 24.0 + float(decoded[2]))
            done_today.add("dishwasher")
        if "dryer" not in done_today and float(decoded[7]) >= 8.0 and not vpp_active:
            suite.shift_appliance("dryer", day_idx, day_idx * 24.0 + float(decoded[7]))
            done_today.add("dryer")
        if "water_heater" not in done_today:
            wh_start = float(np.clip(decoded[3], 7.0, 17.0))
            wh_end = min(18.0, wh_start + 3.0)
            suite.set_ewh_preheat_schedule(
                day_idx, start_h=wh_start, end_h=wh_end,
                temp_c=float(np.clip(decoded[4], 45.0, 75.0)),
            )
            done_today.add("water_heater")
        if "ev" not in done_today:
            ev_dev = suite._ev
            ev_arrival = float(ev_dev.arrival_h) if ev_dev else 18.5
            ev_start = float(np.clip(decoded[5], ev_arrival, 20.0))
            ev_end = float(np.clip(decoded[6], 4.0, 7.5))
            suite.set_ev_mode(day_idx, "smart")
            suite.set_ev_charge_window(day_idx, start_h=ev_start, end_h=ev_end)
            done_today.add("ev")

    def _compute_observation(self) -> np.ndarray:
        sim_h = self._sim_h
        vpp_active = any(
            ev["trigger_h"] <= sim_h < ev["end_h"] for ev in self._vpp_events
        )
        pfeat = _price_features(self.price_profile, sim_h)
        # Emulate what capacity assessment would return — dynamic env doesn't
        # have a running EP so we pass a minimal placeholder assessment.
        assessment = {"committable_kw": 0.0, "recommended_bid_kw": 0.0}
        outdoor = self._outdoor_temp_c(sim_h)
        return EnergyPlusFamilyEnvV2._observation(
            self, sim_h, self._temp, outdoor, vpp_active, assessment, self._loop, pfeat
        )

    def close(self) -> None:
        pass


def _pmv_placeholder(temp_c: float) -> float:
    """Simple PMV proxy for logging (dynamic env doesn't run full thermal comfort)."""
    return (temp_c - 24.5) / 3.0
