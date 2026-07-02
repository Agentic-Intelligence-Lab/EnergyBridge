"""PPO v2 policy adapter for the EnergyBridge benchmark interface.

Key differences from rl_ppo_3day:
- Action: 8 dims (AC, washer, dishwasher, WH start, WH temp, EV start, EV end, dryer)
- Observation: 41 dims with price + preference proxy
- Decision cooldown: once-per-day appliance scheduling
- Model: ENERGYBRIDGE_RL_PREF_V2_MODEL env var
- Debug: ENERGYBRIDGE_RL_PREF_V2_DEBUG_POLICY=fixed bypasses PPO
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[3]
# Ensure baselines.rl_energyplus is importable when adapter is loaded
# from the benchmark runner (which sets sys.path[0] to experiments/benchmark
# only, not the project root). Without this, build_observation's delayed
# import fails inside the EnergyPlus ctypes callback and the exception is
# silently swallowed — leading to "no emitted policy actions" benchmarks.
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
METHOD_ID = "rl_ppo_pref_v2"
OBJECTIVE_SOURCE = "rl_ppo_pref_v2_policy"
MODEL_ENV_VAR = "ENERGYBRIDGE_RL_PREF_V2_MODEL"
DEBUG_ENV_VAR = "ENERGYBRIDGE_RL_PREF_V2_DEBUG_POLICY"
DEFAULT_MODEL_CANDIDATES = (
    PROJECT_ROOT / "benchmark_results" / "rl_ppo_pref_v2_1h_fixed" / "ppo_energyplus_3day.zip",
    PROJECT_ROOT / "benchmark_results" / "rl_ppo_pref_v2_1h" / "ppo_energyplus_3day.zip",
    PROJECT_ROOT / "benchmark_results" / "rl_ppo_pref_v2_smoke_fixed" / "ppo_energyplus_3day.zip",
    PROJECT_ROOT / "benchmark_results" / "rl_ppo_pref_v2_smoke" / "ppo_energyplus_3day.zip",
    PROJECT_ROOT / "benchmark_results" / "rl_ppo_pref_v2_4h" / "ppo_energyplus_3day.zip",
)
DECISION_INTERVAL_H = 1.0 / 6.0
OBS_DIM = 41  # must match environment_pref_v2.OBS_DIM_V2 (41; obs unchanged for 8-dim action)

_MODEL_CACHE: dict[tuple[Path, str], Any] = {}


def _debug_mode() -> str:
    return os.environ.get(DEBUG_ENV_VAR, "").strip().lower()


def resolve_model_path() -> Path:
    configured = os.environ.get(MODEL_ENV_VAR, "").strip()
    if configured:
        path = Path(configured)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        if path.exists():
            return path
        raise FileNotFoundError(f"RL Pref-v2 model not found: {path}. Set {MODEL_ENV_VAR}.")
    for candidate in DEFAULT_MODEL_CANDIDATES:
        if candidate.exists():
            return candidate
    checked = "\n  ".join(str(p) for p in DEFAULT_MODEL_CANDIDATES)
    raise FileNotFoundError(f"No RL Pref-v2 model found. Set {MODEL_ENV_VAR}. Checked:\n  {checked}")


def load_policy(model_path: str | Path | None = None, *, device: str = "cpu") -> Any:
    if _debug_mode() in ("fixed", "1", "true", "yes"):
        return None
    path = Path(model_path) if model_path else resolve_model_path()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    key = (path.resolve(), device)
    if key not in _MODEL_CACHE:
        from stable_baselines3 import PPO
        _MODEL_CACHE[key] = PPO.load(path, device=device, print_system_info=False)
    return _MODEL_CACHE[key]


def decode_action(action: np.ndarray) -> np.ndarray:
    a = np.clip(np.asarray(action, dtype=np.float32), -1.0, 1.0)
    out = np.array([
        25.0 + 3.0 * a[0],          # dim0: AC setpoint [22, 28]
        13.5 + 5.5 * a[1],          # dim1: washer start [8, 19]
        20.25 + 1.25 * a[2],        # dim2: dishwasher start [19, 21.5]
        12.0 + 5.0 * a[3],          # dim3: WH preheat start [7, 17]
        60.0 + 15.0 * a[4],         # dim4: WH temp [45, 75]
        19.25 + 0.75 * a[5],        # dim5: EV charge start [18.5, 20.0]
        5.75 + 1.75 * a[6],         # dim6: EV charge end [4.0, 7.5]
        13.75 + 5.75 * a[7],        # dim7: dryer start [8, 19.5]
    ], dtype=np.float32)
    return out


def _fixed_debug_action(vpp_active: bool, sim_h: float) -> np.ndarray:
    """Deterministic sensible policy for sanity testing (8-dim).

    AC: 25.5°C normal, 26.0°C in VPP.
    Washer: 19:00. Dishwasher: 21:00. Dryer: 19:00.
    WH: start=14h, end=17h, temp=68°C.
    EV: start=20h, end=7h (overnight), mode=smart.
    """
    sp = 26.0 if vpp_active else 25.5
    return np.array([sp, 19.0, 21.0, 14.0, 68.0, 20.0, 7.0, 19.0], dtype=np.float32)


def build_observation(
    *, loop: Any, sim_h: float, temp_c: float, outdoor_temp_c: float,
    vpp_active: bool, assessment: dict[str, Any] | None,
    price_profile: Any = None,
    pref_proxy: dict[str, float] | None = None,
) -> np.ndarray:
    from baselines.rl_energyplus.environment_pref_v2 import (
        _price_features, _shiftable_obs, _wh_obs,
    )

    hour = float(sim_h) % 24.0
    suite = loop.appliance_suite
    # Read sim_days from ApplianceSuite so adapter matches env's time_to_vpp /
    # day_idx scope. Fallback 7 only when suite is missing (defensive).
    sim_days = int(getattr(suite, "_sim_days", 7))
    day_idx = min(sim_days - 1, int(float(sim_h) // 24))
    assessment = assessment or {}
    # Wrap raw DayAheadPriceProfile with adapter if needed (benchmark runner
    # passes the profile directly without get_price; training env wraps it).
    if price_profile is not None and not hasattr(price_profile, "get_price"):
        from baselines.rl_energyplus.environment_pref_v2 import _PriceProfileAdapter
        _base_date = ""
        if hasattr(price_profile, "points") and price_profile.points:
            _base_date = price_profile.points[0].local_time.strftime("%Y-%m-%d")
        elif hasattr(price_profile, "recurring_hour_prices") and price_profile.recurring_hour_prices:
            _base_date = "2025-06-01"  # TOU profile: date doesn't matter, hour-of-day used
        price_profile = _PriceProfileAdapter(price_profile, _base_date)
    pfeat = _price_features(price_profile, sim_h) if price_profile else {}
    pfeat = pfeat or {}
    pref = pref_proxy or {"comfort_weight": 0.35, "price_sensitivity": 0.5, "flexibility": 0.5, "vpp_cooperation": 0.7}

    vpp_target_kwh = max(0.1, 2.0 - float(assessment.get("recommended_bid_kw", 0.0))) if vpp_active else 0.0
    next_vpp = None
    for d in range(sim_days):  # match env's range(self._sim_days)
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
        float(day_idx) / 7.0, time_to_vpp,  # normalize to max 7-day Germany range
        float(temp_c) / 40.0, float(outdoor_temp_c) / 45.0,
        float(getattr(loop, "sp", 25.0)) / 30.0,
        float(8.0 <= hour < 22.0),
        float(vpp_active), vpp_target_kwh / 2.0,
        *capacity_values,
        float(pfeat.get("current", 0.0)) / 0.3,
        float(pfeat.get("next6h_mean", 0.0)) / 0.3,
        float(pfeat.get("next6h_max", 0.0)) / 0.3,
        float(pfeat.get("next6h_min", 0.0)) / 0.3,
        float(pfeat.get("peak", 0.0)),
        pref["comfort_weight"], pref["price_sensitivity"],
        pref["flexibility"], pref["vpp_cooperation"],
    ]
    for name in ("washer", "dishwasher"):
        values.extend(_shiftable_obs(suite._shiftable[name], day_idx))
    values.extend(_wh_obs(suite._water_heater, day_idx))
    values.extend([
        float(suite._ev.present), float(suite._ev._soc),
        float(suite._ev._is_home(hour)),
    ])
    values.extend([
        float(suite._refrigerator.present), float(suite._refrigerator.power_kw) / 2.0,
    ])
    obs = np.asarray(values, dtype=np.float32)
    if obs.shape != (OBS_DIM,):
        raise RuntimeError(f"Obs shape mismatch: {obs.shape} != {OBS_DIM}")
    return obs


def predict_control_result(
    *, loop: Any, sim_h: float, temp_c: float, outdoor_temp_c: float,
    vpp_active: bool, assessment: dict[str, Any] | None,
    appliance_config: dict[str, Any] | None, base_actions: dict[str, Any],
    vpp_event: dict[str, Any] | None = None,
    price_profile: Any = None,
    pref_proxy: dict[str, float] | None = None,
    daily_scheduled: dict[int, set] | None = None,
    model_path: str | Path | None = None, device: str = "cpu",
) -> dict[str, Any]:
    debug_mode = _debug_mode()

    if debug_mode in ("fixed", "1", "true", "yes"):
        # Deterministic fixed policy — bypass PPO entirely
        action_decoded = _fixed_debug_action(vpp_active, sim_h)
        print(f"  [RL Pref-v2 DEBUG=fixed] h={sim_h:.2f} vpp={vpp_active} "
              f"sp={action_decoded[0]:.1f}°C")
        return action_to_control_result_decoded(
            action_decoded, sim_h=sim_h, appliance_config=appliance_config,
            base_actions=base_actions, vpp_event=vpp_event,
            daily_scheduled=daily_scheduled, source="fixed_debug_policy",
        )

    model = load_policy(model_path, device=device)
    observation = build_observation(
        loop=loop, sim_h=sim_h, temp_c=temp_c, outdoor_temp_c=outdoor_temp_c,
        vpp_active=vpp_active, assessment=assessment,
        price_profile=price_profile, pref_proxy=pref_proxy,
    )
    action, _ = model.predict(observation, deterministic=True)
    action_decoded = decode_action(action)
    print(f"  [RL Pref-v2 PPO] h={sim_h:.2f} vpp={vpp_active} "
          f"raw={np.round(action, 3)} sp={action_decoded[0]:.1f}°C")
    return action_to_control_result_decoded(
        action_decoded, sim_h=sim_h, appliance_config=appliance_config,
        base_actions=base_actions, vpp_event=vpp_event,
        daily_scheduled=daily_scheduled, source="ppo_policy",
    )


def action_to_control_result(
    action: np.ndarray, *, sim_h: float,
    appliance_config: dict[str, Any] | None,
    base_actions: dict[str, Any],
    vpp_event: dict[str, Any] | None = None,
    daily_scheduled: dict[int, set] | None = None,
) -> dict[str, Any]:
    """Legacy wrapper: decode action then dispatch."""
    decoded = decode_action(action)
    return action_to_control_result_decoded(
        decoded, sim_h=sim_h, appliance_config=appliance_config,
        base_actions=base_actions, vpp_event=vpp_event,
        daily_scheduled=daily_scheduled, source="ppo_policy",
    )


def _policy_summary(
    decoded: np.ndarray,
    actions: dict[str, Any],
    present: set[str],
    *,
    sim_h: float,
    vpp_active: bool,
    source: str,
) -> str:
    hod = float(sim_h) % 24.0
    parts = [
        f"RL PPO Pref-v2 raw policy ({source}); no fallback appliance commands were added.",
        f"AC setpoint={float(decoded[0]):.1f}C at h={hod:.2f}.",
    ]
    if "washer" in present:
        if "washer_start_h" in actions:
            parts.append(f"Washer emitted start_h={float(actions['washer_start_h']):.1f}.")
        else:
            parts.append(
                f"Washer raw candidate={float(np.clip(float(decoded[1]), 8.0, 19.0)):.1f}h, "
                "not emitted."
            )
    if "dishwasher" in present:
        if "dishwasher_start_h" in actions:
            parts.append(f"Dishwasher emitted start_h={float(actions['dishwasher_start_h']):.1f}.")
        else:
            parts.append(
                f"Dishwasher raw candidate={float(np.clip(float(decoded[2]), 19.0, 21.5)):.1f}h, "
                "not emitted."
            )
    if "dryer" in present:
        if "dryer_start_h" in actions:
            parts.append(f"Dryer emitted start_h={float(actions['dryer_start_h']):.1f}.")
        else:
            _d7 = float(decoded[7]) if len(decoded) > 7 else 15.5
            parts.append(
                f"Dryer raw candidate={float(np.clip(_d7, 8.0, 19.5)):.1f}h, "
                "not emitted."
            )
    if "water_heater" in present:
        if actions.get("water_heater_preheat"):
            parts.append(
                "Water heater emitted preheat "
                f"{float(actions['water_heater_preheat_start_h']):.1f}-"
                f"{float(actions['water_heater_preheat_end_h']):.1f}h @ "
                f"{float(actions['water_heater_preheat_temp_c']):.1f}C."
            )
        else:
            parts.append(
                f"Water heater raw flag={float(decoded[3]):.2f}, "
                f"temp={float(np.clip(float(decoded[4]), 45.0, 75.0)):.1f}C, not emitted."
            )
    unsupported = sorted(present.difference({"washer", "dishwasher", "dryer", "water_heater", "ev"}))
    if unsupported:
        parts.append("No raw RL action dimension for " + ", ".join(unsupported) + ".")
    if vpp_active:
        parts.append("Decision occurred inside a VPP window; shift/preheat actions are gated off.")
    return " ".join(parts)


def action_to_control_result_decoded(
    decoded: np.ndarray, *, sim_h: float,
    appliance_config: dict[str, Any] | None,
    base_actions: dict[str, Any],
    vpp_event: dict[str, Any] | None = None,
    daily_scheduled: dict[int, set] | None = None,
    source: str = "ppo_policy",
) -> dict[str, Any]:
    """Core logic: turn physical action values into benchmark control result."""
    # Keep RL benchmark actions policy-only.  The runner passes an empty dict,
    # and this adapter deliberately ignores any fallback/base actions from
    # legacy call sites.
    actions: dict[str, Any] = {}
    hod = float(sim_h) % 24.0
    day_idx = min(6, int(float(sim_h) // 24))  # up to 7-day Germany benchmark
    vpp_active = bool(vpp_event and float(vpp_event.get("trigger_h", 0)) <= sim_h < float(vpp_event.get("end_h", 0)))
    present = _present_controllable(appliance_config)
    ds = daily_scheduled if daily_scheduled is not None else {}

    # AC setpoint — always applied
    sp = round(float(np.clip(float(decoded[0]), 22.0, 28.0)), 1)

    if "washer" in present and "washer" not in ds.get(day_idx, set()):
        cfg_w = (appliance_config or {}).get("washer", {}) or {}
        dr_adj_w = bool(cfg_w.get("dr_adjustable", True))
        proposed = float(np.clip(float(decoded[1]), 8.0, 19.0)) if dr_adj_w else float(cfg_w.get("preferred_h", 14.0))
        if not vpp_active:
            actions["washer_start_h"] = round(proposed, 3)
            actions["washer_skip"] = False
            ds.setdefault(day_idx, set()).add("washer")
            print(f"  [RL Pref-v2] day={day_idx} washer@{proposed:.1f}h (dr_adj={dr_adj_w})")

    if "dishwasher" in present and "dishwasher" not in ds.get(day_idx, set()):
        cfg_d = (appliance_config or {}).get("dishwasher", {}) or {}
        dr_adj_d = bool(cfg_d.get("dr_adjustable", True))
        proposed = float(np.clip(float(decoded[2]), 19.0, 21.5)) if dr_adj_d else float(cfg_d.get("preferred_h", 14.0))
        if not vpp_active:
            actions["dishwasher_start_h"] = round(proposed, 3)
            actions["dishwasher_skip"] = False
            ds.setdefault(day_idx, set()).add("dishwasher")
            print(f"  [RL Pref-v2] day={day_idx} dishwasher@{proposed:.1f}h (dr_adj={dr_adj_d})")

    if "dryer" in present and "dryer" not in ds.get(day_idx, set()):
        cfg_dr = (appliance_config or {}).get("dryer", {}) or {}
        dr_adj_dr = bool(cfg_dr.get("dr_adjustable", True))
        proposed = float(np.clip(float(decoded[7]) if len(decoded) > 7 else 15.5, 8.0, 19.5)) if dr_adj_dr else float(cfg_dr.get("preferred_h", 15.0))
        if not vpp_active:
            actions["dryer_start_h"] = round(proposed, 3)
            actions["dryer_skip"] = False
            ds.setdefault(day_idx, set()).add("dryer")
            print(f"  [RL Pref-v2] day={day_idx} dryer@{proposed:.1f}h (dr_adj={dr_adj_dr})")

    # Water heater: always emit preheat; PPO controls start/temp (dim3/4); end=start+3h
    if "water_heater" in present and "water_heater" not in ds.get(day_idx, set()):
        start_h = float(np.clip(float(decoded[3]), 7.0, 17.0))
        end_h = min(18.0, start_h + 3.0)
        wh_temp = float(np.clip(float(decoded[4]), 45.0, 75.0))
        actions.update({
            "water_heater_preheat": True,
            "water_heater_preheat_start_h": round(start_h, 3),
            "water_heater_preheat_end_h": round(end_h, 3),
            "water_heater_preheat_temp_c": round(wh_temp, 1),
        })
        ds.setdefault(day_idx, set()).add("water_heater")
        print(f"  [RL Pref-v2] day={day_idx} WH {start_h:.1f}-{end_h:.1f}h @ {wh_temp:.0f}C")

    # EV: PPO controls charge window (dim5/6); mode fixed=smart
    if "ev" in present and "ev" not in ds.get(day_idx, set()):
        ev_cfg = (appliance_config or {}).get("ev", {}) or {}
        ev_arrival = float(ev_cfg.get("arrival_h", 18.5))
        ev_depart = float(ev_cfg.get("departure_h", 7.5))
        ev_start_raw = float(decoded[5]) if len(decoded) > 5 else 21.0
        ev_end_raw = float(decoded[6]) if len(decoded) > 6 else 7.0
        ev_start = float(np.clip(ev_start_raw, ev_arrival, 20.0))
        ev_end = float(np.clip(ev_end_raw, 4.0, 7.5))
        actions["ev_mode"] = "smart"
        actions["ev_charge_start_h"] = round(ev_start, 3)
        actions["ev_charge_end_h"] = round(ev_end, 3)
        ds.setdefault(day_idx, set()).add("ev")
        print(f"  [RL Pref-v2] day={day_idx} EV {ev_start:.1f}-{ev_end:.1f}h mode=smart")

    return {
        "setpoint": sp,
        "next_check_hour": float(sim_h) + DECISION_INTERVAL_H,
        "reason": _policy_summary(
            decoded,
            actions,
            present,
            sim_h=sim_h,
            vpp_active=vpp_active,
            source=source,
        ),
        "appliance_actions": actions,
        "objective_source": OBJECTIVE_SOURCE,
        "model_path": str(resolve_model_path()) if _debug_mode() not in ("fixed", "1", "true", "yes") else "debug_fixed",
    }


def _present_controllable(appliance_config: dict[str, Any] | None) -> set[str]:
    """Return present non-AC appliances (any dr_adjustable).

    dr_adjustable=False means "do not shift this device for VPP benefit",
    NOT "do not emit any command". The runner still requires routine-preserving
    commands for these devices (see family_runner.py:898-901). Matches agent
    and MPC behavior; the dr_adjustable routing is done downstream in
    action_to_control_result_decoded.
    """
    cfg = appliance_config or {}
    present: set[str] = set()
    for name in ("washer", "dishwasher", "dryer", "water_heater", "ev"):
        if (cfg.get(name) or {}).get("present"):
            present.add(name)
    return present
