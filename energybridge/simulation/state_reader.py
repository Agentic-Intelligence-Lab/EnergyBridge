"""Read EnergyPlus output variables and convert them to EnergyBridge home_state format.

Usage inside a pyenergyplus callback::

    from energybridge.simulation.state_reader import StateReader

    reader = StateReader()
    reader.request_variables(api.exchange, state)   # call once at warm-up start
    reader.init_handles(api.exchange, state)         # call once when api_data_fully_ready
    home_state = reader.read(api.exchange, state)    # call every timestep
"""

from __future__ import annotations

from typing import Optional

from energybridge.simulation.variable_catalog import VARIABLES


class StateReader:
    """Reads EnergyPlus output variables and returns a home_state dict."""

    def __init__(self) -> None:
        self._handles: dict[str, int] = {}
        self._initialized: bool = False

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def request_variables(self, exchange, state) -> None:
        """Register all required output variables with EnergyPlus.

        Must be called before the simulation starts (e.g. in the
        callback_begin_new_environment callback or before api.run()).
        """
        for var_name, key in VARIABLES.values():
            exchange.request_variable(state, var_name, key)

    def init_handles(self, exchange, state) -> bool:
        """Obtain variable handles once api_data_fully_ready() returns True.

        Returns True when handles are successfully initialised, False otherwise.
        """
        if self._initialized:
            return True
        if not exchange.api_data_fully_ready(state):
            return False

        missing: list[str] = []
        for field, (var_name, key) in VARIABLES.items():
            handle = exchange.get_variable_handle(state, var_name, key)
            if handle == -1:
                missing.append(f"{var_name} / {key}")
            self._handles[field] = handle

        if missing:
            # Non-fatal: log and continue; missing fields will be None in home_state
            print(
                f"[StateReader] WARNING: could not get handles for: {', '.join(missing)}"
            )

        self._initialized = True
        return True

    # ------------------------------------------------------------------
    # Runtime read
    # ------------------------------------------------------------------

    def read(self, exchange, state) -> dict:
        """Return a home_state dict populated from current EnergyPlus values.

        Fields that could not be read are omitted from the dict so that
        downstream code can apply its own defaults.
        """
        if not self._initialized:
            return {}

        raw: dict[str, Optional[float]] = {}
        for field, handle in self._handles.items():
            if handle == -1:
                raw[field] = None
            else:
                raw[field] = exchange.get_variable_value(state, handle)

        home_state: dict = {}

        # Indoor temperature (°C)
        if raw.get("indoor_temp") is not None:
            home_state["indoor_temp"] = round(float(raw["indoor_temp"]), 2)

        # Outdoor temperature (°C)
        if raw.get("outdoor_temp") is not None:
            home_state["outdoor_temp"] = round(float(raw["outdoor_temp"]), 2)

        # HVAC cooling power (kW) – EnergyPlus reports in W
        if raw.get("cooling_rate_w") is not None:
            home_state["hvac_power_kw"] = round(float(raw["cooling_rate_w"]) / 1000.0, 3)
        else:
            home_state["hvac_power_kw"] = 0.0

        # Whole-building electricity demand (kW)
        if raw.get("facility_power_w") is not None:
            home_state["facility_power_kw"] = round(
                float(raw["facility_power_w"]) / 1000.0, 3
            )

        # HVAC setpoint is not a readable variable in the same way; we leave it
        # to the agent to track via its own state.  Default to 25 °C.
        home_state.setdefault("hvac_setpoint", 25.0)

        # Occupancy: not read from EnergyPlus in this version; assume occupied.
        home_state["occupancy"] = True

        return home_state
