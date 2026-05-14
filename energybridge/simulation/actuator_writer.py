"""Write EnergyBridge control_plan decisions back to EnergyPlus actuators.

Usage inside a pyenergyplus callback::

    from energybridge.simulation.actuator_writer import ActuatorWriter

    writer = ActuatorWriter()
    writer.init_handles(api.exchange, state)          # call once when ready
    result = writer.apply(api.exchange, state, control_plan)  # call each decision
"""

from __future__ import annotations

from energybridge.simulation.variable_catalog import ACTUATORS

# Hard limits enforced before writing to EnergyPlus (independent of safety_checker)
_COOLING_SETPOINT_MIN = 18.0
_COOLING_SETPOINT_MAX = 30.0
_HEATING_SETPOINT_MIN = 15.0
_HEATING_SETPOINT_MAX = 26.0


class ActuatorWriter:
    """Translates a control_plan dict into EnergyPlus actuator writes."""

    def __init__(self) -> None:
        self._handles: dict[str, int] = {}
        self._initialized: bool = False
        # Track the last written setpoint so home_state can reflect it
        self._last_cooling_setpoint: float = 25.0
        self._last_heating_setpoint: float = 22.0

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def init_handles(self, exchange, state) -> bool:
        """Obtain actuator handles once api_data_fully_ready() returns True.

        Returns True when handles are successfully initialised.
        """
        if self._initialized:
            return True
        if not exchange.api_data_fully_ready(state):
            return False

        missing: list[str] = []
        for field, (comp_type, ctrl_type, key) in ACTUATORS.items():
            handle = exchange.get_actuator_handle(state, comp_type, ctrl_type, key)
            if handle == -1:
                missing.append(f"{comp_type} / {ctrl_type} / {key}")
            self._handles[field] = handle

        if missing:
            print(
                f"[ActuatorWriter] WARNING: could not get handles for: {', '.join(missing)}"
            )

        self._initialized = True
        return True

    # ------------------------------------------------------------------
    # Runtime write
    # ------------------------------------------------------------------

    def apply(self, exchange, state, control_plan: dict) -> dict:
        """Write control_plan to EnergyPlus actuators.

        Supported actions in control_plan["action"]:
          - "set_hvac_temperature": writes cooling_sch and heating_sch

        Returns an execution_result dict compatible with EnergyBridgeState.
        """
        if not self._initialized:
            return {
                "status": "skipped",
                "actuator": "eplus_actuator_v1",
                "reason": "handles_not_initialized",
            }

        action = control_plan.get("action", "")

        if action == "set_hvac_temperature":
            return self._apply_hvac_setpoint(exchange, state, control_plan)

        # Unknown action – do nothing but report
        return {
            "status": "skipped",
            "actuator": "eplus_actuator_v1",
            "reason": f"unknown_action:{action}",
        }

    def _apply_hvac_setpoint(self, exchange, state, control_plan: dict) -> dict:
        """Write cooling and heating setpoints derived from control_plan."""
        requested = float(control_plan.get("setpoint", self._last_cooling_setpoint))

        # Clamp to hard limits
        cooling_sp = max(_COOLING_SETPOINT_MIN, min(_COOLING_SETPOINT_MAX, requested))
        # Heating setpoint is kept 2 °C below cooling setpoint to avoid conflict
        heating_sp = max(
            _HEATING_SETPOINT_MIN,
            min(_HEATING_SETPOINT_MAX, cooling_sp - 2.0),
        )

        written: dict[str, float] = {}
        errors: list[str] = []

        cooling_handle = self._handles.get("cooling_setpoint", -1)
        if cooling_handle != -1:
            exchange.set_actuator_value(state, cooling_handle, cooling_sp)
            self._last_cooling_setpoint = cooling_sp
            written["cooling_setpoint"] = cooling_sp
        else:
            errors.append("cooling_setpoint_handle_missing")

        heating_handle = self._handles.get("heating_setpoint", -1)
        if heating_handle != -1:
            exchange.set_actuator_value(state, heating_handle, heating_sp)
            self._last_heating_setpoint = heating_sp
            written["heating_setpoint"] = heating_sp
        else:
            errors.append("heating_setpoint_handle_missing")

        status = "executed" if not errors else "partial"
        result: dict = {
            "status": status,
            "actuator": "eplus_actuator_v1",
            "action": "set_hvac_temperature",
            "written": written,
        }
        if errors:
            result["errors"] = errors
        return result

    def apply_appliances(
        self,
        exchange,
        state,
        ev_fraction: float,
        ewh_setpoint_c: float,
        ewh_availability: float,
    ) -> None:
        """Write EV and EWH actuator values to EnergyPlus.

        Called every timestep by EplusEnv for background automation.
        Does nothing silently if handles are not ready.
        """
        if not self._initialized:
            return

        ev_handle = self._handles.get("ev_fraction", -1)
        if ev_handle != -1:
            exchange.set_actuator_value(state, ev_handle, float(ev_fraction))

        ewh_sp_handle = self._handles.get("ewh_setpoint", -1)
        if ewh_sp_handle != -1:
            exchange.set_actuator_value(state, ewh_sp_handle, float(ewh_setpoint_c))

        ewh_av_handle = self._handles.get("ewh_availability", -1)
        if ewh_av_handle != -1:
            exchange.set_actuator_value(state, ewh_av_handle, float(ewh_availability))

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    @property
    def last_cooling_setpoint(self) -> float:
        return self._last_cooling_setpoint

    @property
    def last_heating_setpoint(self) -> float:
        return self._last_heating_setpoint
