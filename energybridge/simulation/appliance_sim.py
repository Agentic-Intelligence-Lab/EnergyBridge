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
    scheduled_abs_h: Optional[float]
    run_start_abs_h: Optional[float] = None
    completed: bool = False
    ran_during_vpp: bool = False


class ShiftableAppliance:
    """Washer / dishwasher / dryer — runs once per day within a user window."""

    def __init__(self, name: str, config: dict, sim_days: int = 3, *, explicit_only: bool = False) -> None:
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
        self.explicit_only = bool(explicit_only)
        self._days: Dict[int, _DayRecord] = {
            d: _DayRecord(scheduled_abs_h=None if self.explicit_only else self._default_start(d))
            for d in range(sim_days)
        }
        self._day_skipped: Dict[int, bool] = {d: False for d in range(sim_days)}

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

    def skip_today(self, day_idx: int) -> bool:
        """Tell the rule engine not to run this appliance today."""
        if not self.present or not self.shiftable or not self.dr_adjustable:
            return False
        self._day_skipped[day_idx] = True
        return True

    def step(self, sim_h: float, dt_h: float, vpp_active: bool) -> float:
        if not self.present:
            return 0.0
        day_idx = _day_of(sim_h)
        if self._day_skipped.get(day_idx, False):
            return 0.0
        rec = self._days.get(day_idx)
        if rec is None or rec.completed:
            return 0.0
        if rec.run_start_abs_h is None and rec.scheduled_abs_h is not None and sim_h >= rec.scheduled_abs_h:
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
        rec = self._days.get(day_idx, _DayRecord(scheduled_abs_h=None))
        return {
            "name": self.name, "day": day_idx, "present": self.present,
            "completed": rec.completed, "ran_during_vpp": rec.ran_during_vpp,
            "skipped": self._day_skipped.get(day_idx, False),
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
        if self._day_skipped.get(day_idx, False):
            return f"{self.name}: skipped_today"
        if rec.completed:
            return f"{self.name}: done_today"
        if rec.run_start_abs_h is not None:
            remaining = max(0.0, (rec.run_start_abs_h + self.duration_h) - sim_h)
            return f"{self.name}: RUNNING ({remaining:.1f}h left)"
        if rec.scheduled_abs_h is None:
            return (f"{self.name}: no_policy_command "
                    f"[preferred={self.preferred_h:.0f}:00 "
                    f"window={self.earliest_h:.0f}:00-"
                    f"{'(+1d)' if self._overnight else ''}{self.latest_h:.0f}:00] "
                    f"{'[shiftable]' if self.shiftable else '[fixed]'}")
        sched_hod = rec.scheduled_abs_h % 24
        return (f"{self.name}: scheduled={sched_hod:.0f}:00 "
                f"[preferred={self.preferred_h:.0f}:00 "
                f"window={self.earliest_h:.0f}:00-"
                f"{'(+1d)' if self._overnight else ''}{self.latest_h:.0f}:00] "
                f"{'[shiftable]' if self.shiftable else '[fixed]'}")


class WaterHeater:
    """Electric tank water heater — pre-heat thermal-storage controller."""

    def __init__(self, config: dict, sim_days: int = 3, *, explicit_only: bool = False) -> None:
        self.present: bool = bool(config.get("present", True))
        self.rated_kw: float = float(config.get("rated_kw", 2.0))
        self.bath_required_h: float = float(config.get("bath_required_h", 21.0))
        self.dr_adjustable: bool = bool(config.get("dr_adjustable", True))
        self.pre_heat_window_start_h: float = float(config.get("pre_heat_window_start_h", 15.0))
        self.pre_heat_window_end_h: float = float(config.get("pre_heat_window_end_h", 18.0))
        self._normal_on_start: float = 17.0
        self._normal_on_end: float = 21.0
        self.explicit_only = bool(explicit_only)
        self._days: Dict[int, dict] = {
            d: {"preheat_requested": False, "ready_at_bath": not self.explicit_only,
                "ran_during_vpp": False, "energy_kwh": 0.0,
                "preheat_start_h": None, "preheat_end_h": None,
                "preheat_temp_c": None}
            for d in range(sim_days)
        }

    def set_preheat_schedule(self, day_idx: int,
                               start_h: float | None = None,
                               end_h: float | None = None,
                               temp_c: float | None = None,
                               *,
                               force_routine: bool = False) -> bool:
        """Enable preheat for day_idx; optionally override window/temperature.

        start_h : hour-of-day to begin preheating  (e.g. 14.0 = 14:00).
        end_h   : hour-of-day to stop  preheating  (should be <= VPP start).
        temp_c  : tank setpoint during preheat (clamped 45-75 C).
        Unspecified params fall back to class defaults at execution time.
        """
        if not self.present or (not self.dr_adjustable and not force_routine):
            return False
        state = self._days.get(day_idx)
        if state is None:
            return False
        state["preheat_requested"] = True
        if start_h is not None:
            state["preheat_start_h"] = float(start_h)
        if end_h is not None:
            state["preheat_end_h"] = float(end_h)
        if temp_c is not None:
            state["preheat_temp_c"] = float(max(45.0, min(75.0, temp_c)))
        return True

    def request_preheat(self, day_idx: int) -> bool:
        """Enable the configured daily preheat routine.

        ``dr_adjustable=False`` means the Agent cannot reschedule the heater for
        demand response.  It should not disable the user's own fixed preheat
        window from the persona/calendar configuration.
        """
        return self.set_preheat_schedule(day_idx, force_routine=True)

    def step(self, sim_h: float, dt_h: float, vpp_active: bool) -> float:
        if not self.present:
            return 0.0
        day_idx = _day_of(sim_h)
        state = self._days.get(day_idx)
        if state is None:
            return 0.0
        hod = sim_h % 24
        heating = False
        if state["preheat_requested"]:
            ph_start = state.get("preheat_start_h")
            ph_end = state.get("preheat_end_h")
            start = self.pre_heat_window_start_h if ph_start is None else float(ph_start)
            end = self.pre_heat_window_end_h if ph_end is None else float(ph_end)
            if start <= hod < end:
                heating = True
        elif not self.explicit_only:
            if self._normal_on_start <= hod < self._normal_on_end:
                heating = True
        if heating:
            if vpp_active:
                state["ran_during_vpp"] = True
            state["energy_kwh"] += self.rated_kw * dt_h
            return self.rated_kw
        if hod >= self.bath_required_h - 0.5:
            state["ready_at_bath"] = (
                True if not self.explicit_only
                else float(state.get("energy_kwh", 0.0) or 0.0) > 0.0
            )
        return 0.0

    def day_result(self, day_idx: int) -> dict:
        state = self._days.get(day_idx, {})
        return {
            "name": "water_heater", "day": day_idx, "present": self.present,
            "ready_at_bath": state.get("ready_at_bath", not self.explicit_only),
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
        if preheat:
            ph_start = state.get("preheat_start_h") or self.pre_heat_window_start_h
            ph_end   = state.get("preheat_end_h")   or self.pre_heat_window_end_h
            ph_temp  = state.get("preheat_temp_c")  or 65.0
            mode = (f"preheat_mode window={ph_start:.0f}:00-{ph_end:.0f}:00 "
                    f"sp={ph_temp:.0f}C")
        else:
            mode = "no_policy_command" if self.explicit_only else "normal_mode"
        return (f"water_heater: [{adj}] [{mode}] "
                f"bath_required={self.bath_required_h:.0f}:00")


class EVCharger:
    """Smart EV home charger — SOC-based charging within home window."""

    def __init__(self, config: dict, sim_days: int = 3, *, explicit_only: bool = False) -> None:
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
        self.explicit_only = bool(explicit_only)
        self._day_mode: Dict[int, Optional[str]] = {d: None if self.explicit_only else "smart" for d in range(sim_days)}
        self._day_ran_during_vpp: Dict[int, bool] = {d: False for d in range(sim_days)}
        self._day_target_reached: Dict[int, bool] = {d: False for d in range(sim_days)}
        self._day_energy_kwh: Dict[int, float] = {d: 0.0 for d in range(sim_days)}
        # Per-day override: charge only within [start_h, end_h) if set by agent
        self._day_charge_start: Dict[int, Optional[float]] = {d: None for d in range(sim_days)}
        self._day_charge_end:   Dict[int, Optional[float]] = {d: None for d in range(sim_days)}

    def set_charge_mode(self, day_idx: int, mode: str) -> bool:
        if not self.present or mode not in ("smart", "delay", "normal"):
            return False
        if day_idx in self._day_mode:
            self._day_mode[day_idx] = mode
            return True
        return False

    def set_charge_window(self, day_idx: int,
                           start_h: float | None = None,
                           end_h: float | None = None) -> bool:
        """Override the charging window for a specific day.

        start_h: earliest hour-of-day to begin charging (e.g. 22.0 = 22:00).
        end_h:   latest  hour-of-day to stop  charging (e.g. 7.0 = 07:00 next day).
        Overrides mode-based timing; must be within home window.
        """
        if not self.present:
            return False
        if start_h is not None:
            self._day_charge_start[day_idx] = float(start_h)
        if end_h is not None:
            self._day_charge_end[day_idx] = float(end_h)
        return True

    def _is_home(self, hod: float) -> bool:
        if self.arrival_h > self.departure_h:
            return hod >= self.arrival_h or hod < self.departure_h
        return self.arrival_h <= hod < self.departure_h

    def _should_charge(self, day_idx: int, hod: float, vpp_active: bool) -> bool:
        if self._soc >= self.target_soc:
            return False
        # Per-day window override takes priority over mode
        ch_start = self._day_charge_start.get(day_idx)
        ch_end   = self._day_charge_end.get(day_idx)
        if ch_start is not None or ch_end is not None:
            s = ch_start if ch_start is not None else 0.0
            e = ch_end   if ch_end   is not None else 24.0
            if s <= e:
                in_window = s <= hod < e
            else:  # overnight window e.g. 22:00 -> 07:00
                in_window = hod >= s or hod < e
            return in_window and not vpp_active
        mode = self._day_mode.get(day_idx, None if self.explicit_only else "smart")
        if mode is None:
            return False
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
            "mode": self._day_mode.get(day_idx, None if self.explicit_only else "smart"),
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
        mode = self._day_mode.get(day_idx, None if self.explicit_only else "smart")
        ch_start = self._day_charge_start.get(day_idx)
        ch_end   = self._day_charge_end.get(day_idx)
        window_str = (f" charge_window={ch_start:.0f}:00-{ch_end:.0f}:00"
                      if ch_start is not None and ch_end is not None
                      else (f" charge_from={ch_start:.0f}:00" if ch_start is not None
                            else (f" charge_until={ch_end:.0f}:00" if ch_end is not None else "")))
        return (f"ev: SOC={self._soc*100:.0f}% target={self.target_soc*100:.0f}% "
                f"[{'at_home' if at_home else 'away'}] mode={mode or 'no_policy_command'}{window_str} "
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
                 vpp_events: Optional[List[dict]] = None,
                 *, explicit_only: bool = False) -> None:
        self._vpp_events = vpp_events or []
        self._sim_days = sim_days
        self.explicit_only = bool(explicit_only)
        self._shiftable: Dict[str, ShiftableAppliance] = {}
        for nm in self.SHIFTABLE_NAMES:
            raw = appliance_config.get(nm, {})
            cfg = raw if isinstance(raw, dict) and raw else {"present": False}
            self._shiftable[nm] = ShiftableAppliance(nm, cfg, sim_days, explicit_only=self.explicit_only)
        wh_cfg = appliance_config.get("water_heater", {})
        self._water_heater = WaterHeater(wh_cfg if isinstance(wh_cfg, dict) else {}, sim_days, explicit_only=self.explicit_only)
        ev_cfg = appliance_config.get("ev", {})
        self._ev = EVCharger(ev_cfg if isinstance(ev_cfg, dict) else {}, sim_days, explicit_only=self.explicit_only)
        rf_cfg = appliance_config.get("refrigerator", {})
        self._refrigerator = Refrigerator(rf_cfg if isinstance(rf_cfg, dict) else {"present": True})
        self._last_powers: Dict[str, float] = {}
        if not self.explicit_only:
            # Legacy behavior: auto-request preheat for VPP-event days.
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
        """Backward-compat: enable preheat with class-default window."""
        return self._water_heater.set_preheat_schedule(day_idx)

    def set_ewh_preheat_schedule(self, day_idx: int,
                                  start_h: float | None = None,
                                  end_h: float | None = None,
                                  temp_c: float | None = None) -> bool:
        """Enable preheat for day_idx with optional custom window/temperature."""
        return self._water_heater.set_preheat_schedule(
            day_idx, start_h=start_h, end_h=end_h, temp_c=temp_c)

    def skip_appliance(self, name: str, day_idx: int) -> bool:
        """Tell the rule engine not to run a shiftable appliance today."""
        app = self._shiftable.get(name)
        return app.skip_today(day_idx) if app is not None else False

    def set_ev_charge_window(self, day_idx: int,
                              start_h: float | None = None,
                              end_h: float | None = None) -> bool:
        """Set per-day EV charging window; also accepts mode string for compat."""
        return self._ev.set_charge_window(day_idx, start_h=start_h, end_h=end_h)

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
        self._last_powers = dict(powers)
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
                           "ran_during_vpp": r["ran_during_vpp"],
                           "skipped": r["skipped"]}
        wh = self._water_heater.day_result(day_idx)
        summary["water_heater"] = {"present": wh["present"],
                                   "ready_at_bath": wh["ready_at_bath"],
                                   "ran_during_vpp": wh["ran_during_vpp"]}
        ev = self._ev.day_result(day_idx)
        summary["ev"] = {"present": ev["present"], "target_reached": ev["target_reached"],
                         "ran_during_vpp": ev["ran_during_vpp"], "mode": ev["mode"]}
        return summary
