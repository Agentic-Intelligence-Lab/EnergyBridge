"""Local copy of the collaborator control-oriented dynamic model.

The reference implementation lives outside this repo under
``/home/hku_user/work/reference/Dynamic_Model``.  This module keeps the fitted
model assets local to the benchmark baseline and exposes a small adapter that
turns benchmark decision-time state/action pairs into objective-ready predicted
state dictionaries.
"""

from __future__ import annotations

import csv
import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping

import yaml


ASSET_DIR = Path(__file__).resolve().parent / "assets"
BEHAVIOR_MATRIX = ASSET_DIR / "person_behavior_mdp" / "matrices" / "action_policy_matrix.csv"
FACTOR_MATRIX = ASSET_DIR / "person_behavior_mdp" / "matrices" / "factorized_transition_matrix.csv"
THERMAL_PARAMS = ASSET_DIR / "thermal_improvement_experiments" / "04_5r3c_hvac_solar" / "parameters.json"
THERMAL_SUMMARY = ASSET_DIR / "thermal_improvement_experiments" / "summary.json"
APPLIANCE_CONFIG = ASSET_DIR / "configs" / "tianjin_family_appliances.yaml"
HVAC_AUDIT = ASSET_DIR / "complete_sinergym_long" / "metrics" / "complete_sinergym_long_metrics.json"
REGIONAL_5R3C_DIR = ASSET_DIR / "regional_5r3c"
DEFAULT_DYNAMIC_MODEL_REGION = "tianjin"
TASKS = ("dishwasher", "clothes_washer", "clothes_dryer")
TASK_TO_BENCH = {
    "dishwasher": "dishwasher",
    "clothes_washer": "washer",
    "clothes_dryer": "dryer",
}
BENCH_TO_TASK = {v: k for k, v in TASK_TO_BENCH.items()}


@dataclass
class ThermalState:
    t_air: float
    t_mass: float
    t_env: float
    prev_hvac_raw_kw: float = 0.0


@dataclass
class DeviceState:
    ev_soc: float = 0.35
    ev_at_home_prob: float = 1.0
    ewh_temp_c: float = 50.0
    task_waiting_prob: dict[str, float] = field(default_factory=dict)
    task_running_prob: dict[str, float] = field(default_factory=dict)
    task_finished_prob: dict[str, float] = field(default_factory=dict)
    task_remaining_expected: dict[str, float] = field(default_factory=dict)


@dataclass
class MPCState:
    timestamp: str
    day_type: str
    thermal: ThermalState
    devices: DeviceState
    occupied_prob: float = 1.0


@dataclass
class ControlInput:
    heating_setpoint_c: float = 20.0
    cooling_setpoint_c: float = 26.0
    hvac_cooling_fraction: float | None = None
    hvac_heating_fraction: float | None = None
    ev_charge_control: float = 1.0
    ewh_control: float = 1.0
    task_start_control: dict[str, float] = field(default_factory=dict)


@dataclass
class ForecastInput:
    outdoor_temperature_c: float
    direct_solar_w_m2: float = 0.0
    diffuse_solar_w_m2: float = 0.0
    price_yuan_per_kwh: float = 1.0
    base_power_kw: float = 0.0


def _clip(x: float, lo: float, hi: float) -> float:
    return float(min(max(float(x), lo), hi))


def _hour_decimal(ts: datetime) -> float:
    return ts.hour + ts.minute / 60.0


def _in_window(hour: float, start: float, end: float) -> bool:
    if start <= end:
        return start <= hour < end
    return hour >= start or hour < end


def _float_or_none(value: Any) -> float | None:
    try:
        if value is None:
            return None
        value = float(value)
        if not math.isfinite(value):
            return None
        return value
    except (TypeError, ValueError):
        return None


