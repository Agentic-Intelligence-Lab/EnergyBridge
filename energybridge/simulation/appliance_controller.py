"""Background automation controllers for EV charger and electric water heater.

These controllers mirror the logic in Family_Model/control_model/control_model.py
but are integrated into the EnergyBridge co-simulation loop so that EV and EWH
operate realistically during every EP timestep.

The controllers run as *background automation* (every timestep).  The DR agent
can later override them by writing different values via the ActuatorWriter.

Classes
-------
EVModel
    Tracks battery SOC and decides a charging fraction (0–1) each timestep.
ElectricWaterHeaterController
    Decides setpoint (°C) and availability (0/1) based on time-of-day and SOC.
ApplianceController
    Combines both; call ``step()`` each timestep to get actuator values.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# EV model
# ---------------------------------------------------------------------------


@dataclass
class EVModel:
    """Simple home EV battery and charging model."""

    capacity_kwh: float = 60.0
    charger_kw: float = 7.0
    efficiency: float = 0.92
    soc: float = 0.50          # initial state-of-charge (fraction)
    target_soc: float = 0.80
    minimum_soc: float = 0.15
    arrival_hour: float = 18.0    # hour-of-day when EV arrives home
    departure_hour: float = 7.5   # hour-of-day when EV leaves
    daily_drive_kwh: float = 8.0  # energy consumed each commute
    last_departure_day: Optional[int] = field(default=None, repr=False)

    def is_home(self, hour: float) -> bool:
        """True when EV is plugged in (overnight presence)."""
        if self.arrival_hour > self.departure_hour:
            return hour >= self.arrival_hour or hour < self.departure_hour
        return self.arrival_hour <= hour < self.departure_hour

    def apply_departure_drive(self, day_of_year: int, hour: float, dt_hours: float) -> None:
        """Deduct daily driving energy once per departure window."""
        in_departure_step = (
            self.departure_hour <= hour < self.departure_hour + max(dt_hours, 1e-9)
        )
        if in_departure_step and self.last_departure_day != day_of_year:
            self.soc = max(self.minimum_soc, self.soc - self.daily_drive_kwh / self.capacity_kwh)
            self.last_departure_day = day_of_year

    def decide_charging_fraction(self, hour: float, dt_hours: float) -> float:
        """Return 0–1 charging fraction for this timestep."""
        if not self.is_home(hour) or self.soc >= self.target_soc:
            return 0.0
        required_kwh = (self.target_soc - self.soc) * self.capacity_kwh
        delivered_kwh = self.charger_kw * self.efficiency * max(dt_hours, 1e-9)
        return min(1.0, max(0.0, required_kwh / delivered_kwh))

    def apply_charge(self, charging_fraction: float, dt_hours: float) -> None:
        """Update SOC after charging for one timestep."""
        battery_gain = charging_fraction * self.charger_kw * self.efficiency * dt_hours
        self.soc = min(1.0, self.soc + battery_gain / self.capacity_kwh)


# ---------------------------------------------------------------------------
# Electric water heater controller
# ---------------------------------------------------------------------------


@dataclass
class ElectricWaterHeaterController:
    """Time-of-use setpoint controller for WaterHeater:Stratified."""

    normal_setpoint_c: float = 50.0
    comfort_setpoint_c: float = 55.0
    setback_setpoint_c: float = 47.0
    minimum_tank_temp_c: float = 43.0
    morning_start: float = 6.0
    morning_end: float = 8.5
    evening_start: float = 18.0
    evening_end: float = 23.0

    def expected_draw_period(self, hour: float) -> bool:
        """True during typical hot-water usage hours."""
        return (self.morning_start <= hour < self.morning_end) or (
            self.evening_start <= hour < self.evening_end
        )

    def decide(
        self,
        hour: float,
        tank_temp_c: Optional[float],
        ev_fraction: float,
    ) -> tuple[float, float]:
        """Return (availability 0/1, setpoint °C) for this timestep."""
        if tank_temp_c is not None and tank_temp_c < self.minimum_tank_temp_c:
            return 1.0, self.comfort_setpoint_c
        if self.expected_draw_period(hour):
            return 1.0, self.comfort_setpoint_c
        if ev_fraction > 0.0:
            return 1.0, self.setback_setpoint_c
        return 1.0, self.normal_setpoint_c


# ---------------------------------------------------------------------------
# Combined controller
# ---------------------------------------------------------------------------


class ApplianceController:
    """Runs EV and EWH background automation each timestep.

    Usage inside a pyenergyplus callback::

        ctrl = ApplianceController()
        # each timestep:
        values = ctrl.step(
            hour=api.exchange.current_time(s),
            dt_hours=api.exchange.zone_time_step(s),
            day_of_year=api.exchange.day_of_year(s),
            tank_temp_c=<read from EP>,
        )
        # values has keys: ev_fraction, ev_grid_power_w,
        #                  ewh_setpoint_c, ewh_availability,
        #                  ev_soc
    """

    def __init__(
        self,
        ev: Optional[EVModel] = None,
        ewh: Optional[ElectricWaterHeaterController] = None,
    ) -> None:
        self.ev = ev or EVModel()
        self.ewh = ewh or ElectricWaterHeaterController()
        # Cache last-written values for logging
        self._last_ev_fraction: float = 0.0
        self._last_ewh_setpoint: float = self.ewh.normal_setpoint_c
        self._last_ewh_availability: float = 1.0

    def step(
        self,
        hour: float,
        dt_hours: float,
        day_of_year: int,
        tank_temp_c: Optional[float] = None,
        warmup: bool = False,
    ) -> dict:
        """Compute actuator values for this timestep.

        During EnergyPlus warm-up, returns safe defaults without updating state.
        """
        if warmup:
            return {
                "ev_fraction": 0.0,
                "ev_grid_power_w": 0.0,
                "ewh_setpoint_c": self.ewh.normal_setpoint_c,
                "ewh_availability": 1.0,
                "ev_soc": self.ev.soc,
            }

        self.ev.apply_departure_drive(day_of_year, hour, dt_hours)
        ev_fraction = self.ev.decide_charging_fraction(hour, dt_hours)
        self.ev.apply_charge(ev_fraction, dt_hours)
        ewh_availability, ewh_setpoint = self.ewh.decide(hour, tank_temp_c, ev_fraction)

        self._last_ev_fraction = ev_fraction
        self._last_ewh_setpoint = ewh_setpoint
        self._last_ewh_availability = ewh_availability

        return {
            "ev_fraction": ev_fraction,
            "ev_grid_power_w": ev_fraction * self.ev.charger_kw * 1000.0,
            "ewh_setpoint_c": ewh_setpoint,
            "ewh_availability": ewh_availability,
            "ev_soc": self.ev.soc,
        }
