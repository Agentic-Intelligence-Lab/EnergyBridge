"""Catalog of EnergyPlus output variables and actuators used by EnergyBridge.

All EnergyPlus variable/actuator names are centralised here so that
state_reader.py and actuator_writer.py never contain raw strings.

Variable tuples: (variable_name, key_value)
Actuator tuples: (component_type, control_type, actuator_key)
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Output variables to request and read
# ---------------------------------------------------------------------------

# (variable_name, key_value)
VARIABLES: dict[str, tuple[str, str]] = {
    # Zone thermal state
    "indoor_temp": ("Zone Mean Air Temperature", "living_unit1"),
    # Outdoor temperature reported at zone level (Zone-type variable, key = zone name)
    # "Zone Outdoor Air Drybulb Temperature" is more reliably accessible via the
    # Python API than the site-level "Site Outdoor Air Drybulb Temperature".
    "outdoor_temp": ("Zone Outdoor Air Drybulb Temperature", "living_unit1"),
    # HVAC cooling power – Coil:Cooling:DX:SingleSpeed object in the IDF
    "cooling_rate_w": ("Cooling Coil Total Cooling Rate", "DX Cooling Coil_unit1"),
    # Whole-building electricity demand: key is "Whole Building" per EnergyPlus convention
    "facility_power_w": ("Facility Total Electricity Demand Rate", "Whole Building"),
}

# ---------------------------------------------------------------------------
# Actuators to obtain handles for and write
# ---------------------------------------------------------------------------

# (component_type, control_type, actuator_key)
ACTUATORS: dict[str, tuple[str, str, str]] = {
    # HVAC thermostat setpoints – both schedules are Schedule:Compact in the IDF
    "cooling_setpoint": ("Schedule:Compact", "Schedule Value", "cooling_sch"),
    "heating_setpoint": ("Schedule:Compact", "Schedule Value", "heating_sch"),
}