def _canonical_region_key(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if not text:
        return None
    if text in {"germany", "de", "deu", "berlin"} or "germany" in text or "berlin" in text:
        return "berlin"
    if text in {"tianjin", "tj", "beijing", "shanghai", "china", "cn", "chn"} or "tianjin" in text:
        return "tianjin"
    return None


def dynamic_model_region_for_state(state: Mapping[str, Any] | None) -> str:
    """Map benchmark state metadata to a calibrated dynamics model region."""
    state = state or {}
    for key in (
        "dynamic_model_region",
        "dynamics_region",
        "city",
        "weather",
        "weather_label",
        "location",
        "scenario",
    ):
        region = _canonical_region_key(state.get(key))
        if region:
            return region
    return DEFAULT_DYNAMIC_MODEL_REGION


def _region_asset_paths(region_key: str) -> dict[str, Path]:
    region = _canonical_region_key(region_key) or DEFAULT_DYNAMIC_MODEL_REGION
    if region == "berlin":
        regional_dir = REGIONAL_5R3C_DIR / "berlin"
        return {
            "parameters": regional_dir / "parameters_5r3c_hvac_solar.json",
            "metrics": regional_dir / "metrics_5r3c_hvac_solar.json",
            "power_parameters": regional_dir / "power_model_parameters.json",
            "power_metrics": regional_dir / "power_model_metrics.json",
        }
    return {
        "parameters": THERMAL_PARAMS,
        "metrics": THERMAL_SUMMARY,
        "power_parameters": Path(),
        "power_metrics": Path(),
    }


def _read_json_if_exists(path: Path) -> dict[str, Any]:
    if path and path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def _discretize(value: float, cuts: list[float], labels: list[str]) -> str:
    for cut, label in zip(cuts, labels):
        if value < cut:
            return label
    return labels[-1]


class BehaviorMDPExpected:
    def __init__(
        self,
        action_matrix: Path = BEHAVIOR_MATRIX,
        factor_matrix: Path = FACTOR_MATRIX,
    ) -> None:
        self.policy: dict[tuple[str, str], dict[str, float]] = {}
        with action_matrix.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                self.policy.setdefault((row["action_name"], row["state_key"]), {})[
                    str(row["action_value"])
                ] = float(row["probability"])
        self.factor: dict[tuple[str, str], dict[str, float]] = {}
        with factor_matrix.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                self.factor.setdefault((row["transition_name"], row["condition_key"]), {})[
                    str(row["next_value"])
                ] = float(row["probability"])

    def keys(self, state: MPCState, forecast: ForecastInput) -> list[str]:
        ts = datetime.fromisoformat(state.timestamp)
        h = ts.hour
        occ = int(state.occupied_prob >= 0.5)
        evhome = int(state.devices.ev_at_home_prob >= 0.5)
        tin = _discretize(state.thermal.t_air, [20.0, 24.0, 27.0], ["cold", "comfort", "warm", "hot"])
        tout = _discretize(
            forecast.outdoor_temperature_c,
            [10.0, 20.0, 30.0],
            ["cold", "mild", "hot", "extreme"],
        )
        soc = _discretize(state.devices.ev_soc, [0.35, 0.8], ["low", "target", "full"])
        ewh = _discretize(state.devices.ewh_temp_c, [45.0, 55.0], ["low", "normal", "hot"])
        task_codes = {}
        for task in TASKS:
            waiting = state.devices.task_waiting_prob.get(task, 0.0)
            running = state.devices.task_running_prob.get(task, 0.0)
            finished = state.devices.task_finished_prob.get(task, 0.0)
            task_codes[task] = 1 if waiting >= max(running, finished) else 2 if running >= finished else 3
        full = (
            f"{state.day_type}|h{h:02d}|occ{occ}|evhome{evhome}|Tin_{tin}|Tout_{tout}|"
            f"soc_{soc}|ewh_{ewh}|dish{task_codes['dishwasher']}|"
            f"wash{task_codes['clothes_washer']}|dry{task_codes['clothes_dryer']}"
        )
        core = (
            f"{state.day_type}|h{h:02d}|occ{occ}|evhome{evhome}|"
            f"dish{task_codes['dishwasher']}|wash{task_codes['clothes_washer']}|"
            f"dry{task_codes['clothes_dryer']}"
        )
        return [full, core, f"{state.day_type}|h{h:02d}", f"h{h:02d}", "GLOBAL"]

    def action_distribution(self, action_name: str, state: MPCState, forecast: ForecastInput) -> dict[str, float]:
        for key in self.keys(state, forecast):
            dist = self.policy.get((action_name, key))
            if dist:
                return dist
        return {}

    def expected_binary_action(self, action_name: str, state: MPCState, forecast: ForecastInput) -> float:
        dist = self.action_distribution(action_name, state, forecast)
        return float(dist.get("1", dist.get("1.0", 0.0)))

    def expected_setpoint(self, action_name: str, state: MPCState, forecast: ForecastInput, default: float) -> float:
        dist = self.action_distribution(action_name, state, forecast)
        if not dist:
            return default
        return sum(float(k) * v for k, v in dist.items())

    def factor_prob(self, transition_name: str, condition_key: str, next_value: str, default: float) -> float:
        dist = self.factor.get((transition_name, condition_key)) or self.factor.get((transition_name, "GLOBAL")) or {}
        return float(dist.get(str(next_value), default))

    def behavior_forecast(self, state: MPCState, forecast: ForecastInput) -> dict[str, Any]:
        ts = datetime.fromisoformat(state.timestamp)
        h = ts.hour
        occ_now = int(state.occupied_prob >= 0.5)
        ev_now = int(state.devices.ev_at_home_prob >= 0.5)
        return {
            "occupied_prob_next": self.factor_prob(
                "occupied_next", f"{state.day_type}|h{h:02d}|occ{occ_now}", "1", state.occupied_prob
            ),
            "ev_at_home_prob_next": self.factor_prob(
                "ev_at_home_next", f"{state.day_type}|h{h:02d}|evhome{ev_now}", "1", state.devices.ev_at_home_prob
            ),
            "preferred_heating_setpoint_c": self.expected_setpoint("heating_setpoint_c", state, forecast, 20.0),
            "preferred_cooling_setpoint_c": self.expected_setpoint("cooling_setpoint_c", state, forecast, 26.0),
            "ev_charge_request_prob": self.expected_binary_action("ev_charge_request", state, forecast),
            "ewh_on_request_prob": self.expected_binary_action("ewh_on", state, forecast),
            "dishwasher_start_prob": self.expected_binary_action("dishwasher_start", state, forecast),
            "clothes_washer_start_prob": self.expected_binary_action("clothes_washer_start", state, forecast),
            "clothes_dryer_start_prob": self.expected_binary_action("clothes_dryer_start", state, forecast),
        }


class DeterministicExpectedMPCDynamicModel:
    def __init__(self, region_key: str = DEFAULT_DYNAMIC_MODEL_REGION) -> None:
        self.region_key = _canonical_region_key(region_key) or DEFAULT_DYNAMIC_MODEL_REGION
        self.asset_paths = _region_asset_paths(self.region_key)
        self.behavior = BehaviorMDPExpected()
        self.thermal_params = json.loads(self.asset_paths["parameters"].read_text(encoding="utf-8"))
        self.thermal_summary = _read_json_if_exists(self.asset_paths["metrics"])
        self.appliance_cfg = yaml.safe_load(APPLIANCE_CONFIG.read_text(encoding="utf-8"))
        self.devices_cfg = self.appliance_cfg["devices"]
        self.dt_hours = float(self.appliance_cfg["simulation"].get("time_step_minutes", 10)) / 60.0
        self.hvac = self._load_hvac_audit()
        self.envelope = self.thermal_summary["envelope_audit"]
        self.power_model_parameters = _read_json_if_exists(self.asset_paths["power_parameters"])

    def _load_hvac_audit(self) -> dict[str, float]:
        regional_audit = self.thermal_summary.get("hvac_audit")
        if isinstance(regional_audit, dict) and "cooling_capacity_kw" in regional_audit:
            return {
                "cooling_capacity_kw": float(regional_audit.get("cooling_capacity_kw", 10.0)),
                "cooling_cop": float(regional_audit.get("cooling_cop", 4.0)),
                "heating_capacity_kw": float(regional_audit.get("heating_capacity_kw", 6.0)),
                "heating_cop": float(regional_audit.get("heating_cop", 3.5)),
                "supplemental_heating_kw": float(regional_audit.get("supplemental_heating_kw", 0.0)),
                "supply_fan_kw": float(regional_audit.get("supply_fan_kw", 0.25)),
            }
        legacy = json.loads(HVAC_AUDIT.read_text(encoding="utf-8"))["energyplus_hvac_audit"]
        return {
            "cooling_capacity_kw": float(legacy.get("cooling_capacity_kw", 10.0)),
            "cooling_cop": float(legacy.get("cooling_cop", 4.0)),
            "heating_capacity_kw": float(legacy.get("heating_capacity_kw", 6.0)),
            "heating_cop": float(legacy.get("heating_cop", 3.5)),
            "supplemental_heating_kw": float(legacy.get("supplemental_heating_kw", 0.0)),
            "supply_fan_kw": float(legacy.get("supply_fan_kw", 0.25)),
        }

    def q_solar_kw(self, forecast: ForecastInput) -> float:
        irradiance_kw_m2 = _clip((forecast.direct_solar_w_m2 + forecast.diffuse_solar_w_m2) / 1000.0, 0.0, 1.2)
        return self.envelope["window_shgc_area_m2"] * irradiance_kw_m2

    def hvac_from_control(self, state: MPCState, control: ControlInput, forecast: ForecastInput) -> dict[str, float]:
        cooling_capacity = float(self.hvac["cooling_capacity_kw"])
        heating_capacity = float(self.hvac["heating_capacity_kw"])
        cooling_cop = float(self.hvac["cooling_cop"])
        heating_cop = float(self.hvac["heating_cop"])
        cool_frac = (
            _clip((state.thermal.t_air - control.cooling_setpoint_c) / 2.0, 0.0, 1.0)
            if control.hvac_cooling_fraction is None
            else _clip(control.hvac_cooling_fraction, 0.0, 1.0)
        )
        heat_frac = (
            _clip((control.heating_setpoint_c - state.thermal.t_air) / 2.0, 0.0, 1.0)
            if control.hvac_heating_fraction is None
            else _clip(control.hvac_heating_fraction, 0.0, 1.0)
        )
        if cool_frac > 0 and heat_frac > 0:
            if cool_frac >= heat_frac:
                heat_frac = 0.0
            else:
                cool_frac = 0.0
        qcool = cooling_capacity * cool_frac
        qheat = heating_capacity * heat_frac
        fan_power = 0.25 * max(cool_frac, heat_frac)
        hvac_power = qcool / max(cooling_cop, 1e-6) + qheat / max(heating_cop, 1e-6) + fan_power
        p = self.thermal_params
        qeff = (
            -p["eta_cooling"] * qcool
            + p["eta_heating"] * qheat
            + p["eta_fan"] * fan_power
            + p["eta_lag"] * state.thermal.prev_hvac_raw_kw
        )
        raw = -qcool + qheat + fan_power
        return {
            "qcool_kw": qcool,
            "qheat_kw": qheat,
            "fan_power_kw": fan_power,
            "hvac_power_kw": hvac_power,
            "qeff_kw": qeff,
            "raw_hvac_kw": raw,
            "cooling_fraction": cool_frac,
            "heating_fraction": heat_frac,
        }

    def step_thermal(self, state: MPCState, hvac_terms: Mapping[str, float], forecast: ForecastInput) -> ThermalState:
        p = self.thermal_params
        th = state.thermal
        tout = forecast.outdoor_temperature_c
        qsolar = self.q_solar_kw(forecast)
        air_flow = (
            p["g_oa"] * (tout - th.t_air)
            + p["g_am"] * (th.t_mass - th.t_air)
            + p["g_ae"] * (th.t_env - th.t_air)
            + hvac_terms["qeff_kw"]
            + p["r_solar_air"] * qsolar
        )
        mass_flow = p["g_am"] * (th.t_air - th.t_mass) + p["g_me"] * (th.t_env - th.t_mass) + p["r_solar_mass"] * qsolar
        env_flow = (
            p["g_ae"] * (th.t_air - th.t_env)
            + p["g_me"] * (th.t_mass - th.t_env)
            + p["g_eo"] * (tout - th.t_env)
            + p["r_solar_env"] * qsolar
        )
        return ThermalState(
            t_air=_clip(th.t_air + self.dt_hours * air_flow / max(p["c_air"], 1e-8), -40, 80),
            t_mass=_clip(th.t_mass + self.dt_hours * mass_flow / max(p["c_mass"], 1e-8), -40, 80),
            t_env=_clip(th.t_env + self.dt_hours * env_flow / max(p["c_env"], 1e-8), -40, 80),
            prev_hvac_raw_kw=hvac_terms["raw_hvac_kw"],
        )

    def step_ev(self, state: MPCState, control: ControlInput, behavior: Mapping[str, Any]) -> tuple[float, float]:
        cfg = self.devices_cfg["ev_charger"]
        soc = state.devices.ev_soc
        target = float(cfg["target_soc"])
        cap = float(cfg["battery_capacity_kwh"])
        eff = float(cfg["charging_efficiency"])
        rated = float(cfg["rated_power_kw"])
        request = float(behavior["ev_charge_request_prob"])
        at_home = float(behavior["ev_at_home_prob_next"])
        fraction = _clip(control.ev_charge_control * request * at_home, 0.0, 1.0) if soc < target else 0.0
        remaining_grid_kwh = max(0.0, (target - soc) * cap / eff)
        power = min(rated * fraction, remaining_grid_kwh / self.dt_hours if self.dt_hours > 0 else 0.0)
        next_soc = _clip(soc + power * self.dt_hours * eff / cap, 0.0, float(cfg.get("max_soc", 1.0)))
        return power, next_soc

    def draw_liters(self, timestamp: datetime) -> float:
        cfg = self.devices_cfg["electric_water_heater"]
        total = 0.0
        for event in cfg.get("draw_events", []):
            hh, mm = str(event["time"]).split(":")
            if timestamp.hour == int(hh) and timestamp.minute == int(mm):
                total += float(event.get("volume_l", 0.0))
        return total

    def step_ewh(self, state: MPCState, control: ControlInput, behavior: Mapping[str, Any]) -> tuple[float, float, float]:
        cfg = self.devices_cfg["electric_water_heater"]
        temp = state.devices.ewh_temp_c
        volume = float(cfg["tank_volume_l"])
        draw = min(volume, self.draw_liters(datetime.fromisoformat(state.timestamp)))
        if draw > 0:
            temp = ((volume - draw) * temp + draw * float(cfg["inlet_temperature_c"])) / volume
        temp -= float(cfg["loss_coefficient_per_hour"]) * (temp - float(cfg["ambient_temperature_c"])) * self.dt_hours
        request = _clip(control.ewh_control * float(behavior["ewh_on_request_prob"]), 0.0, 1.0)
        rated = float(cfg["rated_power_kw"])
        setpoint = float(cfg["setpoint_c"])
        capacity = volume * 4.186 / 3600.0
        needed = max(0.0, setpoint - temp) * capacity / float(cfg["thermal_efficiency"])
        energy = min(rated * request * self.dt_hours, needed)
        power = energy / self.dt_hours if self.dt_hours > 0 else 0.0
        temp += energy * float(cfg["thermal_efficiency"]) / max(capacity, 1e-9)
        temp = _clip(temp, float(cfg["minimum_temperature_c"]), float(cfg["maximum_temperature_c"]))
        return power, temp, draw

    def step_tasks(self, state: MPCState, control: ControlInput, behavior: Mapping[str, Any]) -> tuple[dict[str, Any], float]:
        ts = datetime.fromisoformat(state.timestamp)
        hour = _hour_decimal(ts)
        results: dict[str, Any] = {}
        total_power = 0.0
        next_waiting: dict[str, float] = {}
        next_running: dict[str, float] = {}
        next_finished: dict[str, float] = {}
        next_remaining: dict[str, float] = {}
        for task in TASKS:
            cfg = self.devices_cfg[task]
            rated = float(cfg["rated_power_kw"])
            duration_steps = max(1, int(math.ceil(float(cfg["cycle_duration_minutes"]) / (self.dt_hours * 60.0))))
            latest = float(cfg["latest_finish_hour"])
            earliest = float(cfg["earliest_start_hour"])
            waiting = state.devices.task_waiting_prob.get(task, 1.0)
            running = state.devices.task_running_prob.get(task, 0.0)
            finished = state.devices.task_finished_prob.get(task, 0.0)
            rem = state.devices.task_remaining_expected.get(task, 0.0)
            request = float(behavior[f"{task}_start_prob"])
            control_start = control.task_start_control.get(task, 1.0)
            can_start = 1.0 if hour >= earliest and hour + duration_steps * self.dt_hours <= latest + 1e-9 else 0.0
            start_prob = _clip(waiting * request * control_start * can_start, 0.0, 1.0)
            running_existing = running * (1.0 if rem > 1.0 else 0.0)
            finish_existing = running * (1.0 if rem <= 1.0 and rem > 0.0 else 0.0)
            run_prob = _clip(running_existing + start_prob, 0.0, 1.0)
            wait_prob = _clip(waiting - start_prob, 0.0, 1.0)
            fin_prob = _clip(finished + finish_existing, 0.0, 1.0)
            remaining = 0.0
            if run_prob > 1e-9:
                remaining = (running_existing * max(rem - 1.0, 0.0) + start_prob * duration_steps) / run_prob
            power = rated * run_prob
            deadline_violation_prob = wait_prob if hour > latest else 0.0
            total_power += power
            next_waiting[task] = wait_prob
            next_running[task] = run_prob
            next_finished[task] = fin_prob
            next_remaining[task] = remaining
            results[task] = {
                "start_probability": start_prob,
                "running_probability": run_prob,
                "waiting_probability": wait_prob,
                "finished_probability": fin_prob,
                "remaining_expected_steps": remaining,
                "power_kw": power,
                "deadline_violation_probability": deadline_violation_prob,
            }
        results["next_waiting_prob"] = next_waiting
        results["next_running_prob"] = next_running
        results["next_finished_prob"] = next_finished
        results["next_remaining_expected"] = next_remaining
        return results, total_power

    def step(self, state: MPCState, control: ControlInput, forecast: ForecastInput) -> dict[str, Any]:
        behavior = self.behavior.behavior_forecast(state, forecast)
        hvac_terms = self.hvac_from_control(state, control, forecast)
        next_thermal = self.step_thermal(state, hvac_terms, forecast)
        ev_power, next_soc = self.step_ev(state, control, behavior)
        ewh_power, next_ewh_temp, draw_l = self.step_ewh(state, control, behavior)
        task_results, task_power = self.step_tasks(state, control, behavior)
        total_power = hvac_terms["hvac_power_kw"] + ev_power + ewh_power + task_power + forecast.base_power_kw
        cost = total_power * self.dt_hours * forecast.price_yuan_per_kwh
        next_ts = datetime.fromisoformat(state.timestamp) + timedelta(hours=self.dt_hours)
        next_devices = DeviceState(
            ev_soc=next_soc,
            ev_at_home_prob=float(behavior["ev_at_home_prob_next"]),
            ewh_temp_c=next_ewh_temp,
            task_waiting_prob=task_results["next_waiting_prob"],
            task_running_prob=task_results["next_running_prob"],
            task_finished_prob=task_results["next_finished_prob"],
            task_remaining_expected=task_results["next_remaining_expected"],
        )
        next_state = MPCState(
            timestamp=next_ts.isoformat(sep=" "),
            day_type=state.day_type,
            thermal=next_thermal,
            devices=next_devices,
            occupied_prob=float(behavior["occupied_prob_next"]),
        )
        return {
            "next_state": asdict(next_state),
            "behavior": behavior,
            "thermal": {"qsolar_kw": self.q_solar_kw(forecast), **hvac_terms},
            "devices": {
                "ev_power_kw": ev_power,
                "ewh_power_kw": ewh_power,
                "ewh_draw_l": draw_l,
                "task_power_kw": task_power,
                "tasks": {k: v for k, v in task_results.items() if k in TASKS},
            },
            "outputs": {
                "hvac_power_kw": hvac_terms["hvac_power_kw"],
                "ev_power_kw": ev_power,
                "ewh_power_kw": ewh_power,
                "task_power_kw": task_power,
                "base_power_kw": forecast.base_power_kw,
                "total_power_kw": total_power,
                "energy_cost_yuan": cost,
                "comfort_violation_c": float(behavior["occupied_prob_next"])
                * max(0.0, next_thermal.t_air - float(behavior["preferred_cooling_setpoint_c"])),
                "task_deadline_violation_expected": sum(
                    task_results[t]["deadline_violation_probability"] for t in TASKS
                ),
            },
        }


class DynamicModelScorer:
    """Adapter from benchmark state/action to predicted objective state."""

    def __init__(self, horizon_steps: int = 18, region_key: str = DEFAULT_DYNAMIC_MODEL_REGION) -> None:
        self.model = DeterministicExpectedMPCDynamicModel(region_key=region_key)
        self.horizon_steps = max(1, int(horizon_steps))

    def predict_objective_state(self, state: dict, action: dict) -> tuple[dict, dict]:
        trajectory, diagnostics = self.predict_objective_trajectory(state, action)
        return trajectory[-1], diagnostics

    def predict_objective_trajectory(self, state: dict, action: dict) -> tuple[list[dict[str, Any]], dict]:
        cur = self._initial_state(state)
        rows: list[dict[str, Any]] = []
        predicted_states: list[dict[str, Any]] = []
        for _ in range(self.horizon_steps):
            ts = datetime.fromisoformat(cur.timestamp)
            forecast = self._forecast(state, ts)
            control = self._control(action, ts)
            result = self.model.step(cur, control, forecast)
            rows.append(result)
            predicted_states.append(
                self._objective_state_at_step(state, action, result, forecast, step_index=len(predicted_states) + 1)
            )
            ns = result["next_state"]
            cur = MPCState(
                timestamp=ns["timestamp"],
                day_type=ns["day_type"],
                thermal=ThermalState(**ns["thermal"]),
                devices=DeviceState(**ns["devices"]),
                occupied_prob=ns["occupied_prob"],
            )
        final = rows[-1]
        diagnostics = {
            "model": "regional_5r3c_hvac_solar_dynamic_model_v2",
            "region": self.model.region_key,
            "thermal_parameters_path": str(self.model.asset_paths["parameters"]),
            "regional_power_model_available": bool(self.model.power_model_parameters),
            "horizon_steps": self.horizon_steps,
            "horizon_minutes": round(self.horizon_steps * self.model.dt_hours * 60.0, 3),
            "predicted_temp_c": float(final["next_state"]["thermal"]["t_air"]),
            "predicted_hvac_power_kw": float(final["outputs"]["hvac_power_kw"]),
            "predicted_total_power_kw": float(final["outputs"]["total_power_kw"]),
            "predicted_task_power_kw": float(final["outputs"]["task_power_kw"]),
            "predicted_ewh_power_kw": float(final["outputs"]["ewh_power_kw"]),
            "predicted_ev_power_kw": float(final["outputs"]["ev_power_kw"]),
            "comfort_violation_c": float(final["outputs"]["comfort_violation_c"]),
            "stage_hvac_power_kw": [round(float(row["outputs"]["hvac_power_kw"]), 6) for row in rows],
            "stage_total_power_kw": [round(float(row["outputs"]["total_power_kw"]), 6) for row in rows],
        }
        for predicted in predicted_states:
            predicted["dynamic_model_prediction"] = diagnostics
        return predicted_states, diagnostics

    def _initial_state(self, state: dict) -> MPCState:
        sim_h = _float_or_none(state.get("sim_h")) or 0.0
        day_idx = int(state.get("day_idx", int(sim_h // 24)))
        hod = _float_or_none(state.get("hod"))
        if hod is None:
            hod = sim_h % 24.0
        hour = int(hod) % 24
        minute = int(round((hod % 1.0) * 60.0))
        timestamp = datetime(2026, 7, min(day_idx + 1, 28), hour, minute)
        temp = _float_or_none(state.get("temp_c")) or 26.0
        devices = DeviceState(
            ev_soc=self._ev_soc(state),
            ev_at_home_prob=self._ev_at_home_prob(state, hod),
            ewh_temp_c=self._ewh_temp(state),
            task_waiting_prob={task: self._task_probs(state, task)[0] for task in TASKS},
            task_running_prob={task: self._task_probs(state, task)[1] for task in TASKS},
            task_finished_prob={task: self._task_probs(state, task)[2] for task in TASKS},
            task_remaining_expected={task: self._task_remaining(state, task) for task in TASKS},
        )
        return MPCState(
            timestamp=timestamp.isoformat(sep=" "),
            day_type="weekend" if timestamp.weekday() >= 5 else "weekday",
            thermal=ThermalState(temp, temp, temp),
            devices=devices,
            occupied_prob=1.0 if 8.0 <= hod < 22.0 else 0.0,
        )

    def _forecast(self, state: dict, ts: datetime) -> ForecastInput:
        outdoor = _float_or_none(state.get("outdoor_temp_c")) or 30.0
        price = _float_or_none(state.get("price"))
        if price is None:
            price = _float_or_none(state.get("tou_price")) or 1.0
        base = _float_or_none(state.get("base_load_kw"))
        if base is None:
            base = _float_or_none(state.get("base_load_forecast_kw")) or 0.0
        return ForecastInput(
            outdoor_temperature_c=outdoor,
            direct_solar_w_m2=0.0 if ts.hour >= 18 or ts.hour < 6 else 250.0,
            diffuse_solar_w_m2=0.0 if ts.hour >= 18 or ts.hour < 6 else 80.0,
            price_yuan_per_kwh=price,
            base_power_kw=base,
        )

    def _objective_state_at_step(
        self,
        state: dict,
        action: dict,
        result: dict[str, Any],
        forecast: ForecastInput,
        step_index: int,
    ) -> dict[str, Any]:
        predicted = dict(state)
        next_state = result["next_state"]
        next_ts = datetime.fromisoformat(next_state["timestamp"])
        sim_h0 = _float_or_none(state.get("sim_h")) or 0.0
        sim_h = sim_h0 + step_index * self.model.dt_hours
        predicted["sim_h"] = sim_h
        predicted["day_idx"] = int(sim_h // 24)
        predicted["hod"] = _hour_decimal(next_ts)
        predicted["dt_h"] = self.model.dt_hours
        predicted["temp_c"] = float(next_state["thermal"]["t_air"])
        predicted["occupancy"] = float(next_state["occupied_prob"])
        predicted["hvac_power_kw"] = float(result["outputs"]["hvac_power_kw"])
        predicted["base_load_kw"] = float(result["outputs"]["base_power_kw"])
        predicted["price"] = float(forecast.price_yuan_per_kwh)
        predicted["outdoor_temp_c"] = float(forecast.outdoor_temperature_c)
        predicted["current_setpoint_c"] = _float_or_none(action.get("setpoint")) or _float_or_none(
            state.get("current_setpoint_c")
        )
        predicted["base_load_forecast_kw"] = float(forecast.base_power_kw)
        event = state.get("vpp_event") or {}
        start = _float_or_none(event.get("trigger_h"))
        end = _float_or_none(event.get("end_h"))
        if start is not None and end is not None and start <= sim_h < end:
            predicted["vpp_active"] = True
            predicted["vpp_event"] = dict(event)
            predicted["vpp_target_kwh"] = state.get("vpp_target_kwh")
        else:
            predicted["vpp_active"] = False
            predicted["vpp_event"] = None
            predicted["vpp_target_kwh"] = None
        return predicted

    def _control(self, action: dict, ts: datetime) -> ControlInput:
        appliances = action.get("appliances") or {}
        hod = _hour_decimal(ts)
        setpoint = _float_or_none(action.get("setpoint")) or 26.0
        return ControlInput(
            cooling_setpoint_c=setpoint,
            heating_setpoint_c=20.0,
            ev_charge_control=self._ev_control(appliances, hod),
            ewh_control=self._ewh_control(appliances, hod),
            task_start_control={
                task: self._task_control(appliances, task, hod)
                for task in TASKS
            },
        )

    def _task_control(self, appliances: dict, task: str, hod: float) -> float:
        name = TASK_TO_BENCH[task]
        if appliances.get(f"{name}_skip") is True:
            return 0.0
        start = _float_or_none(appliances.get(f"{name}_start_h"))
        if start is None:
            return 1.0
        return 1.0 if abs((hod % 24.0) - start) <= self.model.dt_hours / 2.0 else 0.0

    def _ewh_control(self, appliances: dict, hod: float) -> float:
        if appliances.get("water_heater_preheat") is False:
            return 0.0
        start = _float_or_none(appliances.get("water_heater_preheat_start_h"))
        end = _float_or_none(appliances.get("water_heater_preheat_end_h"))
        if start is None or end is None:
            return 1.0 if appliances.get("water_heater_preheat") is True else 0.0
        return 1.0 if _in_window(hod % 24.0, start, end) else 0.0

    def _ev_control(self, appliances: dict, hod: float) -> float:
        start = _float_or_none(appliances.get("ev_charge_start_h"))
        end = _float_or_none(appliances.get("ev_charge_end_h"))
        if start is not None or end is not None:
            s = 0.0 if start is None else start
            e = 24.0 if end is None else end
            return 1.0 if _in_window(hod % 24.0, s, e) else 0.0
        mode = appliances.get("ev_mode")
        if mode == "delay":
            return 1.0 if hod >= 22.0 or hod < 7.5 else 0.0
        if mode == "normal":
            return 1.0
        return 1.0

    def _task_probs(self, state: dict, task: str) -> tuple[float, float, float]:
        name = TASK_TO_BENCH[task]
        day_idx = int(state.get("day_idx", 0))
        results = (state.get("appliance_results") or {}).get(name, [])
        status = " ".join(str(x) for x in state.get("appliance_status_lines") or [])
        if 0 <= day_idx < len(results):
            result = results[day_idx]
            if result.get("completed") or result.get("skipped"):
                return 0.0, 0.0, 1.0
        if f"{name}: RUNNING" in status:
            return 0.0, 1.0, 0.0
        return 1.0, 0.0, 0.0

    def _task_remaining(self, state: dict, task: str) -> float:
        waiting, running, _ = self._task_probs(state, task)
        if running <= 0.0:
            return 0.0
        cfg = self.model.devices_cfg[task]
        return max(1.0, math.ceil(float(cfg["cycle_duration_minutes"]) / (self.model.dt_hours * 60.0)) / 2.0)

    def _ev_soc(self, state: dict) -> float:
        for key in ("ev_current_soc", "ev_soc", "current_soc"):
            value = _float_or_none(state.get(key))
            if value is not None:
                return _clip(value, 0.0, 1.0)
        cfg = (state.get("appliance_config") or {}).get("ev", {}) or {}
        return _clip(_float_or_none(cfg.get("current_soc")) or _float_or_none(cfg.get("initial_soc")) or 0.8, 0.0, 1.0)

    def _ev_at_home_prob(self, state: dict, hod: float) -> float:
        cfg = (state.get("appliance_config") or {}).get("ev", {}) or {}
        if not cfg.get("present", False):
            return 0.0
        arrival = _float_or_none(cfg.get("arrival_h")) or 18.0
        departure = _float_or_none(cfg.get("departure_h")) or 7.5
        return 1.0 if _in_window(hod, arrival, departure) else 0.0

    def _ewh_temp(self, state: dict) -> float:
        cfg = (state.get("appliance_config") or {}).get("water_heater", {}) or {}
        for key in ("temperature_c", "tank_temperature_c", "current_temp_c"):
            value = _float_or_none(state.get(key))
            if value is not None:
                return value
        return _float_or_none(cfg.get("setpoint_c")) or _float_or_none(cfg.get("preheat_temp_c")) or 55.0
