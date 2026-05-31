#!/usr/bin/env python3
"""External EV and electric-water-heater control model for Family_Simple.idf.

The IDF exposes three controllable Schedule:Constant objects:

- EV_Charging_Fraction_Control: 0..1 multiplier on the 7 kW EV charger.
- EWH_Setpoint_Control: electric water heater setpoint in degC.
- EWH_Availability_Control: 0/1 availability for the DHW plant operation.

This script starts EnergyPlus through pyenergyplus, evaluates the external
control model at each zone timestep, and writes these schedule actuators.
"""

from __future__ import annotations

import argparse
import csv
import math
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional


try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
except Exception:
    pass
EPLUS_ROOT = Path(os.getenv("EPLUS_ROOT", "/home/ha_agent/EnergyPlus-24-1-0"))
if str(EPLUS_ROOT) not in sys.path:
    sys.path.insert(0, str(EPLUS_ROOT))

from pyenergyplus.api import EnergyPlusAPI  # noqa: E402


THIS_DIR = Path(__file__).resolve().parent
FAMILY_DIR = THIS_DIR.parent
DEFAULT_IDF = FAMILY_DIR / "Family_Simple.idf"
DEFAULT_WEATHER = (
    FAMILY_DIR.parent
    / "Weather"
    / "Tianjin"
    / "CHN_Tianjin.Tianjin.545270_CSWD.epw"
)
DEFAULT_OUTPUT = FAMILY_DIR / "run_control_model"


@dataclass
class EVModel:
    """Simple home EV battery and charging model."""

    capacity_kwh: float = 60.0
    charger_kw: float = 7.0
    efficiency: float = 0.92
    soc: float = 0.50
    target_soc: float = 0.80
    minimum_soc: float = 0.15
    arrival_hour: float = 18.0
    departure_hour: float = 7.5
    daily_drive_kwh: float = 8.0
    last_departure_day: Optional[int] = None

    def is_home(self, hour: float) -> bool:
        if self.arrival_hour > self.departure_hour:
            return hour >= self.arrival_hour or hour < self.departure_hour
        return self.arrival_hour <= hour < self.departure_hour

    def apply_departure_drive(self, day_of_year: int, hour: float, dt_hours: float) -> None:
        in_departure_step = self.departure_hour <= hour < self.departure_hour + max(dt_hours, 1e-9)
        if in_departure_step and self.last_departure_day != day_of_year:
            self.soc = max(self.minimum_soc, self.soc - self.daily_drive_kwh / self.capacity_kwh)
            self.last_departure_day = day_of_year

    def decide_charging_fraction(self, hour: float, dt_hours: float) -> float:
        if not self.is_home(hour) or self.soc >= self.target_soc:
            return 0.0
        required_battery_kwh = (self.target_soc - self.soc) * self.capacity_kwh
        delivered_battery_kwh = self.charger_kw * self.efficiency * max(dt_hours, 1e-9)
        return min(1.0, max(0.0, required_battery_kwh / delivered_battery_kwh))

    def apply_charge(self, charging_fraction: float, dt_hours: float) -> None:
        battery_gain = charging_fraction * self.charger_kw * self.efficiency * dt_hours
        self.soc = min(1.0, self.soc + battery_gain / self.capacity_kwh)


@dataclass
class ElectricWaterHeaterController:
    """Setpoint controller for EnergyPlus WaterHeater:Stratified."""

    normal_setpoint_c: float = 50.0
    comfort_setpoint_c: float = 55.0
    setback_setpoint_c: float = 47.0
    minimum_tank_temp_c: float = 43.0
    morning_start: float = 6.0
    morning_end: float = 8.5
    evening_start: float = 18.0
    evening_end: float = 23.0

    def expected_draw_period(self, hour: float) -> bool:
        return (self.morning_start <= hour < self.morning_end) or (
            self.evening_start <= hour < self.evening_end
        )

    def decide(self, hour: float, tank_temp_c: Optional[float], ev_fraction: float) -> tuple[float, float]:
        if tank_temp_c is not None and tank_temp_c < self.minimum_tank_temp_c:
            return 1.0, self.comfort_setpoint_c
        if self.expected_draw_period(hour):
            return 1.0, self.comfort_setpoint_c
        if ev_fraction > 0.0:
            return 1.0, self.setback_setpoint_c
        return 1.0, self.normal_setpoint_c


