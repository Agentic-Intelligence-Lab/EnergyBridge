#!/usr/bin/env python3
"""Example 15-zone control loop for the Tianjin medium office model.

The IDF contains one cooling and one heating Schedule:Constant object for each
conditioned zone. EnergyPlus exposes each schedule value as an actuator, using
the same actuator infrastructure that EMS/PythonPlugin logic uses internally:

  Component Type: Schedule:Constant
  Control Type:   Schedule Value
  Actuator Key:   <ZONE>_CLG_SP_CONTROL or <ZONE>_HTG_SP_CONTROL

This script drives those actuators from pyenergyplus and records whether the
reported thermostat setpoint and zone air temperature follow the commands.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional


try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[3] / ".env")
except Exception:
    pass
EPLUS_ROOT = Path(os.getenv("EPLUS_ROOT", "/home/hku_user/EnergyPlus-24-1-0"))
if str(EPLUS_ROOT) not in sys.path:
    sys.path.insert(0, str(EPLUS_ROOT))

from pyenergyplus.api import EnergyPlusAPI  # noqa: E402


THIS_DIR = Path(__file__).resolve().parent
DEFAULT_IDF = THIS_DIR / "medium_office_tianjin.idf"
DEFAULT_WEATHER = Path(
    "/home/ha_agent/work/supporting/weather/epw/tianjin_cswd.epw"
)
DEFAULT_OUTPUT = THIS_DIR / "run_15zone_control_example"

HEATING_SETPOINT_C = 16.0
UNOCCUPIED_COOLING_C = 28.0
COOLING_UNMET_TOLERANCE_C = 0.556

ZONES = [
    "Core_bottom",
    "Core_mid",
    "Core_top",
    "Perimeter_top_ZN_3",
    "Perimeter_top_ZN_2",
    "Perimeter_top_ZN_1",
    "Perimeter_top_ZN_4",
    "Perimeter_bot_ZN_3",
    "Perimeter_bot_ZN_2",
    "Perimeter_bot_ZN_1",
    "Perimeter_bot_ZN_4",
    "Perimeter_mid_ZN_3",
    "Perimeter_mid_ZN_2",
    "Perimeter_mid_ZN_1",
    "Perimeter_mid_ZN_4",
]

UNIQUE_OCCUPIED_COOLING_C = {
    "Core_bottom": 24.0,
    "Core_mid": 25.0,
    "Core_top": 26.0,
    "Perimeter_top_ZN_3": 27.0,
    "Perimeter_top_ZN_2": 26.5,
    "Perimeter_top_ZN_1": 25.5,
    "Perimeter_top_ZN_4": 27.5,
    "Perimeter_bot_ZN_3": 23.0,
    "Perimeter_bot_ZN_2": 23.5,
    "Perimeter_bot_ZN_1": 24.0,
    "Perimeter_bot_ZN_4": 24.5,
    "Perimeter_mid_ZN_3": 25.0,
    "Perimeter_mid_ZN_2": 25.5,
    "Perimeter_mid_ZN_1": 26.0,
    "Perimeter_mid_ZN_4": 26.5,
}


def schedule_name(zone: str, kind: str) -> str:
    return f"{zone.upper()}_{kind}_SP_CONTROL"


def zone_group(zone: str) -> str:
    if zone.startswith("Core_"):
        return "Core"
    if "_bot_" in zone:
        return "Bottom perimeter"
    if "_mid_" in zone:
        return "Middle perimeter"
    if "_top_" in zone:
        return "Top perimeter"
    return "Other"


@dataclass(frozen=True)
class ControlPattern:
    mode: str
    occupied_start_hour: float = 8.0
    occupied_end_hour: float = 18.0

    def occupied(self, hour: float) -> bool:
        return self.occupied_start_hour <= hour < self.occupied_end_hour

    def cooling_setpoint(self, zone: str, hour: float) -> float:
        if not self.occupied(hour):
            return UNOCCUPIED_COOLING_C
        if self.mode == "unique":
            return UNIQUE_OCCUPIED_COOLING_C[zone]
        if self.mode == "uniform_24":
            return 24.0
        if self.mode == "uniform_26":
            return 26.0
        if zone.startswith("Core_"):
            return 26.0
        if "_bot_" in zone:
            return 23.0
        if "_mid_" in zone:
            return 25.0
        if "_top_" in zone:
            return 27.0
        return 26.0


@dataclass
class WeightedStats:
    hours: float = 0.0
    commanded_sp_hours: float = 0.0
    reported_sp_hours: float = 0.0
    temp_hours: float = 0.0
    cooling_rate_hours: float = 0.0
    abs_temp_error_hours: float = 0.0
    abs_setpoint_error_hours: float = 0.0
    unmet_hours: float = 0.0
    max_temp_c: float = -math.inf

    def add(
        self,
        dt: float,
        commanded_sp_c: float,
        reported_sp_c: float,
        temp_c: float,
        cooling_rate_w: float,
    ) -> None:
        self.hours += dt
        self.commanded_sp_hours += commanded_sp_c * dt
        self.reported_sp_hours += reported_sp_c * dt
        self.temp_hours += temp_c * dt
        self.cooling_rate_hours += cooling_rate_w * dt
        self.abs_temp_error_hours += abs(temp_c - commanded_sp_c) * dt
        self.abs_setpoint_error_hours += abs(reported_sp_c - commanded_sp_c) * dt
        if temp_c > commanded_sp_c + COOLING_UNMET_TOLERANCE_C:
            self.unmet_hours += dt
        self.max_temp_c = max(self.max_temp_c, temp_c)

    def mean(self, weighted_value: float) -> Optional[float]:
        if self.hours <= 0.0:
            return None
        return weighted_value / self.hours

    def as_dict(self) -> Dict[str, Optional[float]]:
        return {
            "occupied_hours": self.hours,
            "commanded_cooling_sp_c": self.mean(self.commanded_sp_hours),
            "reported_cooling_sp_c": self.mean(self.reported_sp_hours),
            "mean_zone_temp_c": self.mean(self.temp_hours),
            "max_zone_temp_c": self.max_temp_c if self.hours > 0.0 else None,
            "mean_zone_cooling_rate_w": self.mean(self.cooling_rate_hours),
            "mean_abs_temp_to_commanded_sp_c": self.mean(self.abs_temp_error_hours),
            "mean_abs_reported_sp_to_commanded_sp_c": self.mean(
                self.abs_setpoint_error_hours
            ),
            "occupied_cooling_unmet_h_over_0p556c": self.unmet_hours,
        }


@dataclass
class Controller:
    api: EnergyPlusAPI
    pattern: ControlPattern
    write_log: bool = False
    initialized: bool = False
    actuators: Dict[str, Dict[str, int]] = field(
        default_factory=lambda: {"cooling": {}, "heating": {}}
    )
    variables: Dict[str, Dict[str, int]] = field(
        default_factory=lambda: {
            "zone_temp": {},
            "reported_cooling_sp": {},
            "reported_heating_sp": {},
            "zone_cooling_rate": {},
        }
    )
    facility_variables: Dict[str, int] = field(default_factory=dict)
    last_cooling_sp: Dict[str, float] = field(
        default_factory=lambda: {zone: UNOCCUPIED_COOLING_C for zone in ZONES}
    )
    last_heating_sp: Dict[str, float] = field(
        default_factory=lambda: {zone: HEATING_SETPOINT_C for zone in ZONES}
    )
    stats: Dict[str, WeightedStats] = field(
        default_factory=lambda: {zone: WeightedStats() for zone in ZONES}
    )
    raw_rows: List[Dict[str, object]] = field(default_factory=list)
    hvac_demand_hours: float = 0.0
    hvac_demand_weighted_w: float = 0.0
    hvac_demand_max_w: float = 0.0

    @property
    def exchange(self):
        return self.api.exchange

    def request_variables(self, state) -> None:
        for zone in ZONES:
            self.exchange.request_variable(state, "Zone Mean Air Temperature", zone)
            self.exchange.request_variable(
                state, "Zone Thermostat Cooling Setpoint Temperature", zone
            )
            self.exchange.request_variable(
                state, "Zone Thermostat Heating Setpoint Temperature", zone
            )
            self.exchange.request_variable(
                state, "Zone Air System Sensible Cooling Rate", zone
            )
        self.exchange.request_variable(
            state, "Facility Total HVAC Electricity Demand Rate", "Whole Building"
        )

    def zone_variable_handle(self, state, variable_name: str, zone: str) -> int:
        handle = self.exchange.get_variable_handle(state, variable_name, zone)
        if handle == -1:
            handle = self.exchange.get_variable_handle(state, variable_name, zone.upper())
        return handle

    def initialize_handles(self, state) -> None:
        if self.initialized or not self.exchange.api_data_fully_ready(state):
            return

        for zone in ZONES:
            self.actuators["cooling"][zone] = self.exchange.get_actuator_handle(
                state,
                "Schedule:Constant",
                "Schedule Value",
                schedule_name(zone, "CLG"),
            )
            self.actuators["heating"][zone] = self.exchange.get_actuator_handle(
                state,
                "Schedule:Constant",
                "Schedule Value",
                schedule_name(zone, "HTG"),
            )
            self.variables["zone_temp"][zone] = self.zone_variable_handle(
                state, "Zone Mean Air Temperature", zone
            )
            self.variables["reported_cooling_sp"][zone] = self.zone_variable_handle(
                state, "Zone Thermostat Cooling Setpoint Temperature", zone
            )
            self.variables["reported_heating_sp"][zone] = self.zone_variable_handle(
                state, "Zone Thermostat Heating Setpoint Temperature", zone
            )
            self.variables["zone_cooling_rate"][zone] = self.zone_variable_handle(
                state, "Zone Air System Sensible Cooling Rate", zone
            )

        self.facility_variables["hvac_electricity_demand"] = (
            self.exchange.get_variable_handle(
                state, "Facility Total HVAC Electricity Demand Rate", "Whole Building"
            )
        )

        missing = []
        for kind, handles in self.actuators.items():
            missing.extend(f"{kind}:{zone}" for zone, handle in handles.items() if handle == -1)
        for kind, handles in self.variables.items():
            missing.extend(f"{kind}:{zone}" for zone, handle in handles.items() if handle == -1)
        if self.facility_variables["hvac_electricity_demand"] == -1:
            missing.append("facility:Facility Total HVAC Electricity Demand Rate")
        if missing:
            raise RuntimeError("Missing EnergyPlus handles: " + ", ".join(missing))

        self.initialized = True

    def variable_value(self, state, kind: str, zone: str) -> float:
        return self.exchange.get_variable_value(state, self.variables[kind][zone])

    def control_callback(self, state) -> None:
        self.initialize_handles(state)
        if not self.initialized:
            return

        hour = self.exchange.current_time(state)
        for zone in ZONES:
            cooling_sp = self.pattern.cooling_setpoint(zone, hour)
            heating_sp = HEATING_SETPOINT_C
            self.exchange.set_actuator_value(
                state, self.actuators["cooling"][zone], cooling_sp
            )
            self.exchange.set_actuator_value(
                state, self.actuators["heating"][zone], heating_sp
            )
            self.last_cooling_sp[zone] = cooling_sp
            self.last_heating_sp[zone] = heating_sp

    def logging_callback(self, state) -> None:
        if not self.initialized or self.exchange.warmup_flag(state):
            return

        hour = self.exchange.current_time(state)
        occupied = self.pattern.occupied(hour)
        dt = self.exchange.zone_time_step(state)

        hvac_handle = self.facility_variables["hvac_electricity_demand"]
        hvac_demand_w = self.exchange.get_variable_value(state, hvac_handle)
        self.hvac_demand_hours += dt
        self.hvac_demand_weighted_w += hvac_demand_w * dt
        self.hvac_demand_max_w = max(self.hvac_demand_max_w, hvac_demand_w)

        for zone in ZONES:
            temp_c = self.variable_value(state, "zone_temp", zone)
            reported_cooling_sp_c = self.variable_value(
                state, "reported_cooling_sp", zone
            )
            reported_heating_sp_c = self.variable_value(
                state, "reported_heating_sp", zone
            )
            cooling_rate_w = self.variable_value(state, "zone_cooling_rate", zone)
            commanded_cooling_sp_c = self.last_cooling_sp[zone]
            commanded_heating_sp_c = self.last_heating_sp[zone]

            if occupied:
                self.stats[zone].add(
                    dt,
                    commanded_cooling_sp_c,
                    reported_cooling_sp_c,
                    temp_c,
                    cooling_rate_w,
                )

            if self.write_log:
                self.raw_rows.append(
                    {
                        "month": self.exchange.month(state),
                        "day": self.exchange.day_of_month(state),
                        "hour": hour,
                        "dt_hours": dt,
                        "zone": zone,
                        "zone_group": zone_group(zone),
                        "occupied_control": int(occupied),
                        "commanded_heating_sp_c": commanded_heating_sp_c,
                        "commanded_cooling_sp_c": commanded_cooling_sp_c,
                        "reported_heating_sp_c": reported_heating_sp_c,
                        "reported_cooling_sp_c": reported_cooling_sp_c,
                        "zone_temp_c": temp_c,
                        "zone_air_system_sensible_cooling_rate_w": cooling_rate_w,
                        "facility_hvac_electricity_demand_w": hvac_demand_w,
                    }
                )

    def zone_summaries(self) -> List[Dict[str, object]]:
        rows = []
        for zone in ZONES:
            row = {
                "zone": zone,
                "zone_group": zone_group(zone),
            }
            row.update(self.stats[zone].as_dict())
            rows.append(row)
        return rows

    def group_summaries(self) -> List[Dict[str, object]]:
        rows = []
        for group in ("Core", "Bottom perimeter", "Middle perimeter", "Top perimeter"):
            members = [zone for zone in ZONES if zone_group(zone) == group]
            combined = WeightedStats()
            for zone in members:
                stats = self.stats[zone]
                combined.hours += stats.hours
                combined.commanded_sp_hours += stats.commanded_sp_hours
                combined.reported_sp_hours += stats.reported_sp_hours
                combined.temp_hours += stats.temp_hours
                combined.cooling_rate_hours += stats.cooling_rate_hours
                combined.abs_temp_error_hours += stats.abs_temp_error_hours
                combined.abs_setpoint_error_hours += stats.abs_setpoint_error_hours
                combined.unmet_hours += stats.unmet_hours
                combined.max_temp_c = max(combined.max_temp_c, stats.max_temp_c)
            row = {"zone_group": group, "zones": ";".join(members)}
            row.update(combined.as_dict())
            rows.append(row)
        return rows

    def run_summary(self, exit_code: int, output_dir: Path, idf_path: Path, weather: Path) -> Dict[str, object]:
        zone_rows = self.zone_summaries()
        max_setpoint_error = max(
            row["mean_abs_reported_sp_to_commanded_sp_c"] or 0.0 for row in zone_rows
        )
        max_mean_temp_error = max(
            row["mean_abs_temp_to_commanded_sp_c"] or 0.0 for row in zone_rows
        )
        max_unmet = max(row["occupied_cooling_unmet_h_over_0p556c"] or 0.0 for row in zone_rows)
        mean_hvac_demand = (
            self.hvac_demand_weighted_w / self.hvac_demand_hours
            if self.hvac_demand_hours > 0.0
            else None
        )
        return {
            "energyplus_exit_code": exit_code,
            "idf": str(idf_path),
            "weather": str(weather),
            "output_dir": str(output_dir),
            "pattern": self.pattern.mode,
            "controlled_zone_count": len(ZONES),
            "max_mean_abs_reported_sp_to_commanded_sp_c": max_setpoint_error,
            "max_mean_abs_temp_to_commanded_sp_c": max_mean_temp_error,
            "max_zone_unmet_h_over_0p556c": max_unmet,
            "mean_facility_hvac_electricity_demand_w": mean_hvac_demand,
            "max_facility_hvac_electricity_demand_w": self.hvac_demand_max_w,
        }


def write_csv(path: Path, rows: List[Dict[str, object]]) -> None:
    if not rows:
        return
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def cleanup_energyplus_outputs(run_dir: Path) -> None:
    keep = {"eplusout.err", "eplustbl.htm", "eplustbl.csv", "eplusout.audit", "eplusout.end"}
    for path in run_dir.iterdir():
        if path.is_file() and path.name not in keep:
            path.unlink()


def run_control(
    idf_path: Path,
    weather_path: Path,
    output_dir: Path,
    pattern: ControlPattern,
    write_log: bool,
    keep_energyplus_output: bool,
) -> Dict[str, object]:
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)
    run_dir = output_dir / "energyplus_run"
    run_dir.mkdir()

    api = EnergyPlusAPI()
    state = api.state_manager.new_state()
    api.runtime.set_console_output_status(state, False)

    controller = Controller(api=api, pattern=pattern, write_log=write_log)
    controller.request_variables(state)
    api.runtime.callback_begin_system_timestep_before_predictor(
        state, controller.control_callback
    )
    api.runtime.callback_end_zone_timestep_after_zone_reporting(
        state, controller.logging_callback
    )
    exit_code = api.runtime.run_energyplus(
        state, ["-w", str(weather_path), "-d", str(run_dir), "-r", str(idf_path)]
    )
    api.state_manager.delete_state(state)

    zone_rows = controller.zone_summaries()
    group_rows = controller.group_summaries()
    write_csv(output_dir / "zone_control_zone_summary.csv", zone_rows)
    write_csv(output_dir / "zone_control_group_summary.csv", group_rows)
    if write_log:
        write_csv(output_dir / "zone_control_log.csv", controller.raw_rows)

    summary = controller.run_summary(exit_code, output_dir, idf_path, weather_path)
    summary["zone_summary_csv"] = str(output_dir / "zone_control_zone_summary.csv")
    summary["group_summary_csv"] = str(output_dir / "zone_control_group_summary.csv")
    (output_dir / "zone_control_summary.json").write_text(json.dumps(summary, indent=2))

    if not keep_energyplus_output:
        cleanup_energyplus_outputs(run_dir)
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a 15-zone EMS-style actuator control example."
    )
    parser.add_argument("--idf", type=Path, default=DEFAULT_IDF)
    parser.add_argument("--weather", type=Path, default=DEFAULT_WEATHER)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--pattern",
        choices=["unique", "uniform_24", "uniform_26", "staggered"],
        default="unique",
    )
    parser.add_argument("--occupied-start-hour", type=float, default=8.0)
    parser.add_argument("--occupied-end-hour", type=float, default=18.0)
    parser.add_argument("--write-log", action="store_true")
    parser.add_argument("--keep-energyplus-output", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    pattern = ControlPattern(
        mode=args.pattern,
        occupied_start_hour=args.occupied_start_hour,
        occupied_end_hour=args.occupied_end_hour,
    )
    summary = run_control(
        idf_path=args.idf,
        weather_path=args.weather,
        output_dir=args.output,
        pattern=pattern,
        write_log=args.write_log,
        keep_energyplus_output=args.keep_energyplus_output,
    )

    print(f"EnergyPlus exit code: {summary['energyplus_exit_code']}")
    print(f"Controlled zones: {summary['controlled_zone_count']}")
    print(
        "Max setpoint tracking error: "
        f"{summary['max_mean_abs_reported_sp_to_commanded_sp_c']:.6f} C"
    )
    print(
        "Max mean temperature error: "
        f"{summary['max_mean_abs_temp_to_commanded_sp_c']:.3f} C"
    )
    print(
        "Max cooling unmet hours (>0.556 C): "
        f"{summary['max_zone_unmet_h_over_0p556c']:.2f} h"
    )
    print(f"Zone summary: {summary['zone_summary_csv']}")
    print(f"Group summary: {summary['group_summary_csv']}")
    return int(summary["energyplus_exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
