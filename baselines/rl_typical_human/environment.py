"""A lightweight Gymnasium environment for the Typical_Human weekly schedule."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List

import gymnasium as gym
import numpy as np
from gymnasium import spaces

from .pricing import TimeOfUsePricing
from .schedule import DaySchedule, generate_typical_week
from energybridge.quantification import assess_vpp_request


@dataclass
class RuntimeTask:
    device: str
    earliest_start: datetime
    latest_finish: datetime
    duration_steps: int
    rated_power_kw: float
    state: str = "waiting"
    remaining_steps: int = 0
    invalid_start_count: int = 0
    started_count: int = 0
    finished_count: int = 0

    @property
    def state_code(self) -> int:
        return {"idle": 0, "waiting": 1, "running": 2, "finished": 3}.get(self.state, -1)


class TypicalHumanEnv(gym.Env):
    """Standalone 7-day HEMS scheduling environment for smoke RL tests.

    This is intentionally lightweight and does not launch EnergyPlus. It checks
    whether the typical human schedule, external device logic, tariff and reward
    can run end-to-end before connecting the same scenario to Sinergym.
    """

    metadata = {"render_modes": []}

    def __init__(self, seed: int = 20260601, schedules: List[DaySchedule] | None = None,
                 vpp_weight: float = 1.0):
        super().__init__()
        self.seed_value = seed
        self.schedules = schedules or generate_typical_week(seed)
        self.dt_minutes = 10
        self.dt_hours = self.dt_minutes / 60.0
        self.start_datetime = datetime(2026, 6, 1, 0, 0)
        self.end_datetime = datetime(2026, 6, 8, 0, 0)
        self.max_steps = int((self.end_datetime - self.start_datetime).total_seconds() // 60 // self.dt_minutes)
        self.pricing = TimeOfUsePricing()
        self.vpp_weight = float(vpp_weight)
        self.action_space = spaces.Box(
            low=np.array([15.0, 24.0, 0.0, 0.0, 0.0, 0.0, 0.0], dtype=np.float32),
            high=np.array([22.0, 30.0, 1.0, 1.0, 1.0, 1.0, 1.0], dtype=np.float32),
            dtype=np.float32,
        )
        self.observation_space = spaces.Box(low=-np.inf, high=np.inf, shape=(26,), dtype=np.float32)
        self.reset()

    def _build_maps(self) -> None:
        self.day_map = {day.date: day for day in self.schedules}
        self.hot_water_events: Dict[datetime, float] = {}
        self.drive_events: Dict[datetime, float] = {}
        self.tasks: List[RuntimeTask] = []
        for day in self.schedules:
            for event in day.hot_water_events:
                self.hot_water_events[datetime.fromisoformat(event.timestamp)] = event.volume_l
            if day.ev_departure:
                self.drive_events[datetime.fromisoformat(day.ev_departure)] = day.ev_drive_kwh
            for task in day.task_events:
                self.tasks.append(
                    RuntimeTask(
                        device=task.device,
                        earliest_start=datetime.fromisoformat(task.earliest_start),
                        latest_finish=datetime.fromisoformat(task.latest_finish),
                        duration_steps=max(1, int(round(task.duration_minutes / self.dt_minutes))),
                        rated_power_kw=task.rated_power_kw,
                    )
                )

    def reset(self, *, seed: int | None = None, options: Dict[str, Any] | None = None):
        super().reset(seed=seed)
        self.current_step = 0
        self.now = self.start_datetime
        self.ev_soc = 0.80
        self.ev_target_soc = 0.80
        self.ev_capacity_kwh = 60.0
        self.ev_power_kw = 7.0
        self.ev_efficiency = 0.90
        self.ewh_temp_c = 55.0
        self.ewh_setpoint_c = 55.0
        self.ewh_min_c = 42.0
        self.ewh_power_kw = 3.0
        self.ewh_volume_l = 120.0
        self.ewh_loss_per_hour = 0.02
        self.indoor_temp_c = 25.5
        self.total_cost_yuan = 0.0
        self.total_reward = 0.0
        self._build_maps()
        obs = self._observation({})
        return obs, {"typical_human": self._info_row({}, 0.0, 0.0)}

    def _day_schedule(self) -> DaySchedule:
        return self.day_map[self.now.date().isoformat()]

    def _ev_at_home(self) -> bool:
        day = self._day_schedule()
        if not day.ev_departure or not day.ev_arrival:
            return True
        depart = datetime.fromisoformat(day.ev_departure)
        arrive = datetime.fromisoformat(day.ev_arrival)
        return not (depart <= self.now < arrive)

    def _occupied(self) -> bool:
        day = self._day_schedule()
        wake = datetime.fromisoformat(day.wake_time)
        sleep = datetime.fromisoformat(day.sleep_time)
        if day.day_type == "weekend":
            if day.ev_departure and day.ev_arrival:
                depart = datetime.fromisoformat(day.ev_departure)
                arrive = datetime.fromisoformat(day.ev_arrival)
                return not (depart <= self.now < arrive)
            return wake <= self.now <= sleep
        return self._ev_at_home() and wake <= self.now <= sleep

    def _outdoor_temperature(self) -> float:
        hour = self.now.hour + self.now.minute / 60.0
        day_index = (self.now.date() - self.start_datetime.date()).days
        return 27.0 + 5.0 * np.sin(2 * np.pi * (hour - 14.0) / 24.0) + 0.35 * day_index

    def _apply_drive_event(self) -> None:
        drive_kwh = self.drive_events.get(self.now, 0.0)
        if drive_kwh > 0:
            self.ev_soc = max(0.20, self.ev_soc - drive_kwh / self.ev_capacity_kwh)

    def _step_ev(self, charge_request: float) -> tuple[float, float]:
        if not self._ev_at_home() or self.ev_soc >= self.ev_target_soc - 1e-9:
            return 0.0, 0.0
        requested_power = self.ev_power_kw * float(np.clip(charge_request, 0.0, 1.0))
        remaining_grid_kwh = max(0.0, (self.ev_target_soc - self.ev_soc) * self.ev_capacity_kwh / self.ev_efficiency)
        power_kw = min(requested_power, remaining_grid_kwh / self.dt_hours)
        energy_kwh = power_kw * self.dt_hours
        self.ev_soc = min(1.0, self.ev_soc + energy_kwh * self.ev_efficiency / self.ev_capacity_kwh)
        return power_kw, energy_kwh

    def _step_ewh(self, on: float) -> tuple[float, float, float]:
        draw_l = self.hot_water_events.get(self.now, 0.0)
        if draw_l > 0:
            inlet_c = 15.0
            self.ewh_temp_c = ((self.ewh_volume_l - draw_l) * self.ewh_temp_c + draw_l * inlet_c) / self.ewh_volume_l
        self.ewh_temp_c -= self.ewh_loss_per_hour * (self.ewh_temp_c - 20.0) * self.dt_hours
        capacity_kwh_per_k = self.ewh_volume_l * 4.186 / 3600.0
        if on >= 0.5 and self.ewh_temp_c < self.ewh_setpoint_c:
            needed_kwh = (self.ewh_setpoint_c - self.ewh_temp_c) * capacity_kwh_per_k
            energy_kwh = min(self.ewh_power_kw * self.dt_hours, max(0.0, needed_kwh))
        else:
            energy_kwh = 0.0
        self.ewh_temp_c += energy_kwh / capacity_kwh_per_k if capacity_kwh_per_k > 0 else 0.0
        return energy_kwh / self.dt_hours if self.dt_hours > 0 else 0.0, energy_kwh, draw_l

    def _step_tasks(self, actions: Dict[str, float]) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        for name in ["dishwasher", "clothes_washer", "clothes_dryer"]:
            result[f"{name}_power_kw"] = 0.0
            result[f"{name}_energy_kwh"] = 0.0
            result[f"{name}_state_code"] = 0
            result[f"{name}_remaining_steps"] = 0
            result[f"{name}_deadline_violation"] = 0
        for task in self.tasks:
            start_request = actions.get(f"{task.device}_start", 0.0) >= 0.5
            if task.state == "waiting" and start_request:
                if task.earliest_start <= self.now and self.now + timedelta(minutes=task.duration_steps * self.dt_minutes) <= task.latest_finish:
                    if task.device == "clothes_dryer":
                        washer_done = any(t.device == "clothes_washer" and t.state == "finished" and t.earliest_start.date() == task.earliest_start.date() for t in self.tasks)
                        if not washer_done:
                            task.invalid_start_count += 1
                        else:
                            task.state = "running"
                            task.remaining_steps = task.duration_steps
                            task.started_count += 1
                    else:
                        task.state = "running"
                        task.remaining_steps = task.duration_steps
                        task.started_count += 1
                else:
                    task.invalid_start_count += 1
            running = task.state == "running"
            if running:
                result[f"{task.device}_power_kw"] += task.rated_power_kw
                result[f"{task.device}_energy_kwh"] += task.rated_power_kw * self.dt_hours
                task.remaining_steps -= 1
                if task.remaining_steps <= 0:
                    task.state = "finished"
                    task.finished_count += 1
                    task.remaining_steps = 0
            if task.state == "waiting" and self.now > task.latest_finish:
                result[f"{task.device}_deadline_violation"] += 1
            result[f"{task.device}_state_code"] = max(result[f"{task.device}_state_code"], task.state_code)
            result[f"{task.device}_remaining_steps"] = max(result[f"{task.device}_remaining_steps"], task.remaining_steps)
        return result

    def _step_hvac_proxy(self, heating_sp: float, cooling_sp: float) -> tuple[float, float]:
        outdoor = self._outdoor_temperature()
        self.indoor_temp_c += 0.06 * (outdoor - self.indoor_temp_c)
        cooling_power = 0.0
        heating_power = 0.0
        if self.indoor_temp_c > cooling_sp:
            cooling_power = min(3.5, 0.6 + 0.75 * (self.indoor_temp_c - cooling_sp))
            self.indoor_temp_c -= 0.28 * cooling_power
        elif self.indoor_temp_c < heating_sp:
            heating_power = min(3.0, 0.5 + 0.65 * (heating_sp - self.indoor_temp_c))
            self.indoor_temp_c += 0.22 * heating_power
        return cooling_power + heating_power, outdoor

    def _comfort_violation(self) -> float:
        low, high = 23.0, 26.0
        if self.indoor_temp_c < low:
            return low - self.indoor_temp_c
        if self.indoor_temp_c > high:
            return self.indoor_temp_c - high
        return 0.0

    def _vpp_active(self) -> bool:
        return self.now.date() < self.start_datetime.date() + timedelta(days=3) and self.now.hour == 18

    def _vpp_remaining_fraction(self) -> float:
        if not self._vpp_active():
            return 0.0
        return max(0.0, (60.0 - self.now.minute) / 60.0)

    def _capacity_assessment(self, latest: Dict[str, Any]) -> Dict[str, Any]:
        devices: Dict[str, Dict[str, Any]] = {
            "ev": {
                "enabled": True, "type": "ev_charger", "rated_power_kw": self.ev_power_kw,
                "battery_capacity_kwh": self.ev_capacity_kwh, "target_soc": self.ev_target_soc,
                "charging_efficiency": self.ev_efficiency, "arrival_hour": 18.0,
                "departure_hour": 8.0, "stop_at_target": True,
            },
            "water_heater": {
                "enabled": True, "type": "electric_water_heater", "rated_power_kw": self.ewh_power_kw,
                "tank_volume_l": self.ewh_volume_l, "setpoint_c": self.ewh_setpoint_c,
                "minimum_temperature_c": self.ewh_min_c, "ambient_temperature_c": 20.0,
                "thermal_efficiency": 1.0, "loss_coefficient_per_hour": self.ewh_loss_per_hour,
            },
        }
        observation: Dict[str, Any] = {
            "timestamp": self.now.isoformat(sep=" "), "time_step_minutes": self.dt_minutes,
            "ev_soc": self.ev_soc, "ev_target_soc": self.ev_target_soc,
            "ev_at_home": self._ev_at_home(), "ev_power_kw": float(latest.get("ev_power_kw", 0.0)),
            "water_heater_temperature_c": self.ewh_temp_c,
            "water_heater_setpoint_c": self.ewh_setpoint_c,
            "water_heater_power_kw": float(latest.get("ewh_power_kw", 0.0)),
        }
        for device, power_key in (
            ("dishwasher", "dishwasher_power_kw"),
            ("clothes_washer", "clothes_washer_power_kw"),
            ("clothes_dryer", "clothes_dryer_power_kw"),
        ):
            matching = [task for task in self.tasks if task.device == device and task.earliest_start.date() == self.now.date()]
            task = matching[0] if matching else None
            devices[device] = {
                "enabled": task is not None, "type": "task_appliance",
                "rated_power_kw": task.rated_power_kw if task else 0.0,
                "cycle_duration_minutes": task.duration_steps * self.dt_minutes if task else 0,
                "earliest_start_hour": (
                    task.earliest_start.hour + task.earliest_start.minute / 60.0 if task else 0.0
                ),
                "latest_finish_hour": (
                    task.latest_finish.hour + task.latest_finish.minute / 60.0 if task else 24.0
                ),
                "interruptible": False,
            }
            observation[f"{device}_state"] = task.state if task else "idle"
            observation[f"{device}_power_kw"] = float(latest.get(power_key, 0.0))
        return assess_vpp_request(
            observation,
            {"simulation": {"time_step_minutes": self.dt_minutes}, "devices": devices},
            {"direction": "down", "target_kw": 2.0, "duration_minutes": 60.0},
        )

    def _observation(self, latest: Dict[str, Any], capacity: Dict[str, Any] | None = None) -> np.ndarray:
        hour = self.now.hour + self.now.minute / 60.0
        price = self.pricing.price_at(self.now).price_yuan_per_kwh
        assessment = (capacity or self._capacity_assessment(latest))["assessment"]
        task_vals = []
        for name in ["dishwasher", "clothes_washer", "clothes_dryer"]:
            matching = [t for t in self.tasks if t.device == name and t.earliest_start.date() == self.now.date()]
            if matching:
                task = matching[0]
                task_vals.extend([task.state_code, task.remaining_steps / max(1, task.duration_steps)])
            else:
                task_vals.extend([0, 0.0])
        obs = np.array(
            [
                np.sin(2 * np.pi * hour / 24.0),
                np.cos(2 * np.pi * hour / 24.0),
                self.now.weekday() / 6.0,
                price,
                float(self._occupied()),
                float(self._ev_at_home()),
                self.ev_soc,
                self.ewh_temp_c / 70.0,
                self.indoor_temp_c / 40.0,
                self._outdoor_temperature() / 45.0,
                latest.get("household_power_kw", 0.0) / 20.0,
                latest.get("cost_yuan", 0.0),
                latest.get("comfort_violation_c", 0.0) / 10.0,
                latest.get("ev_power_kw", 0.0) / 7.0,
                latest.get("ewh_power_kw", 0.0) / 3.0,
                float(self._vpp_active()),
                self._vpp_remaining_fraction(),
                float(assessment["committable_kw"]) / 10.0,
                float(assessment["recommended_bid_kw"]) / 10.0,
                float(assessment["success_probability"]),
                *task_vals,
            ],
            dtype=np.float32,
        )
        return obs

    def _info_row(self, latest: Dict[str, Any], reward: float, sinergym_reward: float = 0.0) -> Dict[str, Any]:
        return {
            "timestamp": self.now.isoformat(sep=" "),
            "step": self.current_step,
            "reward": reward,
            **latest,
        }

    def step(self, action: np.ndarray):
        action = np.asarray(action, dtype=np.float32).reshape(-1)
        heating_sp, cooling_sp = float(action[0]), float(action[1])
        self._apply_drive_event()
        hvac_power_kw, outdoor_c = self._step_hvac_proxy(heating_sp, cooling_sp)
        ev_power_kw, ev_energy_kwh = self._step_ev(float(action[2]))
        ewh_power_kw, ewh_energy_kwh, draw_l = self._step_ewh(float(action[3]))
        task_result = self._step_tasks(
            {
                "dishwasher_start": float(action[4]),
                "clothes_washer_start": float(action[5]),
                "clothes_dryer_start": float(action[6]),
            }
        )
        task_power_kw = sum(float(task_result[f"{name}_power_kw"]) for name in ["dishwasher", "clothes_washer", "clothes_dryer"])
        task_energy_kwh = sum(float(task_result[f"{name}_energy_kwh"]) for name in ["dishwasher", "clothes_washer", "clothes_dryer"])
        base_load_kw = 0.35 + (0.25 if self._occupied() else 0.05)
        household_power_kw = base_load_kw + hvac_power_kw + ev_power_kw + ewh_power_kw + task_power_kw
        household_energy_kwh = base_load_kw * self.dt_hours + hvac_power_kw * self.dt_hours + ev_energy_kwh + ewh_energy_kwh + task_energy_kwh
        price = self.pricing.price_at(self.now)
        cost_yuan = household_energy_kwh * price.price_yuan_per_kwh
        comfort_violation_c = self._comfort_violation()
        hot_water_violation_c = max(0.0, self.ewh_min_c - self.ewh_temp_c)
        ev_deadline_violation = 1 if (self.now.hour == 7 and self.now.minute == 50 and self.ev_soc < self.ev_target_soc - 0.02) else 0
        task_deadline_violations = sum(int(task_result[f"{name}_deadline_violation"]) for name in ["dishwasher", "clothes_washer", "clothes_dryer"])
        vpp_active = self._vpp_active()
        vpp_penalty = self.vpp_weight * household_energy_kwh if vpp_active else 0.0
        reward = -(
            cost_yuan
            + comfort_violation_c
            + 2.0 * hot_water_violation_c
            + 5.0 * ev_deadline_violation
            + 2.0 * task_deadline_violations
            + vpp_penalty
        )
        self.total_cost_yuan += cost_yuan
        self.total_reward += reward
        task_counts = {
            device: {
                "finished": sum(task.finished_count for task in self.tasks if task.device == device),
                "total": sum(1 for task in self.tasks if task.device == device),
            }
            for device in ["dishwasher", "clothes_washer", "clothes_dryer"]
        }
        latest = {
            "hour": self.now.hour + self.now.minute / 60.0,
            "tou_period": price.period,
            "tou_price_yuan_per_kwh": price.price_yuan_per_kwh,
            "occupied": int(self._occupied()),
            "outdoor_temperature_c": outdoor_c,
            "indoor_temperature_c": self.indoor_temp_c,
            "heating_setpoint_c": heating_sp,
            "cooling_setpoint_c": cooling_sp,
            "base_load_kw": base_load_kw,
            "hvac_power_kw": hvac_power_kw,
            "ev_at_home": int(self._ev_at_home()),
            "ev_soc": self.ev_soc,
            "ev_power_kw": ev_power_kw,
            "ev_energy_kwh": ev_energy_kwh,
            "ewh_temperature_c": self.ewh_temp_c,
            "ewh_power_kw": ewh_power_kw,
            "ewh_energy_kwh": ewh_energy_kwh,
            "ewh_draw_l": draw_l,
            **task_result,
            "dishwasher_tasks_finished": task_counts["dishwasher"]["finished"],
            "dishwasher_tasks_total": task_counts["dishwasher"]["total"],
            "washer_tasks_finished": task_counts["clothes_washer"]["finished"],
            "washer_tasks_total": task_counts["clothes_washer"]["total"],
            "dryer_tasks_finished": task_counts["clothes_dryer"]["finished"],
            "dryer_tasks_total": task_counts["clothes_dryer"]["total"],
            "external_devices_power_kw": ev_power_kw + ewh_power_kw + task_power_kw,
            "household_power_kw": household_power_kw,
            "household_energy_kwh": household_energy_kwh,
            "cost_yuan": cost_yuan,
            "comfort_violation_c": comfort_violation_c,
            "hot_water_violation_c": hot_water_violation_c,
            "ev_deadline_violation": ev_deadline_violation,
            "task_deadline_violations": task_deadline_violations,
            "vpp_active": int(vpp_active),
            "vpp_penalty": vpp_penalty,
            "total_cost_yuan": self.total_cost_yuan,
        }
        capacity = self._capacity_assessment(latest)
        assessment = capacity["assessment"]
        latest.update({
            "capacity_committable_kw": assessment["committable_kw"],
            "capacity_recommended_bid_kw": assessment["recommended_bid_kw"],
            "capacity_success_probability": assessment["success_probability"],
            "capacity_constraints": "|".join(assessment["main_constraints"]),
        })
        obs = self._observation(latest, capacity)
        self.current_step += 1
        self.now += timedelta(minutes=self.dt_minutes)
        terminated = self.current_step >= self.max_steps
        return obs, float(reward), terminated, False, {"typical_human": self._info_row(latest, float(reward))}