class FamilyControlModel:
    def __init__(self, api: EnergyPlusAPI, output_dir: Path, ev: EVModel, ewh: ElectricWaterHeaterController):
        self.api = api
        self.exchange = api.exchange
        self.output_dir = output_dir
        self.ev = ev
        self.ewh = ewh
        self.handles: Dict[str, int] = {}
        self.initialized = False
        self.rows: List[Dict[str, object]] = []
        self.last_ev_fraction = 0.0
        self.last_ewh_setpoint = ewh.normal_setpoint_c
        self.last_ewh_availability = 1.0

    def request_variables(self, state) -> None:
        requests = [
            ("Zone Mean Air Temperature", "living_unit1"),
            ("Electric Equipment Electricity Rate", "EV_Charger"),
            ("Water Heater Electricity Rate", "Water Heater_Tank_unit1"),
            ("Water Heater Tank Temperature", "Water Heater_Tank_unit1"),
            ("Facility Total Electricity Demand Rate", "Whole Building"),
            ("Schedule Value", "EV_Charging_Fraction_Control"),
            ("Schedule Value", "EWH_Setpoint_Control"),
            ("Schedule Value", "EWH_Availability_Control"),
        ]
        for variable_name, key in requests:
            self.exchange.request_variable(state, variable_name, key)

    def _handle(self, state, kind: str, *args: str) -> int:
        if kind == "actuator":
            return self.exchange.get_actuator_handle(state, *args)
        if kind == "variable":
            return self.exchange.get_variable_handle(state, *args)
        if kind == "meter":
            return self.exchange.get_meter_handle(state, args[0])
        raise ValueError(kind)

    def initialize_handles(self, state) -> None:
        if self.initialized or not self.exchange.api_data_fully_ready(state):
            return
        self.handles = {
            "ev_fraction_act": self._handle(
                state, "actuator", "Schedule:Constant", "Schedule Value", "EV_Charging_Fraction_Control"
            ),
            "ev_power_act": self._handle(
                state, "actuator", "ElectricEquipment", "Electricity Rate", "EV_Charger"
            ),
            "ewh_setpoint_act": self._handle(
                state, "actuator", "Schedule:Constant", "Schedule Value", "EWH_Setpoint_Control"
            ),
            "ewh_availability_act": self._handle(
                state, "actuator", "Schedule:Constant", "Schedule Value", "EWH_Availability_Control"
            ),
            "ewh_onoff_act": self._handle(
                state,
                "actuator",
                "Plant Component WaterHeater:Stratified",
                "On/Off Supervisory",
                "Water Heater_Tank_unit1",
            ),
            "zone_temp": self._handle(state, "variable", "Zone Mean Air Temperature", "living_unit1"),
            "ev_power": self._handle(state, "variable", "Electric Equipment Electricity Rate", "EV_Charger"),
            "ewh_power": self._handle(
                state, "variable", "Water Heater Electricity Rate", "Water Heater_Tank_unit1"
            ),
            "tank_temp": self._handle(
                state, "variable", "Water Heater Tank Temperature", "Water Heater_Tank_unit1"
            ),
            "facility_power": self._handle(
                state, "variable", "Facility Total Electricity Demand Rate", "Whole Building"
            ),
        }
        required = ["ev_fraction_act", "ev_power_act", "ewh_setpoint_act", "ewh_availability_act"]
        missing = [name for name in required if self.handles.get(name, -1) == -1]
        if missing:
            raise RuntimeError(f"EnergyPlus handles not found: {', '.join(missing)}")
        self.initialized = True

    def _variable(self, state, name: str) -> Optional[float]:
        handle = self.handles.get(name, -1)
        if handle == -1:
            return None
        return self.exchange.get_variable_value(state, handle)

    def control_callback(self, state) -> None:
        self.initialize_handles(state)
        if not self.initialized:
            return

        hour = self.exchange.current_time(state)
        dt_hours = self.exchange.zone_time_step(state)

        if self.exchange.warmup_flag(state):
            ev_fraction = 0.0
            ewh_availability = 1.0
            ewh_setpoint = self.ewh.normal_setpoint_c
        else:
            day = self.exchange.day_of_year(state)
            self.ev.apply_departure_drive(day, hour, dt_hours)
            ev_fraction = self.ev.decide_charging_fraction(hour, dt_hours)
            self.ev.apply_charge(ev_fraction, dt_hours)

            tank_temp = self._variable(state, "tank_temp")
            ewh_availability, ewh_setpoint = self.ewh.decide(hour, tank_temp, ev_fraction)

        self.exchange.set_actuator_value(state, self.handles["ev_fraction_act"], ev_fraction)
        self.exchange.set_actuator_value(
            state, self.handles["ev_power_act"], ev_fraction * self.ev.charger_kw * 1000.0
        )
        self.exchange.set_actuator_value(state, self.handles["ewh_setpoint_act"], ewh_setpoint)
        self.exchange.set_actuator_value(state, self.handles["ewh_availability_act"], ewh_availability)
        if self.handles.get("ewh_onoff_act", -1) != -1:
            self.exchange.set_actuator_value(state, self.handles["ewh_onoff_act"], ewh_availability)

        self.last_ev_fraction = ev_fraction
        self.last_ewh_setpoint = ewh_setpoint
        self.last_ewh_availability = ewh_availability

    def logging_callback(self, state) -> None:
        if not self.initialized or self.exchange.warmup_flag(state):
            return
        self.rows.append(
            {
                "month": self.exchange.month(state),
                "day": self.exchange.day_of_month(state),
                "day_of_year": self.exchange.day_of_year(state),
                "hour": self.exchange.current_time(state),
                "dt_hours": self.exchange.zone_time_step(state),
                "ev_soc": self.ev.soc,
                "ev_charge_fraction": self.last_ev_fraction,
                "ev_grid_power_w": self.last_ev_fraction * self.ev.charger_kw * 1000.0,
                "ewh_setpoint_c": self.last_ewh_setpoint,
                "ewh_availability": self.last_ewh_availability,
                "zone_temp_c": self._variable(state, "zone_temp"),
                "tank_temp_c": self._variable(state, "tank_temp"),
                "ev_eplus_power_w": self._variable(state, "ev_power"),
                "ewh_eplus_power_w": self._variable(state, "ewh_power"),
                "facility_electric_w": self._variable(state, "facility_power"),
            }
        )

    def write_log(self) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        log_path = self.output_dir / "control_model_log.csv"
        fieldnames = [
            "month",
            "day",
            "day_of_year",
            "hour",
            "dt_hours",
            "ev_soc",
            "ev_charge_fraction",
            "ev_grid_power_w",
            "ewh_setpoint_c",
            "ewh_availability",
            "zone_temp_c",
            "tank_temp_c",
            "ev_eplus_power_w",
            "ewh_eplus_power_w",
            "facility_electric_w",
        ]
        with log_path.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(self.rows)
        return log_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Family_Simple.idf with external EV/EWH control.")
    parser.add_argument("--idf", type=Path, default=DEFAULT_IDF)
    parser.add_argument("--weather", type=Path, default=DEFAULT_WEATHER)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--ev-capacity-kwh", type=float, default=60.0)
    parser.add_argument("--ev-charger-kw", type=float, default=7.0)
    parser.add_argument("--ev-efficiency", type=float, default=0.92)
    parser.add_argument("--ev-initial-soc", type=float, default=0.50)
    parser.add_argument("--ev-target-soc", type=float, default=0.80)
    parser.add_argument("--ev-daily-drive-kwh", type=float, default=8.0)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    api = EnergyPlusAPI()
    state = api.state_manager.new_state()
    api.runtime.set_console_output_status(state, False)

    ev = EVModel(
        capacity_kwh=args.ev_capacity_kwh,
        charger_kw=args.ev_charger_kw,
        efficiency=args.ev_efficiency,
        soc=args.ev_initial_soc,
        target_soc=args.ev_target_soc,
        daily_drive_kwh=args.ev_daily_drive_kwh,
    )
    controller = FamilyControlModel(api, args.output, ev, ElectricWaterHeaterController())
    controller.request_variables(state)

    api.runtime.callback_begin_system_timestep_before_predictor(state, controller.control_callback)
    api.runtime.callback_end_zone_timestep_after_zone_reporting(state, controller.logging_callback)

    command_line_args = ["-w", str(args.weather), "-d", str(args.output), str(args.idf)]
    exit_code = api.runtime.run_energyplus(state, command_line_args)
    log_path = controller.write_log()

    api.state_manager.delete_state(state)
    print(f"EnergyPlus exit code: {exit_code}")
    print(f"Control log: {log_path}")
    return int(exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
