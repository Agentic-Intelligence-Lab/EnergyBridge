"""PPO v2 policy adapter for the EnergyBridge benchmark interface.

Key differences from rl_ppo_3day:
- Action: 5 dims (AC, washer, dishwasher, WH flag, WH temp) vs 3
- Observation: 41 dims with price + preference proxy
- Decision cooldown: once-per-day appliance scheduling
- Model: ENERGYBRIDGE_RL_PREF_V2_MODEL env var
- Debug: ENERGYBRIDGE_RL_PREF_V2_DEBUG_POLICY=fixed bypasses PPO
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[3]
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
OBS_DIM = 42  # must match environment_pref_v2.OBS_DIM_V2 (42 after v2.1)

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
        15.5 + 7.5 * a[1],          # dim1: washer start [8, 23]
        16.0 + 7.0 * a[2],          # dim2: dishwasher start [9, 23]
        12.0 + 5.0 * a[3],          # dim3: WH preheat start [7, 17]
        60.0 + 15.0 * a[4],         # dim4: WH temp [45, 75]
        21.0 + 9.0 * a[5],          # dim5: EV charge start [12, 30]
        7.0 + 7.0 * a[6],           # dim6: EV charge end [0, 14]
    ], dtype=np.float32)
    return out


def _fixed_debug_action(vpp_active: bool, sim_h: float) -> np.ndarray:
    """Deterministic sensible policy for sanity testing (7-dim).

    AC: 25.5°C normal, 26.0°C in VPP.
    Washer: 19:00. Dishwasher: 21:00.
    WH: start=14h, end=17h, temp=68°C.
    EV: start=22h, end=7h (overnight), mode=smart.
    """
    sp = 26.0 if vpp_active else 25.5
    return np.array([sp, 19.0, 21.0, 14.0, 68.0, 21.0, 7.0], dtype=np.float32)


def build_observation(
    *, loop: Any, sim_h: float, temp_c: float, outdoor_temp_c: float,
    vpp_active: bool, assessment: dict[str, Any] | None,
    price_profile: Any = None,
    pref_proxy: dict[str, float] | None = None,
) -> np.ndarray:
    from baselines.rl_energyplus_3day.environment_pref_v2 import (
        _price_features, _shiftable_obs, _wh_obs,
    )

    hour = float(sim_h) % 24.0
    day_idx = min(2, int(float(sim_h) // 24))
    suite = loop.appliance_suite
    assessment = assessment or {}
    pfeat = _price_features(price_profile, sim_h) if price_profile else {}
    pfeat = pfeat or {}
    pref = pref_proxy or {"comfort_weight": 0.35, "price_sensitivity": 0.5, "flexibility": 0.5, "vpp_cooperation": 0.7}

    vpp_target_kwh = max(0.1, 2.0 - float(assessment.get("recommended_bid_kw", 0.0))) if vpp_active else 0.0
    next_vpp = None
    for d in range(3):
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
        float(day_idx) / 3.0, time_to_vpp,
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
    # EV target reached today (new dim 41)
    ev_day_result = suite._ev.day_result(day_idx)
    values.append(float(ev_day_result.get("target_reached", False)))
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
                f"Washer raw candidate={float(np.clip(float(decoded[1]), 8.0, 23.0)):.1f}h, "
                "not emitted."
            )
    if "dishwasher" in present:
        if "dishwasher_start_h" in actions:
            parts.append(f"Dishwasher emitted start_h={float(actions['dishwasher_start_h']):.1f}.")
        else:
            parts.append(
                f"Dishwasher raw candidate={float(np.clip(float(decoded[2]), 9.0, 23.0)):.1f}h, "
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
    unsupported = sorted(present.difference({"washer", "dishwasher", "water_heater", "ev"}))
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
    day_idx = min(2, int(float(sim_h) // 24))
    vpp_active = bool(vpp_event and float(vpp_event.get("trigger_h", 0)) <= sim_h < float(vpp_event.get("end_h", 0)))
    present = _present_controllable(appliance_config)
    ds = daily_scheduled if daily_scheduled is not None else {}

    # AC setpoint — always applied
    sp = round(float(np.clip(float(decoded[0]), 22.0, 28.0)), 1)

    # Washer: PPO decides start time once per day; skip during VPP window
    if "washer" in present and "washer" not in ds.get(day_idx, set()):
        proposed = float(np.clip(float(decoded[1]), 8.0, 23.0))
        if not vpp_active:
            actions["washer_start_h"] = round(proposed, 3)
            actions["washer_skip"] = False
            ds.setdefault(day_idx, set()).add("washer")
            print(f"  [RL Pref-v2] day={day_idx} washer@{proposed:.1f}h")

    # Dishwasher: PPO decides start time once per day; skip during VPP window
    if "dishwasher" in present and "dishwasher" not in ds.get(day_idx, set()):
        proposed = float(np.clip(float(decoded[2]), 9.0, 23.0))
        if not vpp_active:
            actions["dishwasher_start_h"] = round(proposed, 3)
            actions["dishwasher_skip"] = False
            ds.setdefault(day_idx, set()).add("dishwasher")
            print(f"  [RL Pref-v2] day={day_idx} dishwasher@{proposed:.1f}h")

    # Water heater: always emit preheat; PPO controls start/temp (dim3/4); end=start+3h
    if "water_heater" in present and "water_heater" not in ds.get(day_idx, set()):
        start_h = float(np.clip(float(decoded[3]), 7.0, 17.0))
        end_h = min(20.0, start_h + 3.0)
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
        ev_start = float(np.clip(float(decoded[5]) if len(decoded) > 5 else 22.0, 0.0, 30.0))
        ev_end = float(np.clip(float(decoded[6]) if len(decoded) > 6 else 7.0, 0.0, 14.0))
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
