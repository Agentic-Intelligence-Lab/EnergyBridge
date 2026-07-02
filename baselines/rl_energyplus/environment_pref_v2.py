"""v2 RL environment: expanded action, price-aware, preference-aware, metric-aligned reward.

Key improvements over environment.py:
- Action: 8 dims (AC, washer, dishwasher, WH start, WH temp, EV start, EV end, dryer) vs 3
- Observation: +price features (current + next 6h mean/max/min/peak)
- Observation: +user preference proxy (comfort_weight, price_sensitivity, flexibility, vpp_cooperation)
- Reward: aligned with benchmark metrics (appliance_shift_success, VPP avoidance, user_pref)
- Decision cooldown: avoid redundant daily appliance commands
- Scenario: configurable (city/date/price) instead of hardcoded Tianjin
"""

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
EPLUS_ROOT = Path(os.environ.get("EPLUS_ROOT", "/home/hku_user/EnergyPlus-24-1-0"))
for path in (PROJECT_ROOT, EPLUS_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from energybridge.quantification import assess_suite_vpp_request
from energybridge.simulation.appliance_sim import ApplianceSuite
from energybridge.data.day_ahead import maybe_load_price_profile, generate_runperiod_idf
from experiments.benchmark.family_runner import (
    _FamilyLoop, _compute_pmv, _make_vpp_events, _is_weather_run_period,
)


class _PriceProfileAdapter:
    """Wrap DayAheadPriceProfile to expose get_price(sim_h: float).

    DayAheadPriceProfile.price_at() requires a datetime object, but
    _price_features() passes sim_h (float hours from episode start).
    This adapter converts sim_h using the episode base date.
    """

    def __init__(self, profile, base_date):
        from datetime import datetime
        self._profile = profile
        self._base = datetime.fromisoformat(base_date) if base_date else datetime(2025, 6, 1)

    def get_price(self, sim_h):
        from datetime import timedelta
        dt = self._base + timedelta(hours=float(sim_h))
        result = self._profile.price_at(dt)
        return float(result) if result is not None else 0.0


# ----- Observation schema ----------------------------------------------------
OBSERVATION_NAMES_V2 = (
    # Time (4)
    "hour_sin", "hour_cos", "day_idx_scaled", "time_to_vpp_start_scaled",
    # Environment & comfort (4)
    "zone_temperature_c_scaled", "outdoor_temperature_c_scaled",
    "current_setpoint_scaled", "occupancy",
    # VPP (4)
    "vpp_active", "vpp_target_kwh_scaled",
    "capacity_committable_kw_scaled", "capacity_recommended_bid_kw_scaled",
    # Price (5) — current and next-6h aggregate
    "price_current_scaled", "price_next6h_mean_scaled",
    "price_next6h_max_scaled", "price_next6h_min_scaled", "price_peak_indicator",
    # User preference proxy (4) — from persona tags + weights
    "comfort_weight", "price_sensitivity", "flexibility", "vpp_cooperation",
    # Appliances — washer (5)
    "washer_present", "washer_state_scaled", "washer_scheduled_hour_scaled",
    "washer_earliest_hour_scaled", "washer_latest_hour_scaled",
    # Appliances — dishwasher (5)
    "dishwasher_present", "dishwasher_state_scaled", "dishwasher_scheduled_hour_scaled",
    "dishwasher_earliest_hour_scaled", "dishwasher_latest_hour_scaled",
    # Appliances — water heater (5)
    "water_heater_present", "water_heater_preheat_requested",
    "water_heater_preheat_start_hour_scaled", "water_heater_preheat_end_hour_scaled",
    "water_heater_bath_required_hour_scaled",
    # Appliances — EV (3, placeholder if not present)
    "ev_present", "ev_soc", "ev_at_home",
    # Refrigerator (2)
    "refrigerator_present", "refrigerator_power_kw_scaled",
)
OBS_DIM_V2 = len(OBSERVATION_NAMES_V2)  # 41

# ----- Action schema ---------------------------------------------------------
# Decode ranges aligned to training persona all_appliances_full valid windows.
# dim 0: AC setpoint [22.0, 28.0]
# dim 1: Washer start hour [8.0, 19.0]         (aligned with washer.latest_h=21 - duration=2)
# dim 2: Dishwasher start hour [19.0, 21.5]    (overnight-first window; matches household schedules)
# dim 3: WH preheat start hour [7.0, 17.0]
# dim 4: WH target temperature [45.0, 75.0]
# dim 5: EV charge start hour [18.5, 20.0]     (bounded so last-day sim finishes charging before cutoff)
# dim 6: EV charge end hour   [4.0, 7.5]       (before EV departure_h=7.5; lower bound avoids too-short window)
# dim 7: Dryer start hour [8.0, 19.5]          (aligned with dryer.latest_h=21 - duration=1.5)
ACTION_DIM_V2 = 8


def _decode_action_v2(action: np.ndarray) -> np.ndarray:
    """Decode normalized [-1,1]^8 action to physical values."""
    a = np.clip(np.asarray(action, dtype=np.float32), -1.0, 1.0)
    return np.array([
        25.0 + 3.0 * a[0],                     # dim0: AC setpoint [22, 28]
        13.5 + 5.5 * a[1],                     # dim1: washer start [8, 19]
        20.25 + 1.25 * a[2],                   # dim2: dishwasher start [19, 21.5]
        12.0 + 5.0 * a[3],                     # dim3: WH preheat start [7, 17]
        60.0 + 15.0 * a[4],                    # dim4: WH temp [45, 75]
        19.25 + 0.75 * a[5],                   # dim5: EV charge start [18.5, 20.0]
        5.75 + 1.75 * a[6],                    # dim6: EV charge end [4.0, 7.5]
        13.75 + 5.75 * a[7],                   # dim7: dryer start [8, 19.5]
    ], dtype=np.float32)


# ----- User preference proxy ------------------------------------------------
def _build_user_preference_proxy(persona: dict) -> dict[str, float]:
    """Derive numeric preference signals from persona tags and scoring weights."""
    tags = persona.get("tags", {}) or {}
    weights = (persona.get("preferences", {}) or {}).get("scoring_weights", {}) or {}
    # comfort_weight: from scoring weight, normalized around 0.35
    comfort_raw = float(weights.get("comfort", 0.35))
    # price_sensitivity: derived from tags.price
    price_map = {"price_sensitive": 1.0, "price_driven": 0.9, "price_indifferent": 0.1, "low_incentive": 0.3}
    price_raw = price_map.get(tags.get("price", ""), 0.5)
    # flexibility: derived from tags.task
    flex_map = {"flexible": 1.0, "semi_rigid": 0.6, "rigid": 0.2}
    flex_raw = flex_map.get(tags.get("task", ""), 0.5)
    # vpp_cooperation: derived from tags.price + tags.control; higher = more willing
    control = tags.get("control", "")
    coop = 1.0 if control in ("high_trust_auto",) else (0.7 if control in ("suggestion_first",) else 0.4)
    if tags.get("price") in ("price_indifferent",):
        coop *= 0.5
    return {
        "comfort_weight": round(comfort_raw, 3),
        "price_sensitivity": round(price_raw, 3),
        "flexibility": round(flex_raw, 3),
        "vpp_cooperation": round(coop, 3),
    }


# ----- Price observation helpers ---------------------------------------------
def _price_features(price_profile: Any, sim_h: float) -> dict[str, float]:
    """Extract current + forecast price features at sim_h."""
    if price_profile is None or not hasattr(price_profile, "get_price"):
        return {"current": 0.0, "next6h_mean": 0.0, "next6h_max": 0.0, "next6h_min": 0.0, "peak": 0.0}
    try:
        current = float(price_profile.get_price(sim_h))
    except Exception:
        current = 0.0
    window = [float(price_profile.get_price(sim_h + h)) for h in np.arange(0.5, 6.5, 0.5)]
    window = [v for v in window if v > 0]
    if not window:
        return {"current": current, "next6h_mean": current, "next6h_max": current, "next6h_min": current, "peak": 0.0}
    mean_price = np.mean(window) if window else current
    peak = 1.0 if max(window) > 0.15 else 0.0  # heuristic: price > 0.15 EUR/kWh = peak
    return {
        "current": current, "next6h_mean": float(mean_price),
        "next6h_max": float(max(window)), "next6h_min": float(min(window)),
        "peak": float(peak),
    }


# ----- Reward ----------------------------------------------------------------
REWARD_WEIGHTS_V2 = {
    "energy_base": 0.3,
    "price_mult": 0.2,
    "vpp_mult": 2.0,
    "comfort_mult": 8.0,
    "terminal_washer": 200.0,
    "terminal_dishwasher": 200.0,
    "terminal_wh": 100.0,
    "terminal_vpp_avoid": 100.0,
    "terminal_vpp_energy": 80.0,
    "terminal_ev": 300.0,   # EV target_soc reached
    "terminal_dryer": 200.0,  # dryer scheduled within latest_h - duration_h
}


def _compute_reward_mpc_aligned(
    *, setpoint_c: float, indoor_c: float, outdoor_c: float,
    occupied: bool, vpp_active: bool,
    price_current: float, dt_h: float,
    appliance_config: dict | None, appliance_actions: dict | None,
    day_idx: int, vpp_target_kw: float | None = None,
    reward_scale: float | None = None,
) -> tuple[float, dict]:
    """MPC-aligned step-level reward using shared controllable cost."""
    from energybridge.objectives.shared_energy_objective import (
        compute_shared_controllable_cost, MPC_ALIGNED_WEIGHTS,
    )
    w = dict(MPC_ALIGNED_WEIGHTS)
    if reward_scale is not None:
        w["reward_scale"] = float(reward_scale)
    scale = w["reward_scale"]

    cost = compute_shared_controllable_cost(
        setpoint_c=setpoint_c, indoor_c=indoor_c, outdoor_c=outdoor_c,
        occupied=occupied, vpp_active=vpp_active,
        vpp_target_kw=vpp_target_kw, price_current=price_current,
        dt_h=dt_h, appliance_config=appliance_config,
        appliance_actions=appliance_actions, day_idx=day_idx,
    )
    reward = -scale * cost["total_cost"]
    return float(np.clip(reward, -50.0, 50.0)), cost


def _terminal_penalty_mpc_aligned(
    appliance_config: dict | None,
    suite: Any,
    vpp_event_energy: dict | None = None,
) -> tuple[float, dict]:
    """Episode-end penalty using MPC-aligned terminal costs."""
    from energybridge.objectives.shared_energy_objective import (
        compute_terminal_penalty, MPC_ALIGNED_WEIGHTS,
    )
    w = dict(MPC_ALIGNED_WEIGHTS)
    results = getattr(suite, "all_results", lambda: {})() if hasattr(suite, "all_results") else {}
    if not results:
        return 0.0, {}

    washer_days = [d for d in results.get("washer", []) if d.get("present")]
    dishwasher_days = [d for d in results.get("dishwasher", []) if d.get("present")]
    wh_days = [d for d in results.get("water_heater", []) if d.get("present")]

    washer_ok = all(d.get("completed") for d in washer_days) if washer_days else True
    dishwasher_ok = all(d.get("completed") for d in dishwasher_days) if dishwasher_days else True
    wh_ok = all(d.get("ready_at_bath", True) for d in wh_days) if wh_days else True

    total_vpp = sum(float(v) for v in (vpp_event_energy or {}).values())
    vpp_in = 0
    for wd in washer_days:
        if wd.get("ran_during_vpp"):
            vpp_in += 1
    for dd in dishwasher_days:
        if dd.get("ran_during_vpp"):
            vpp_in += 1

    result = compute_terminal_penalty(
        appliance_config=appliance_config,
        washer_completed=washer_ok,
        dishwasher_completed=dishwasher_ok,
        wh_ready=wh_ok,
        total_vpp_kwh=total_vpp,
        appliance_in_vpp_events=vpp_in,
    )
    penalty = result["terminal_penalty"]
    return -w["reward_scale"] * penalty, result


def _compute_reward_v2(
    *, energy_kwh: float, temp_c: float, sim_h: float,
    vpp_active: bool, vpp_window_kwh_accum: float,
    comfort_min: float, comfort_max: float,
    pref_weight: float, comfort_weight: float,
    price_sensitivity: float, vpp_cooperation: float,
    price_current: float,
    appliance_results: dict | None = None,
    vpp_event_energy: dict | None = None,
) -> float:
    """Metric-aligned reward with corrected conservative weights."""
    hod = sim_h % 24.0
    occupied = 8.0 <= hod < 22.0
    w = REWARD_WEIGHTS_V2

    # Comfort violation: per-degree penalty in occupied hours
    comfort_violation = max(0.0, comfort_min - temp_c, temp_c - comfort_max)
    comfort_penalty = w["comfort_mult"] * comfort_violation if occupied else 0.0

    # Energy: base penalty + price uplift. Structure identical to original Xudong design.
    # Tianjin: price_profile=None -> _price_features returns 0.0 -> max(0,0)=0 -> factor=1.0.
    # Germany: _PriceProfileAdapter feeds real price -> factor rises with price.
    # Negative price: max(0,...) keeps factor=1.0, no incentive to increase consumption.
    price_factor = 1.0 + w["price_mult"] * price_sensitivity * max(0.0, float(price_current) / 0.15)
    energy_penalty = w["energy_base"] * energy_kwh * price_factor

    # VPP: extra penalty during VPP window (reward cooperation)
    vpp_penalty = (vpp_cooperation * w["vpp_mult"] * energy_kwh) if vpp_active else 0.0

    # Total (negative sum = penalty)
    reward = -(energy_penalty + comfort_penalty + vpp_penalty)

    return float(np.clip(reward, -50.0, 50.0))


def _terminal_reward_v2(loop: Any, vpp_event_energy: dict, price_profile: Any) -> float:
    """Episode-end bonus aligned with benchmark metrics, conservative weights."""
    suite = getattr(loop, "appliance_suite", None)
    if suite is None:
        return 0.0
    results = suite.all_results()
    w = REWARD_WEIGHTS_V2

    # Appliance completion bonuses
    washer_days = [d for d in results.get("washer", []) if d.get("present")]
    dishwasher_days = [d for d in results.get("dishwasher", []) if d.get("present")]
    dryer_days = [d for d in results.get("dryer", []) if d.get("present")]
    wh_days = [d for d in results.get("water_heater", []) if d.get("present")]

    washer_app = suite._shiftable.get("washer")
    dishwasher_app = suite._shiftable.get("dishwasher")
    dryer_app = suite._shiftable.get("dryer")
    w_max = (washer_app.latest_h - washer_app.duration_h) if washer_app else 20.0
    d_max = (dishwasher_app.latest_h - dishwasher_app.duration_h) if dishwasher_app else 21.5
    dr_max = (dryer_app.latest_h - dryer_app.duration_h) if dryer_app else 20.5
    washer_ok = sum(
        1 for d in washer_days
        if d.get("scheduled_abs_h") is not None
        and np.isfinite(d["scheduled_abs_h"])
        and (d["scheduled_abs_h"] % 24) <= w_max + 1e-6
    ) / max(1, len(washer_days))
    dishwasher_ok = sum(
        1 for d in dishwasher_days
        if d.get("scheduled_abs_h") is not None
        and np.isfinite(d["scheduled_abs_h"])
        and (d["scheduled_abs_h"] % 24) <= d_max + 1e-6
    ) / max(1, len(dishwasher_days))
    dryer_ok = sum(
        1 for d in dryer_days
        if d.get("scheduled_abs_h") is not None
        and np.isfinite(d["scheduled_abs_h"])
        and (d["scheduled_abs_h"] % 24) <= dr_max + 1e-6
    ) / max(1, len(dryer_days)) if dryer_days else 0.0
    wh_ok = sum(1 for d in wh_days if d.get("ready_at_bath")) / max(1, len(wh_days))

    # VPP avoidance bonus: completed AND not running during VPP
    avoid_count = 0
    total_present = 0
    for name in ("washer", "dishwasher", "dryer"):
        app = suite._shiftable.get(name)
        max_s = (app.latest_h - app.duration_h) if app else 21.5
        for d in results.get(name, []):
            if d.get("present"):
                total_present += 1
                sched = d.get("scheduled_abs_h")
                in_window = (
                    sched is not None and np.isfinite(sched)
                    and (sched % 24) <= max_s + 1e-6
                )
                if in_window and not d.get("ran_during_vpp"):
                    avoid_count += 1
    for d in wh_days:
        if d.get("present"):
            total_present += 1
            if not d.get("ran_during_vpp"):
                avoid_count += 1
    avoidance_bonus = (avoid_count / max(1, total_present)) * w["terminal_vpp_avoid"]

    # VPP energy bonus: reward low VPP window energy
    total_vpp_kwh = sum(float(v) for v in (vpp_event_energy or {}).values())
    vpp_bonus = max(0.0, w["terminal_vpp_energy"] - total_vpp_kwh * 3.0)

    # EV target_reached bonus
    ev_days = [d for d in results.get("ev", []) if d.get("present")]
    ev_ok = sum(1 for d in ev_days if d.get("target_reached")) / max(1, len(ev_days)) if ev_days else 0.0

    bonus = (
        w["terminal_washer"] * washer_ok +
        w["terminal_dishwasher"] * dishwasher_ok +
        w["terminal_dryer"] * dryer_ok +
        w["terminal_wh"] * wh_ok +
        w["terminal_ev"] * ev_ok +
        avoidance_bonus +
        vpp_bonus
    )
    return float(bonus)


# ----- Environment -----------------------------------------------------------
class EnergyPlusFamilyEnvV2(gym.Env):
    """v2 Gymnasium wrapper: expanded action, price-aware, preference-aware."""

    metadata = {"render_modes": []}
    OBSERVATION_NAMES = OBSERVATION_NAMES_V2

    def __init__(self, output_root: str | Path = "/tmp/energybridge_rl_pref_v2",
                 persona_id: str = "basic_role_a_commuter_price_cooperative",
                 city: str = "Tianjin",
                 start_date: str = "",
                 price_csv: str = "",
                 price_profile: Any = None,
                 reward_mode: str = "default") -> None:
        super().__init__()
        self.reward_mode = reward_mode  # "default" or "mpc_aligned_v1"
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

        # Load price profile and wrap with adapter so get_price(sim_h) works
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

        # Resolve EPW from city
        EPW_DIR = PROJECT_ROOT / "experiments" / "weather" / "epw"
        epw_map = {
            "tianjin": EPW_DIR / "CHN_TJ_Tianjin.545270_CSWD.epw",
            "beijing": EPW_DIR / "CHN_BJ_Beijing.545110_CSWD.epw",
            "shanghai": EPW_DIR / "CHN_SH_Shanghai.583620_CSWD.epw",
            "germany": PROJECT_ROOT / "experiments" / "weather" / "epw" / "DEU_Germany_2025_real.epw",
        }
        self._epw_path = epw_map.get(city.lower(), epw_map["tianjin"])

        self._sim_days = 7 if city.lower() == "germany" else 3
        if city.lower() == "germany":
            from datetime import date as _date
            _template_idf = PROJECT_ROOT / "experiments" / "models" / "family_home" / "berlin_family_geg_final.idf"
            _runperiod_start = _date.fromisoformat(start_date) if start_date else _date(2025, 6, 1)
            self._idf_path = generate_runperiod_idf(
                _template_idf, self.output_root,
                start_date=_runperiod_start, days=self._sim_days,
            )
        else:
            self._idf_path = PROJECT_ROOT / "experiments" / "models" / "family_home" / f"family_simple_{self._sim_days}day.idf"

        self.action_space = spaces.Box(low=-1.0, high=1.0, shape=(ACTION_DIM_V2,), dtype=np.float32)
        self.observation_space = spaces.Box(
            low=-np.inf, high=np.inf, shape=(OBS_DIM_V2,), dtype=np.float32
        )
        self._action_queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=1)
        self._packet_queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._episode_dir: Path | None = None
        self.rows: list[dict[str, Any]] = []
        self.final_appliance_results: dict[str, Any] = {}
        self.vpp_event_energy: dict[str, float] = {}
        self.terminal_bonus: float = 0.0

    def reset(self, *, seed: int | None = None, options: dict | None = None):
        super().reset(seed=seed)
        self.close()
        self._stop.clear()
        self.rows = []
        self.vpp_event_energy = {}
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
        return (packet["observation"], packet["reward"], packet["terminated"], False, packet["info"])

    def close(self) -> None:
        self._stop.set()
        try:
            self._action_queue.put_nowait(np.zeros(ACTION_DIM_V2, dtype=np.float32))
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
            VPP_EVENTS = _make_vpp_events(self._sim_days, start_h=18.0, duration_h=1.0)

            api = EnergyPlusAPI()
            state = api.state_manager.new_state()
            api.runtime.set_console_output_status(state, False)
            ex = api.exchange
            loop = _FamilyLoop()
            loop.appliance_suite = ApplianceSuite(
                self.persona.get("appliances", {}), sim_days=self._sim_days,
                vpp_events=VPP_EVENTS, explicit_only=True
            )
            # explicit_only=True also makes WaterHeater.ready_at_bath require energy_kwh > 0.

            ex.request_variable(state, "Zone Mean Air Temperature", "living_unit1")
            ex.request_variable(state, "Facility Total Electricity Demand Rate", "Whole Building")
            ex.request_variable(state, "Site Outdoor Air Drybulb Temperature", "Environment")
            last_sim_h: float | None = None
            last_action = np.array([25.0, 15.5, 16.0, 12.0, 60.0, 21.0, 7.0, 15.5], dtype=np.float32)
            # Decision memory: prevent repeated daily appliance scheduling
            daily_appliance_set: dict[int, set] = {d: set() for d in range(self._sim_days)}
            first_packet_sent = False

            def callback(s) -> None:
                nonlocal last_sim_h, last_action, first_packet_sent
                if self._stop.is_set() or not loop.init(ex, s):
                    return
                # Ignore EnergyPlus sizing / design-day environments (kind_of_sim != 3).
                # Berlin IDF has design days that would otherwise corrupt loop.start_day
                # and produce negative sim_h. Matches family_runner's a30055f fix.
                if not _is_weather_run_period(ex, s):
                    return
                if loop.h_out == -1:
                    loop.h_out = ex.get_variable_handle(s, "Site Outdoor Air Drybulb Temperature", "Environment")
                if ex.warmup_flag(s):
                    return
                day = ex.day_of_year(s)
                if loop.start_day is None:
                    loop.start_day = day
                sim_h = (day - loop.start_day) * 24.0 + ex.current_time(s)
                if sim_h >= self._sim_days * 24.0:
                    return
                dt = float(ex.zone_time_step(s))
                temp = float(ex.get_variable_value(s, loop.h_temp))
                facility_kw = max(0.0, float(ex.get_variable_value(s, loop.h_fac)) / 1000.0)
                outdoor = float(ex.get_variable_value(s, loop.h_out)) if loop.h_out != -1 else 30.0
                vpp_active = any(ev["trigger_h"] <= sim_h < ev["end_h"] for ev in VPP_EVENTS)

                # Accumulate VPP event energy
                for ev in VPP_EVENTS:
                    if ev["trigger_h"] <= sim_h < ev["end_h"]:
                        eid = ev["id"]
                        self.vpp_event_energy[eid] = self.vpp_event_energy.get(eid, 0.0) + facility_kw * dt

                pfeat = _price_features(self.price_profile, sim_h)

                if last_sim_h is not None:
                    energy_kwh = facility_kw * dt
                    occupied = 8.0 <= sim_h % 24.0 < 22.0
                    if self.reward_mode == "mpc_aligned_v1":
                        reward, cost_dict = _compute_reward_mpc_aligned(
                            setpoint_c=float(last_action[0]), indoor_c=temp,
                            outdoor_c=outdoor, occupied=occupied,
                            vpp_active=vpp_active,
                            price_current=pfeat["current"], dt_h=dt,
                            appliance_config=self.persona.get("appliances", {}),
                            appliance_actions=None,  # updated below after decode
                            day_idx=min(self._sim_days - 1, int(sim_h // 24)),
                            vpp_target_kw=2.0 / 1.0 if vpp_active else None,
                        )
                    else:
                        reward = _compute_reward_v2(
                            energy_kwh=energy_kwh, temp_c=temp, sim_h=sim_h,
                            vpp_active=vpp_active,
                            vpp_window_kwh_accum=sum(self.vpp_event_energy.values()),
                            comfort_min=self._comfort_min, comfort_max=self._comfort_max,
                            pref_weight=self.pref_proxy["comfort_weight"],
                            comfort_weight=self.pref_proxy["comfort_weight"],
                            price_sensitivity=self.pref_proxy["price_sensitivity"],
                            vpp_cooperation=self.pref_proxy["vpp_cooperation"],
                            price_current=pfeat["current"],
                            appliance_results=None,
                            vpp_event_energy=self.vpp_event_energy,
                        )
                else:
                    energy_kwh = 0.0
                    reward = 0.0
                    cost_dict = {}

                capacity = assess_suite_vpp_request(loop.appliance_suite, sim_h, 2.0, 60.0)
                assessment = capacity["assessment"]
                observation = self._observation(sim_h, temp, outdoor, vpp_active, assessment, loop, pfeat)
                row = {
                    "sim_hour": sim_h, "indoor_temperature_c": temp, "outdoor_temperature_c": outdoor,
                    "facility_power_kw": facility_kw, "energy_kwh": energy_kwh,
                    "cooling_setpoint_c": float(last_action[0]), "vpp_active": int(vpp_active),
                    "pmv": _compute_pmv(temp),
                    "washer_start_request": float(last_action[1]),
                    "dishwasher_start_request": float(last_action[2]),
                    "water_heater_preheat_request": float(last_action[3]),
                    "dryer_start_request": float(last_action[7]),
                    "reward": reward,
                }
                if last_sim_h is not None:
                    self.rows.append(row)
                self._packet_queue.put({
                    "observation": observation, "reward": reward, "terminated": False,
                    "info": {"energyplus_family": row},
                })
                first_packet_sent = True
                try:
                    raw_action = self._action_queue.get(timeout=120)
                except queue.Empty:
                    self._stop.set()
                    return
                decoded = _decode_action_v2(raw_action)
                last_action = decoded
                loop.sp = float(np.clip(decoded[0], 22.0, 28.0))
                day_idx = min(self._sim_days - 1, int(sim_h // 24))
                hod = sim_h % 24.0

                # Apply appliance actions with cooldown (once per day per device)
                if day_idx not in daily_appliance_set:
                    daily_appliance_set[day_idx] = set()
                done_today = daily_appliance_set[day_idx]

                # Washer — skip during VPP window to match adapter behavior
                if "washer" not in done_today and float(decoded[1]) >= 8.0 and not vpp_active:
                    target_abs_h = day_idx * 24.0 + float(decoded[1])
                    loop.appliance_suite.shift_appliance("washer", day_idx, target_abs_h)
                    done_today.add("washer")
                # Dishwasher — skip during VPP window to match adapter behavior
                if "dishwasher" not in done_today and float(decoded[2]) >= 9.0 and not vpp_active:
                    target_abs_h = day_idx * 24.0 + float(decoded[2])
                    loop.appliance_suite.shift_appliance("dishwasher", day_idx, target_abs_h)
                    done_today.add("dishwasher")
                # Dryer — skip during VPP window to match adapter behavior
                if "dryer" not in done_today and float(decoded[7]) >= 8.0 and not vpp_active:
                    target_abs_h = day_idx * 24.0 + float(decoded[7])
                    loop.appliance_suite.shift_appliance("dryer", day_idx, target_abs_h)
                    done_today.add("dryer")
                # Water heater preheat
                if "water_heater" not in done_today:
                    wh_start = float(np.clip(decoded[3], 7.0, 17.0))
                    wh_end = min(18.0, wh_start + 3.0)
                    loop.appliance_suite.set_ewh_preheat_schedule(
                        day_idx, start_h=wh_start,
                        end_h=wh_end,
                        temp_c=float(np.clip(decoded[4], 45.0, 75.0)),
                    )
                    done_today.add("water_heater")
                if "ev" not in done_today:
                    ev_dev = loop.appliance_suite._ev
                    ev_arrival = float(ev_dev.arrival_h) if ev_dev else 18.5
                    ev_depart = float(ev_dev.departure_h) if ev_dev else 7.5
                    ev_start = float(np.clip(decoded[5], ev_arrival, 20.0))
                    ev_end = float(np.clip(decoded[6], 4.0, 7.5))
                    loop.appliance_suite.set_ev_mode(day_idx, "smart")
                    loop.appliance_suite.set_ev_charge_window(day_idx, start_h=ev_start, end_h=ev_end)
                    done_today.add("ev")
                powers = loop.appliance_suite.step(sim_h, dt)
                self._write_actuators(ex, s, loop, powers, sim_h)
                last_sim_h = sim_h

            api.runtime.callback_end_system_timestep_after_hvac_reporting(state, callback)
            assert self._episode_dir is not None
            self._episode_dir.mkdir(parents=True, exist_ok=True)
            exit_code = api.runtime.run_energyplus(
                state, ["-w", str(self._epw_path), "-d", str(self._episode_dir), str(self._idf_path)],
            )
            self.final_appliance_results = loop.appliance_suite.all_results()
            api.state_manager.delete_state(state)
            if first_packet_sent:
                final_obs = np.zeros(self.observation_space.shape, dtype=np.float32)
                if self.reward_mode == "mpc_aligned_v1":
                    terminal_bonus, _ = _terminal_penalty_mpc_aligned(
                        self.persona.get("appliances", {}), loop.appliance_suite,
                        vpp_event_energy=self.vpp_event_energy,
                    )
                else:
                    terminal_bonus = _terminal_reward_v2(loop, self.vpp_event_energy, self.price_profile)
                self.terminal_bonus = float(terminal_bonus)
                self._packet_queue.put({
                    "observation": final_obs, "reward": terminal_bonus,
                    "terminated": True, "info": {"energyplus_family": {"exit_code": exit_code}},
                })
        except Exception as exc:
            self._packet_queue.put({"error": repr(exc)})

    def _observation(self, sim_h: float, temp: float, outdoor: float,
                     vpp_active: bool, assessment: dict, loop: Any,
                     pfeat: dict | None = None) -> np.ndarray:
        hour = sim_h % 24.0
        day_idx = min(self._sim_days - 1, int(sim_h // 24))
        suite = loop.appliance_suite
        pfeat = pfeat or {}
        vpp_target_kwh = max(0.1, 2.0 - float(assessment.get("recommended_bid_kw", 0.0))) if vpp_active else 0.0

        # Time to next VPP start
        next_vpp = None
        for d in range(self._sim_days):
            start = d * 24.0 + 18.0
            if sim_h < start:
                next_vpp = start
                break
        time_to_vpp = max(0.0, (next_vpp - sim_h) / 24.0) if next_vpp else 0.0

        capacity_values = (
            [float(assessment.get("committable_kw", 0.0)) / 2.0,
             float(assessment.get("recommended_bid_kw", 0.0)) / 2.0]
            if vpp_active else [0.0, 0.0]
        )

        values = [
            np.sin(2 * np.pi * hour / 24.0), np.cos(2 * np.pi * hour / 24.0),
            float(day_idx) / 7.0, time_to_vpp,  # normalize over max 7-day range; consistent with adapter
            temp / 40.0, outdoor / 45.0,
            float(getattr(loop, "sp", 25.0)) / 30.0,
            float(8.0 <= hour < 22.0),
            float(vpp_active), vpp_target_kwh / 2.0,
            *capacity_values,
            # Price
            float(pfeat.get("current", 0.0)) / 0.3,
            float(pfeat.get("next6h_mean", 0.0)) / 0.3,
            float(pfeat.get("next6h_max", 0.0)) / 0.3,
            float(pfeat.get("next6h_min", 0.0)) / 0.3,
            float(pfeat.get("peak", 0.0)),
            # User preference proxy
            self.pref_proxy["comfort_weight"],
            self.pref_proxy["price_sensitivity"],
            self.pref_proxy["flexibility"],
            self.pref_proxy["vpp_cooperation"],
        ]
        for name in ("washer", "dishwasher"):
            values.extend(self._shiftable_obs(suite._shiftable[name], day_idx))
        values.extend(self._wh_obs(suite._water_heater, day_idx))
        values.extend([
            float(suite._ev.present), float(suite._ev._soc), float(suite._ev._is_home(hour)),
        ])
        values.extend([
            float(suite._refrigerator.present), float(suite._refrigerator.power_kw) / 2.0,
        ])
        obs = np.asarray(values, dtype=np.float32)
        if obs.shape != (OBS_DIM_V2,):
            raise RuntimeError(f"Obs shape mismatch: {obs.shape} != {OBS_DIM_V2}")
        return obs

    @staticmethod
    def _shiftable_obs(appliance: Any, day_idx: int) -> list[float]:
        record = appliance._days.get(day_idx)
        skipped = appliance._day_skipped.get(day_idx, False)
        state = 0.0 if (not appliance.present or skipped) else (
            3.0 if (record is not None and record.completed) else (
                2.0 if (record is not None and record.run_start_abs_h is not None) else 1.0))
        scheduled_h = (record.scheduled_abs_h % 24.0 if record is not None and record.scheduled_abs_h is not None and np.isfinite(record.scheduled_abs_h) else -24.0)
        return [
            float(appliance.present), state / 3.0, scheduled_h / 24.0,
            float(appliance.earliest_h) / 24.0, float(appliance.latest_h) / 24.0,
        ]

    @staticmethod
    def _wh_obs(water_heater: Any, day_idx: int) -> list[float]:
        state = water_heater._days.get(day_idx, {})
        start = state.get("preheat_start_h") or water_heater.pre_heat_window_start_h
        end = state.get("preheat_end_h") or water_heater.pre_heat_window_end_h
        return [
            float(water_heater.present), float(state.get("preheat_requested", False)),
            float(start) / 24.0, float(end) / 24.0, float(water_heater.bath_required_h) / 24.0,
        ]

    @staticmethod
    def _write_actuators(ex, state, loop: Any, powers: dict, sim_h: float) -> None:
        ex.set_actuator_value(state, loop.h_cool, loop.sp)
        ex.set_actuator_value(state, loop.h_heat, 20.0)
        for name, handle, design_kw in (
            ("washer", loop.h_washer, 2.0), ("dishwasher", loop.h_dishwasher, 1.5),
            ("dryer", loop.h_dryer, 3.0), ("refrigerator", loop.h_refrigerator, 0.2),
            ("ev", loop.h_ev, 7.0),
        ):
            if handle != -1:
                ex.set_actuator_value(state, handle, min(1.0, float(powers.get(name, 0.0)) / design_kw))
        if loop.h_ewh_sp != -1:
            ex.set_actuator_value(state, loop.h_ewh_sp, 65.0 if float(powers.get("water_heater", 0.0)) > 0 else 40.0)


# Module-level aliases for imports used by benchmark wrapper
_shiftable_obs = EnergyPlusFamilyEnvV2._shiftable_obs
_wh_obs = EnergyPlusFamilyEnvV2._wh_obs
