"""Home simulation object for local state updates."""

from __future__ import annotations

from copy import deepcopy


class HomeSimulator:
    """Maintain the simulated home state across turns."""

    def __init__(self, initial_state: dict | None = None) -> None:
        self._state = initial_state or {
            "indoor_temp": 25.8,
            "outdoor_temp": 33.0,
            "hvac_setpoint": 25.0,
            "hvac_power_kw": 2.2,
            "occupancy": True,
        }

    def snapshot(self) -> dict:
        return deepcopy(self._state)

    def apply_control_result(self, result: dict) -> None:
        control_plan = result.get("control_plan", {})
        if "setpoint" in control_plan:
            self._state["hvac_setpoint"] = float(control_plan["setpoint"])
        self._state["indoor_temp"] = round(
            (float(self._state["indoor_temp"]) + float(self._state["hvac_setpoint"])) / 2.0,
            2,
        )
