"""PPO policy adapter for the three-day EnergyPlus RL baseline."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[3]
METHOD_ID = "rl_ppo_3day"
OBJECTIVE_SOURCE = "rl_ppo_3day_policy"
MODEL_ENV_VAR = "ENERGYBRIDGE_RL_MODEL"
DEFAULT_MODEL_CANDIDATES = (
    PROJECT_ROOT / "benchmark_results" / "rl_ppo_3day_smoke" / "ppo_energyplus_3day.zip",
    PROJECT_ROOT / "benchmark_results" / "rl_energyplus_smoke" / "ppo_energyplus_3day.zip",
)
DECISION_INTERVAL_H = 1.0 / 6.0
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

_MODEL_CACHE: dict[tuple[Path, str], Any] = {}


def resolve_model_path() -> Path:
    configured = os.environ.get(MODEL_ENV_VAR, "").strip()
    if configured:
        path = Path(configured)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        if path.exists():
            return path
        raise FileNotFoundError(
            f"RL PPO model not found: {path}. Set {MODEL_ENV_VAR} to a trained "
            "ppo_energyplus_3day.zip file."
        )
    for candidate in DEFAULT_MODEL_CANDIDATES:
        if candidate.exists():
            return candidate
    checked = "\n  ".join(str(path) for path in DEFAULT_MODEL_CANDIDATES)
    raise FileNotFoundError(
        f"No RL PPO model found. Set {MODEL_ENV_VAR} or create one with the "
        "rl_energyplus_3day smoke trainer. Checked:\n  " + checked
    )


def load_policy(model_path: str | Path | None = None, *, device: str = "cpu") -> Any:
    path = Path(model_path) if model_path else resolve_model_path()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    key = (path.resolve(), device)
    if key not in _MODEL_CACHE:
        from stable_baselines3 import PPO

        _MODEL_CACHE[key] = PPO.load(path, device=device, print_system_info=False)
    return _MODEL_CACHE[key]


def decode_action(action: np.ndarray) -> np.ndarray:
    normalized = np.clip(np.asarray(action, dtype=np.float32), -1.0, 1.0)
    return np.array(
        [
            25.0 + 3.0 * normalized[0],
            (normalized[1] + 1.0) / 2.0,
            (normalized[2] + 1.0) / 2.0,
        ],
        dtype=np.float32,
    )


def build_observation(
    *,
    loop: Any,
    sim_h: float,
    temp_c: float,
    outdoor_temp_c: float,
    vpp_active: bool,
    assessment: dict[str, Any] | None,
) -> np.ndarray:
    hour = float(sim_h) % 24.0
    day_idx = min(2, int(float(sim_h) // 24))
    suite = loop.appliance_suite
    assessment = assessment or {}
    vpp_target_kwh = max(0.1, 2.0 - float(assessment.get("recommended_bid_kw", 0.0))) if vpp_active else 0.0
    capacity_values = (
        [
            float(assessment.get("committable_kw", 0.0)) / 2.0,
            float(assessment.get("recommended_bid_kw", 0.0)) / 2.0,
            *_capacity_constraint_flags(assessment),
        ]
        if vpp_active
        else [0.0] * 7
    )
    values = [
        np.sin(2 * np.pi * hour / 24.0),
        np.cos(2 * np.pi * hour / 24.0),
        max(0.0, 72.0 - float(sim_h)) / 72.0,
        float(temp_c) / 40.0,
        float(outdoor_temp_c) / 45.0,
        float(vpp_active),
        vpp_target_kwh / 2.0,
        *capacity_values,
    ]
    for name in ("washer", "dishwasher", "dryer"):
        values.extend(_shiftable_observation(suite._shiftable[name], day_idx))
    values.extend(_water_heater_observation(suite._water_heater, day_idx))
    values.extend(_ev_observation(suite._ev, day_idx, hour))
    values.extend(
        [
            float(suite._refrigerator.present),
            float(suite._refrigerator.power_kw) / 2.0,
        ]
    )
    observation = np.asarray(values, dtype=np.float32)
    expected_shape = (len(OBSERVATION_NAMES),)
    if observation.shape != expected_shape:
        raise RuntimeError(f"RL observation schema mismatch: {observation.shape} != {expected_shape}")
    return observation


def predict_control_result(
    *,
    loop: Any,
    sim_h: float,
    temp_c: float,
    outdoor_temp_c: float,
    vpp_active: bool,
    assessment: dict[str, Any] | None,
    appliance_config: dict[str, Any] | None,
    base_actions: dict[str, Any],
    vpp_event: dict[str, Any] | None = None,
    model_path: str | Path | None = None,
    device: str = "auto",
) -> dict[str, Any]:
    model = load_policy(model_path, device=device)
    observation = build_observation(
        loop=loop,
        sim_h=sim_h,
        temp_c=temp_c,
        outdoor_temp_c=outdoor_temp_c,
        vpp_active=vpp_active,
        assessment=assessment,
    )
    action, _ = model.predict(observation, deterministic=True)
    result = action_to_control_result(
        action,
        sim_h=sim_h,
        appliance_config=appliance_config,
        base_actions=base_actions,
        vpp_event=vpp_event,
    )
    result["model_path"] = str(resolve_model_path() if model_path is None else model_path)
    return result


def action_to_control_result(
    action: np.ndarray,
    *,
    sim_h: float,
    appliance_config: dict[str, Any] | None,
    base_actions: dict[str, Any],
    vpp_event: dict[str, Any] | None = None,
) -> dict[str, Any]:
    decoded = decode_action(action)
    # Keep RL benchmark actions policy-only.  Legacy callers may still pass
    # base_actions, but those must not complete missing appliance commands.
    actions: dict[str, Any] = {}
    hod = float(sim_h) % 24.0
    vpp_active = _event_active(vpp_event, sim_h)
    present = _present_controllable(appliance_config)

    if "washer" in present and float(decoded[1]) >= 0.5 and not vpp_active:
        actions["washer_start_h"] = round(hod, 3)
        actions["washer_skip"] = False
    if "water_heater" in present and float(decoded[2]) >= 0.5 and not vpp_active and hod < 18.0:
        actions.update(
            {
                "water_heater_preheat": True,
                "water_heater_preheat_start_h": round(hod, 3),
                "water_heater_preheat_end_h": round(min(18.0, hod + 3.0), 3),
                "water_heater_preheat_temp_c": 65.0,
            }
        )
    summary = [
        "RL PPO 3-day raw policy; no fallback appliance commands were added.",
        f"AC setpoint={float(decoded[0]):.1f}C at h={hod:.2f}.",
    ]
    if "washer" in present:
        summary.append(
            "Washer emitted start_h="
            + (f"{float(actions['washer_start_h']):.1f}." if "washer_start_h" in actions else "none.")
        )
    if "water_heater" in present:
        summary.append(
            "Water heater emitted preheat="
            + ("yes." if actions.get("water_heater_preheat") else "no.")
        )
    unsupported = sorted(present.difference({"washer", "water_heater"}))
    if unsupported:
        summary.append("No raw RL action dimension for " + ", ".join(unsupported) + ".")
    if vpp_active:
        summary.append("Decision occurred inside a VPP window; shift/preheat actions are gated off.")

    return {
        "setpoint": round(float(decoded[0]), 3),
        "next_check_hour": float(sim_h) + DECISION_INTERVAL_H,
        "reason": " ".join(summary),
        "appliance_actions": actions,
        "objective_source": OBJECTIVE_SOURCE,
    }


def _capacity_constraint_flags(assessment: dict[str, Any]) -> list[float]:
    constraints = [str(item) for item in assessment.get("main_constraints", [])]
    return [
        float(any(item.startswith(f"{device}:") for item in constraints))
        for device in ("ev", "water_heater", "washer", "dishwasher", "dryer")
    ]


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


def _water_heater_observation(water_heater: Any, day_idx: int) -> list[float]:
    state = water_heater._days.get(day_idx, {})
    start = state.get("preheat_start_h") or water_heater.pre_heat_window_start_h
    end = state.get("preheat_end_h") or water_heater.pre_heat_window_end_h
    return [
        float(water_heater.present),
        float(state.get("preheat_requested", False)),
        float(start) / 24.0,
        float(end) / 24.0,
        float(water_heater.bath_required_h) / 24.0,
    ]


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


def _present_controllable(appliance_config: dict[str, Any] | None) -> set[str]:
    cfg = appliance_config or {}
    present: set[str] = set()
    for name in ("washer", "dishwasher", "dryer"):
        dev = cfg.get(name, {}) or {}
        if bool(dev.get("present", False)) and bool(dev.get("shiftable", True)) and bool(dev.get("dr_adjustable", True)):
            present.add(name)
    for name in ("water_heater", "ev"):
        dev = cfg.get(name, {}) or {}
        if bool(dev.get("present", False)) and bool(dev.get("dr_adjustable", True)):
            present.add(name)
    return present


def _event_active(event: dict[str, Any] | None, sim_h: float) -> bool:
    if not event:
        return False
    try:
        return float(event["trigger_h"]) <= float(sim_h) < float(event["end_h"])
    except (KeyError, TypeError, ValueError):
        return False
