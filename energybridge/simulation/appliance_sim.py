"""Independent per-appliance simulator for EnergyBridge family home benchmark.

Each appliance is modelled and controlled independently — shifting one device
has no effect on any other device. The agent calls shift() or
preheat_water_heater() to reschedule individual appliances; step() is called
every EnergyPlus timestep to compute instantaneous load and track VPP
interaction.

Design rules
------------
* Independence: every appliance carries its own state; no shared mutable data.
* Per-day reset: shiftable tasks run once per day and are initialised at start.
* Overnight windows: dishwasher often has latest_h < earliest_h (e.g. 19→7).
  These are handled as absolute sim-hours.
* Precision: shift(name, day_idx, abs_sim_h) accepts absolute simulation hour.
* VPP tracking: each appliance independently records ran_during_vpp.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List


def _day_of(sim_h: float) -> int:
    return int(sim_h // 24)


@dataclass
class _DayRecord:
    scheduled_abs_h: float
    run_start_abs_h: Optional[float] = None
    completed: bool = False
    ran_during_vpp: bool = False


class ShiftableAppliance:
    """Washer / dishwasher / dryer — runs once per day within a user window."""

    def __init__(self, name: str, config: dict, sim_days: int = 3) -> None:
        self.name = name
        self.present: bool = bool(config.get("present", True))
        self.earliest_h: float = float(config.get("earliest_h", 8.0))
        self.latest_h: float = float(config.get("latest_h", 22.0))
        self.preferred_h: float = float(config.get("preferred_h", 14.0))
        self.duration_h: float = float(config.get("duration_h", 2.0))
        self.power_kw: float = float(config.get("power_kw", 1.5))
        self.shiftable: bool = bool(config.get("shiftable", True))
        self.dr_adjustable: bool = bool(config.get("dr_adjustable", True))
        self._overnight: bool = (self.latest_h < self.earliest_h)
        self._days: Dict[int, _DayRecord] = {
            d: _DayRecord(scheduled_abs_h=self._default_start(d))
            for d in range(sim_days)
        }

    def _default_start(self, day_idx: int) -> float:
        base = day_idx * 24
        if self._overnight and self.preferred_h < self.earliest_h:
            return base + 24 + self.preferred_h
        return base + self.preferred_h

    def _window_abs(self, day_idx: int):
        base = day_idx * 24
        abs_e = base + self.earliest_h
        abs_l = (base + 24 + self.latest_h) if self._overnight else (base + self.latest_h)
        return abs_e, abs_l

    def shift(self, day_idx: int, new_abs_h: float) -> bool:
        if not self.present or not self.shiftable or not self.dr_adjustable:
            return False
        rec = self._days.get(day_idx)
        if rec is None or rec.run_start_abs_h is not None or rec.completed:
            return False
        abs_e, abs_l = self._window_abs(day_idx)
        if abs_e <= new_abs_h <= abs_l - self.duration_h:
            rec.scheduled_abs_h = new_abs_h
            return True
        return False

    def step(self, sim_h: float, dt_h: float, vpp_active: bool) -> float:
        if not self.present:
            return 0.0
        day_idx = _day_of(sim_h)
        rec = self._days.get(day_idx)
        if rec is None or rec.completed:
            return 0.0
        if rec.run_start_abs_h is None and sim_h >= rec.scheduled_abs_h:
            rec.run_start_abs_h = sim_h
        if rec.run_start_abs_h is not None:
            elapsed = sim_h - rec.run_start_abs_h
            if elapsed >= self.duration_h:
                rec.completed = True
                return 0.0
            if vpp_active:
                rec.ran_during_vpp = True
            return self.power_kw
        return 0.0

    def day_result(self, day_idx: int) -> dict:
        rec = self._days.get(day_idx, _DayRecord(scheduled_abs_h=0))
        return {
            "name": self.name, "day": day_idx, "present": self.present,
            "completed": rec.completed, "ran_during_vpp": rec.ran_during_vpp,
            "scheduled_abs_h": rec.scheduled_abs_h,
        }

    def all_results(self) -> List[dict]:
        return [self.day_result(d) for d in sorted(self._days)]

    def status_str(self, sim_h: float) -> str:
        if not self.present:
            return f"{self.name}: not_present"
        day_idx = _day_of(sim_h)
        rec = self._days.get(day_idx)
        if rec is None:
            return f"{self.name}: n/a"
        if rec.completed:
            return f"{self.name}: done_today"
        if rec.run_start_abs_h is not None:
            remaining = max(0.0, (rec.run_start_abs_h + self.duration_h) - sim_h)
            return f"{self.name}: RUNNING ({remaining:.1f}h left)"
        sched_hod = rec.scheduled_abs_h % 24
        return (f"{self.name}: scheduled={sched_hod:.0f}:00 "
                f"window={self.earliest_h:.0f}:00-"
                f"{'(+1d)' if self._overnight else ''}{self.latest_h:.0f}:00 "
                f"{'[shiftable]' if self.shiftable else '[fixed]'}")


class WaterHeater:
    """Electric tank water heater — pre-heat thermal-storage controller."""

    def __init__(self, config: dict, sim_days: int = 3) -> None:
        self.present: bool = bool(config.get("present", True))
        self.rated_kw: float = float(config.get("rated_kw", 2.0))
        self.bath_required_h: float = float(config.get("bath_required_h", 21.0))
        self.dr_adjustable: bool = bool(config.get("dr_adjustable", True))
        self.pre_heat_window_start_h: float = float(config.get("pre_heat_window_start_h", 15.0))
        self.pre_heat_window_end_h: float = float(config.get("pre_heat_window_end_h", 18.0))
        self._normal_on_start: float = 17.0
        self._normal_on_end: float = 21.0
        self._days: Dict[int, dict] = {
            d: {"preheat_requested": False, "ready_at_bath": True,
                "ran_during_vpp": False, "energy_kwh": 0.0}
            for d in range(sim_days)
        }

    def request_preheat(self, day_idx: int) -> bool:
        if not self.present or not self.dr_adjustable:
            return False
        state = self._days.get(day_idx)
        if state is None:
            return False
        state["preheat_requested"] = True
        return True

    def step(self, sim_h: float, dt_h: float, vpp_active: bool) -> float:
        if not self.present:
            return 0.0
        day_idx = _day_of(sim_h)
        state = self._days.get(day_idx)
        if state is None:
            return 0.0
        hod = sim_h % 24
        heating = False
        if state["preheat_requested"] and self.dr_adjustable:
            if self.pre_heat_window_start_h <= hod < self.pre_heat_window_end_h:
                heating = True
        else:
            if self._normal_on_start <= hod < self._normal_on_end:
                heating = True
        if heating:
            if vpp_active:
                state["ran_during_vpp"] = True
            state["energy_kwh"] += self.rated_kw * dt_h
            return self.rated_kw
        if hod >= self.bath_required_h - 0.5:
            state["ready_at_bath"] = True
        return 0.0

    def day_result(self, day_idx: int) -> dict:
        state = self._days.get(day_idx, {})
        return {
            "name": "water_heater", "day": day_idx, "present": self.present,
            "ready_at_bath": state.get("ready_at_bath", True),
            "ran_during_vpp": state.get("ran_during_vpp", False),
            "preheat_used": state.get("preheat_requested", False),
            "energy_kwh": round(state.get("energy_kwh", 0.0), 3),
        }

    def all_results(self) -> List[dict]:
        return [self.day_result(d) for d in sorted(self._days)]

    def status_str(self, sim_h: float) -> str:
        if not self.present:
            return "water_heater: not_present"
        day_idx = _day_of(sim_h)
        state = self._days.get(day_idx, {})
        preheat = state.get("preheat_requested", False)
        adj = "dr_adjustable" if self.dr_adjustable else "fixed_schedule"
        mode = "preheat_mode" if preheat else "normal_mode"
        return (f"water_heater: [{adj}] [{mode}] "
                f"bath_required={self.bath_required_h:.0f}:00 "
                f"preheat_window={self.pre_heat_window_start_h:.0f}:00-"
                f"{self.pre_heat_window_end_h:.0f}:00")


class EVCharger:
    """Smart EV home charger — SOC-based charging within home window."""

    def __init__(self, config: dict, sim_days: int = 3) -> None:
        self.present: bool = bool(config.get("present", False))
        self.charger_kw: float = float(config.get("charger_kw", 7.0))
        self.capacity_kwh: float = float(config.get("capacity_kwh", 60.0))
        self.target_soc: float = float(config.get("target_soc", 0.80))
        self.min_soc: float = float(config.get("min_soc", 0.15))
        self.arrival_h: float = float(config.get("arrival_h", 18.0))
        self.departure_h: float = float(config.get("departure_h", 7.5))
        self.daily_drive_kwh: float = float(config.get("daily_drive_kwh", 8.0))
        self.efficiency: float = float(config.get("efficiency", 0.92))
        # Start at target SOC (EV was already charged before sim begins)
        self._soc: float = self.target_soc
        self._departed: set = set()
        self._day_mode: Dict[int, str] = {d: "smart" for d in range(sim_days)}
        self._day_ran_during_vpp: Dict[int, bool] = {d: False for d in range(sim_days)}
        self._day_target_reached: Dict[int, bool] = {d: False for d in range(sim_days)}
        self._day_energy_kwh: Dict[int, float] = {d: 0.0 for d in range(sim_days)}

    def set_charge_mode(self, day_idx: int, mode: str) -> bool:
        if not self.present or mode not in ("smart", "delay", "normal"):
            return False
        if day_idx in self._day_mode:
            self._day_mode[day_idx] = mode
            return True
        return False

    def _is_home(self, hod: float) -> bool:
        if self.arrival_h > self.departure_h:
            return hod >= self.arrival_h or hod < self.departure_h
        return self.arrival_h <= hod < self.departure_h

    def _should_charge(self, day_idx: int, hod: float, vpp_active: bool) -> bool:
        if self._soc >= self.target_soc:
            return False
        mode = self._day_mode.get(day_idx, "smart")
        if mode == "normal":
            return True
        if mode == "delay":
            return hod >= 22.0 or hod < self.departure_h
        # smart: avoid VPP
        return not vpp_active

    def step(self, sim_h: float, dt_h: float, vpp_active: bool) -> float:
        if not self.present:
            return 0.0
        day_idx = _day_of(sim_h)
        hod = sim_h % 24
        # Departure energy deduction
        if (day_idx not in self._departed and
                self.departure_h <= hod < self.departure_h + max(dt_h, 1e-6)):
            self._soc = max(self.min_soc,
                            self._soc - self.daily_drive_kwh / self.capacity_kwh)
            self._departed.add(day_idx)
        if not self._is_home(hod):
            return 0.0
        if not self._should_charge(day_idx, hod, vpp_active):
            return 0.0
        needed_kwh = (self.target_soc - self._soc) * self.capacity_kwh
        if needed_kwh <= 0:
            return 0.0
        delivered_kwh = self.charger_kw * self.efficiency * max(dt_h, 1e-9)
        fraction = min(1.0, needed_kwh / delivered_kwh)
        gain = fraction * self.charger_kw * self.efficiency * dt_h
        self._soc = min(1.0, self._soc + gain / self.capacity_kwh)
        if self._soc >= self.target_soc:
            # Attribute target_reached to arrival day, not completion day.
            # If hod < arrival_h, the session started the previous evening.
            arrival_day = day_idx - 1 if (hod < self.arrival_h and day_idx > 0) else day_idx
            if arrival_day in self._day_target_reached:
                self._day_target_reached[arrival_day] = True
        if vpp_active:
            self._day_ran_during_vpp[day_idx] = True
        power = fraction * self.charger_kw
        self._day_energy_kwh[day_idx] = self._day_energy_kwh.get(day_idx, 0.0) + power * dt_h
        return power

    def day_result(self, day_idx: int) -> dict:
        return {
            "name": "ev", "day": day_idx, "present": self.present,
            "target_reached": self._day_target_reached.get(day_idx, False),
            "ran_during_vpp": self._day_ran_during_vpp.get(day_idx, False),
            "mode": self._day_mode.get(day_idx, "smart"),
            "soc_end": round(self._soc, 3),
            "energy_kwh": round(self._day_energy_kwh.get(day_idx, 0.0), 3),
        }

    def all_results(self) -> List[dict]:
        return [self.day_result(d) for d in sorted(self._day_mode)]

    def status_str(self, sim_h: float) -> str:
        if not self.present:
            return "ev: not_present"
        day_idx = _day_of(sim_h)
        hod = sim_h % 24
        at_home = self._is_home(hod)
        mode = self._day_mode.get(day_idx, "smart")
        return (f"ev: SOC={self._soc*100:.0f}% target={self.target_soc*100:.0f}% "
                f"[{'at_home' if at_home else 'away'}] mode={mode} "
                f"arrival={self.arrival_h:.0f}:00")


class Refrigerator:
    """Always-on baseline load — uncontrollable; tracks VPP exposure only."""

    def __init__(self, config: dict) -> None:
        self.present: bool = bool(config.get("present", True))
        self.power_kw: float = float(config.get("power_kw", 0.15))

    def step(self, sim_h: float, dt_h: float, vpp_active: bool) -> float:
        return self.power_kw if self.present else 0.0

    def status_str(self, sim_h: float) -> str:
        if not self.present:
            return "refrigerator: not_present"
        return f"refrigerator: always_on {self.power_kw:.2f}kW [uncontrollable]"


class ApplianceSuite:
    """Top-level manager: all appliances for one persona, fully independent."""

    SHIFTABLE_NAMES = ("washer", "dishwasher", "dryer")

    def __init__(self, appliance_config: dict, sim_days: int = 3,
                 vpp_events: Optional[List[dict]] = None) -> None:
        self._vpp_events = vpp_events or []
        self._sim_days = sim_days
        self._shiftable: Dict[str, ShiftableAppliance] = {}
        for nm in self.SHIFTABLE_NAMES:
            raw = appliance_config.get(nm, {})
            cfg = raw if isinstance(raw, dict) and raw else {"present": False}
            self._shiftable[nm] = ShiftableAppliance(nm, cfg, sim_days)
        wh_cfg = appliance_config.get("water_heater", {})
        self._water_heater = WaterHeater(wh_cfg if isinstance(wh_cfg, dict) else {}, sim_days)
        ev_cfg = appliance_config.get("ev", {})
        self._ev = EVCharger(ev_cfg if isinstance(ev_cfg, dict) else {}, sim_days)
        rf_cfg = appliance_config.get("refrigerator", {})
        self._refrigerator = Refrigerator(rf_cfg if isinstance(rf_cfg, dict) else {"present": True})
        # Auto-request preheat for all VPP-event days (default=on; agent can override)
        for _ev in self._vpp_events:
            self._water_heater.request_preheat(int(_ev["trigger_h"] // 24))

    def _is_vpp_active(self, sim_h: float) -> bool:
        for ev in self._vpp_events:
            if ev["trigger_h"] <= sim_h < ev["end_h"]:
                return True
        return False

    def shift_appliance(self, name: str, day_idx: int, new_abs_h: float) -> bool:
        app = self._shiftable.get(name)
        return app.shift(day_idx, new_abs_h) if app is not None else False

    def preheat_water_heater(self, day_idx: int) -> bool:
        return self._water_heater.request_preheat(day_idx)

    def set_ev_mode(self, day_idx: int, mode: str) -> bool:
        return self._ev.set_charge_mode(day_idx, mode)

    def step(self, sim_h: float, dt_h: float) -> Dict[str, float]:
        """Advance all appliances independently. Returns {name: power_kw}."""
        vpp = self._is_vpp_active(sim_h)
        powers: Dict[str, float] = {}
        for nm, app in self._shiftable.items():
            powers[nm] = app.step(sim_h, dt_h, vpp)
        powers["water_heater"] = self._water_heater.step(sim_h, dt_h, vpp)
        powers["ev"] = self._ev.step(sim_h, dt_h, vpp)
        powers["refrigerator"] = self._refrigerator.step(sim_h, dt_h, vpp)
        return powers

    def status_lines(self, sim_h: float) -> List[str]:
        lines: List[str] = []
        for app in self._shiftable.values():
            lines.append(app.status_str(sim_h))
        lines.append(self._water_heater.status_str(sim_h))
        lines.append(self._ev.status_str(sim_h))
        lines.append(self._refrigerator.status_str(sim_h))
        return lines

    def all_results(self) -> Dict[str, List[dict]]:
        out: Dict[str, List[dict]] = {}
        for nm, app in self._shiftable.items():
            out[nm] = app.all_results()
        out["water_heater"] = self._water_heater.all_results()
        out["ev"] = self._ev.all_results()
        return out

    def vpp_day_summary(self, day_idx: int) -> dict:
        """Per-appliance VPP interaction summary for one day."""
        summary: Dict[str, Any] = {}
        for nm, app in self._shiftable.items():
            r = app.day_result(day_idx)
            summary[nm] = {"present": r["present"], "completed": r["completed"],
                           "ran_during_vpp": r["ran_during_vpp"]}
        wh = self._water_heater.day_result(day_idx)
        summary["water_heater"] = {"present": wh["present"],
                                   "ready_at_bath": wh["ready_at_bath"],
                                   "ran_during_vpp": wh["ran_during_vpp"]}
        ev = self._ev.day_result(day_idx)
        summary["ev"] = {"present": ev["present"], "target_reached": ev["target_reached"],
                         "ran_during_vpp": ev["ran_during_vpp"], "mode": ev["mode"]}
        return summary
